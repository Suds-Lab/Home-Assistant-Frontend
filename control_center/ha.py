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


def split_instance_entity(entity_id):
    """Split a dashboard entity id into (instance_id, real_entity_id).

    Remote entities are namespaced as `{instance_id}:{entity_id}` (e.g.
    `garage:light.bedroom`); main-instance entities are plain. Returns
    (None, entity_id) for the main instance."""
    if ":" in entity_id:
        iid, eid = entity_id.split(":", 1)
        return iid, eid
    return None, entity_id


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


_HA_TEMP_UNIT = None


def ha_temperature_unit():
    """Home Assistant's configured temperature unit ('°C' or '°F'), cached.

    Schedules are always set and displayed in this unit; HA itself converts the
    setpoint to each thermostat's own native unit when the command is sent, so a
    mixed fleet needs no conversion on our side. Falls back to '°C' and doesn't
    cache a failed lookup, so it retries next call."""
    global _HA_TEMP_UNIT
    if _HA_TEMP_UNIT:
        return _HA_TEMP_UNIT
    if os.environ.get("MOCK_HA"):
        _HA_TEMP_UNIT = os.environ.get("MOCK_TEMP_UNIT", "°C")
        return _HA_TEMP_UNIT
    try:
        cfg = ha_request("/api/config") or {}
        unit = (cfg.get("unit_system") or {}).get("temperature")
    except Exception:  # noqa: BLE001
        return "°C"
    _HA_TEMP_UNIT = unit if unit in ("°C", "°F") else "°C"
    return _HA_TEMP_UNIT


def call_service(domain, service, entity_id, extra=None, *, instance_id=None):
    if os.environ.get("MOCK_HA"):
        print(f"[mock_ha] call_service {domain}.{service} {entity_id} {extra}")
        return {}
    # Remote instances: issue the command over the WebSocket (like reads and
    # registry writes), not REST. The WebSocket path sends the Origin header that
    # gets past Cloudflare bot protection; a REST POST would be 403'd while the
    # read stream keeps working, so CC changes never reached the remote HA.
    if instance_id is not None:
        return ha_ws_command(
            {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "service_data": dict(extra or {}),
                "target": {"entity_id": entity_id},
            },
            instance_id=instance_id,
        )
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
    Used for device control on remote instances and for registry writes (e.g.
    moving a device to an area). Logs what it sends and why it failed - a silent
    failure here is why a remote command could vanish with no trace in the log."""
    label = f" (instance: {instance_id})" if instance_id else ""
    what = payload.get("type", "command")
    if what == "call_service":
        what = f"{payload.get('domain')}.{payload.get('service')} on {payload.get('target', {}).get('entity_id')}"
    token = _ws_token(instance_id)
    _headers = {
        "User-Agent": _BROWSER_UA,
        "Origin": _inst_url(instance_id),
    } if instance_id else {}
    # Match the read connection's 30s timeout: a fresh handshake over a slow
    # Cloudflare tunnel can take well over 10s, and a too-short timeout silently
    # dropped remote commands while the (longer-lived) read stream kept working.
    try:
        ws = websocket.create_connection(_ws_url(instance_id), timeout=30, header=_headers)
    except Exception as err:  # noqa: BLE001
        print(f"HA WS command connect failed{label} for {what}: {err}")
        raise ApiError("Could not reach Home Assistant", 502)
    try:
        json.loads(ws.recv())  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            print(f"HA WS command auth failed{label} for {what}")
            raise ApiError("Home Assistant rejected the connection", 502)
        msg = dict(payload)
        msg["id"] = 1
        ws.send(json.dumps(msg))
        while True:
            res = json.loads(ws.recv())
            if res.get("type") == "result" and res.get("id") == 1:
                if not res.get("success"):
                    err_msg = (res.get("error") or {}).get("message") or "Home Assistant rejected the change"
                    print(f"HA WS command rejected{label} for {what}: {err_msg}")
                    raise ApiError(err_msg, 502)
                print(f"HA WS command OK{label}: {what}")
                return res.get("result")
    except ApiError:
        raise
    except Exception as err:  # noqa: BLE001
        print(f"HA WS command error{label} for {what}: {err}")
        raise ApiError("Couldn't reach Home Assistant.", 502)
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
    """ha_registries() with a short TTL cache - the registries rarely change.

    On a refresh failure (remote briefly unreachable / a Cloudflare hiccup)
    ha_registries() returns {}. Rather than propagate that empty dict - which
    makes a manageable device look unmanageable - serve the last-known cached
    data. The background _ws_loop keeps this cache warm, so once an instance has
    ever been reachable, a transient blip no longer surfaces as an empty
    registry. Only a never-seen instance returns {}."""
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
    # Refresh failed: fall back to stale data if we have any (better than {}).
    with _REG_LOCK:
        c = _REG_CACHE.get(instance_id)
        return c["data"] if c and c["data"] else {}


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
