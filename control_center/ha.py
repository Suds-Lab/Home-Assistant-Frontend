"""Home Assistant client.

Talks to HA over REST (ha_request / call_service) and WebSocket (registry reads
and writes), with a short-TTL cache for the registries. A pure client: no
dependency on the app's auth/store/access layers. The realtime state-broadcast
loop that pushes changes to connected browsers stays in core, since it depends
on access control and the SSE subscriber registry.

All public functions accept an optional `instance_id` keyword argument. Passing
None (the default) targets the main HA instance; passing a string targets the
matching remote instance from REMOTE_INSTANCES.
"""
import json
import os
import threading
import time

import requests
import websocket  # websocket-client

from config import HA_TOKEN, HA_URL, REMOTE_INSTANCES
from errors import ApiError

# Map instance_id -> (url, token). None = main instance.
_INSTANCES = {None: (HA_URL, HA_TOKEN)}
for _r in REMOTE_INSTANCES:
    _INSTANCES[_r["id"]] = (_r["url"], _r["token"])


def _inst_url(instance_id):
    return _INSTANCES.get(instance_id, _INSTANCES[None])[0]


def _inst_token(instance_id):
    return _INSTANCES.get(instance_id, _INSTANCES[None])[1]


_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def ha_request(path, method="GET", payload=None, *, instance_id=None):
    url = _inst_url(instance_id)
    token = _inst_token(instance_id)
    _label = f" (instance: {instance_id})" if instance_id else ""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if instance_id:
        headers["User-Agent"] = _BROWSER_UA
    try:
        res = requests.request(
            method,
            f"{url}{path}",
            headers=headers,
            json=payload,
            timeout=15,
        )
    except requests.RequestException as err:
        print(f"HA connection error{_label} for {method} {path}: {err}")
        raise ApiError("Couldn't reach Home Assistant.", 502)
    if not res.ok:
        print(f"HA request failed ({res.status_code}){_label} for {method} {path}: {res.text[:500]}")
        raise ApiError("Couldn't reach Home Assistant. Check the add-on logs for details.", 502)
    return res.json() if res.text else None


def call_service(domain, service, entity_id, extra=None, *, instance_id=None):
    if os.environ.get("MOCK_HA"):
        print(f"[mock_ha] call_service {domain}.{service} {entity_id} {extra}")
        return {}
    body = {"entity_id": entity_id, **(extra or {})}
    return ha_request(f"/api/services/{domain}/{service}", "POST", body, instance_id=instance_id)


def _ws_url(instance_id=None):
    base = _inst_url(instance_id).replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/api/websocket"


def _ws_token(instance_id=None):
    return _inst_token(instance_id)


def ha_registries(instance_id=None):
    """One-shot WebSocket query for HA's floor/area/entity/device registries, so
    the picker can group devices by floor and room. Returns {} on any failure."""
    token = _ws_token(instance_id)
    _headers = {
        "User-Agent": _BROWSER_UA,
        "Origin": _inst_url(instance_id),
    } if instance_id else {}
    try:
        ws = websocket.create_connection(_ws_url(instance_id), timeout=10, header=_headers)
    except Exception:  # noqa: BLE001
        return {}
    try:
        json.loads(ws.recv())  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            return {}
        cmds = {
            1: "config/floor_registry/list",
            2: "config/area_registry/list",
            3: "config/entity_registry/list",
            4: "config/device_registry/list",
            5: "manifest/list",
        }
        for i, t in cmds.items():
            ws.send(json.dumps({"id": i, "type": t}))
        got = {}
        while len(got) < len(cmds):
            msg = json.loads(ws.recv())
            if msg.get("type") == "result" and msg.get("id") in cmds:
                got[msg["id"]] = msg.get("result") or []
        return {
            "floors": got[1], "areas": got[2], "entities": got[3], "devices": got[4],
            "integrations": {m.get("domain"): m.get("name")
                             for m in (got.get(5) or []) if m.get("domain") and m.get("name")},
        }
    except Exception:  # noqa: BLE001
        return {}
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def ha_ws_command(payload, instance_id=None):
    """Send one WebSocket command to HA and return its result, or raise ApiError.
    Used for registry writes (e.g. moving a device to an area)."""
    token = _ws_token(instance_id)
    _headers = {
        "User-Agent": _BROWSER_UA,
        "Origin": _inst_url(instance_id),
    } if instance_id else {}
    try:
        ws = websocket.create_connection(_ws_url(instance_id), timeout=10, header=_headers)
    except Exception:  # noqa: BLE001
        raise ApiError("Could not reach Home Assistant", 502)
    try:
        json.loads(ws.recv())  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            raise ApiError("Home Assistant rejected the connection", 502)
        msg = dict(payload)
        msg["id"] = 1
        ws.send(json.dumps(msg))
        while True:
            res = json.loads(ws.recv())
            if res.get("type") == "result" and res.get("id") == 1:
                if not res.get("success"):
                    raise ApiError(
                        (res.get("error") or {}).get("message")
                        or "Home Assistant rejected the change",
                        502,
                    )
                return res.get("result")
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


# Per-instance registry cache: instance_id -> {"ts": float, "data": dict}
_REG_CACHE = {}
_REG_LOCK = threading.Lock()


def cache_registries_from_ws(instance_id, floors, areas, entities, devices, integrations):
    """Store registry data that was fetched over an existing WebSocket connection
    (e.g. from _ws_loop) so ha_registries_cached() can serve it without opening
    a separate WebSocket to the remote HA."""
    data = {
        "floors": floors,
        "areas": areas,
        "entities": entities,
        "devices": devices,
        "integrations": {
            m.get("domain"): m.get("name")
            for m in (integrations or [])
            if m.get("domain") and m.get("name")
        },
    }
    with _REG_LOCK:
        _REG_CACHE[instance_id] = {"ts": time.time(), "data": data}


def _invalidate_registries(instance_id=None):
    with _REG_LOCK:
        if instance_id is not None:
            if instance_id in _REG_CACHE:
                _REG_CACHE[instance_id]["ts"] = 0.0
        else:
            for v in _REG_CACHE.values():
                v["ts"] = 0.0


def ha_registries_cached(ttl=300, instance_id=None):
    """ha_registries() with a short TTL cache - the registries rarely change."""
    now = time.time()
    with _REG_LOCK:
        c = _REG_CACHE.get(instance_id)
        if c is not None and c["data"] is not None and (now - c["ts"]) < ttl:
            return c["data"]
    reg = ha_registries(instance_id=instance_id)
    if reg:
        with _REG_LOCK:
            _REG_CACHE[instance_id] = {"ts": now, "data": reg}
    return reg


def _location_lookup(reg):
    """Build an entity_id -> (room, floor, icon) resolver from HA's registries."""
    floors = {f["floor_id"]: f.get("name") for f in reg.get("floors", [])}
    areas = {a["area_id"]: a for a in reg.get("areas", [])}
    dev_area = {d["id"]: d.get("area_id") for d in reg.get("devices", [])}
    ent_area = {}
    for e in reg.get("entities", []):
        aid = e.get("area_id") or dev_area.get(e.get("device_id"))
        if aid:
            ent_area[e["entity_id"]] = aid

    def locate(entity_id):
        aid = ent_area.get(entity_id)
        area = areas.get(aid) if aid else None
        if not area:
            return (None, None, None)
        return (area.get("name"), floors.get(area.get("floor_id")), area.get("icon"))

    return locate
