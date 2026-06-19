"""Home Assistant client.

Talks to HA over REST (ha_request / call_service) and WebSocket (registry reads
and writes), with a short-TTL cache for the registries. A pure client: no
dependency on the app's auth/store/access layers. The realtime state-broadcast
loop that pushes changes to connected browsers stays in core, since it depends
on access control and the SSE subscriber registry.
"""
import json
import threading
import time

import requests
import websocket  # websocket-client

from config import HA_TOKEN, HA_URL
from errors import ApiError

def ha_request(path, method="GET", payload=None):
    try:
        res = requests.request(
            method,
            f"{HA_URL}{path}",
            headers={
                "Authorization": f"Bearer {HA_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
    except requests.RequestException as err:
        print(f"HA connection error for {method} {path}: {err}")
        raise ApiError("Couldn't reach Home Assistant.", 502)
    if not res.ok:
        print(f"HA request failed ({res.status_code}) for {method} {path}: {res.text[:500]}")
        raise ApiError("Couldn't reach Home Assistant. Check the add-on logs for details.", 502)
    # Some service calls return an empty body.
    return res.json() if res.text else None


def call_service(domain, service, entity_id, extra=None):
    body = {"entity_id": entity_id, **(extra or {})}
    return ha_request(f"/api/services/{domain}/{service}", "POST", body)


def _ws_url():
    base = HA_URL.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/api/websocket"


def ha_registries():
    """One-shot WebSocket query for HA's floor/area/entity/device registries, so
    the picker can group devices by floor and room. Returns {} on any failure."""
    try:
        ws = websocket.create_connection(_ws_url(), timeout=10)
    except Exception:  # noqa: BLE001
        return {}
    try:
        json.loads(ws.recv())  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            return {}
        cmds = {
            1: "config/floor_registry/list",
            2: "config/area_registry/list",
            3: "config/entity_registry/list",
            4: "config/device_registry/list",
            5: "manifest/list",  # integration domain -> friendly name (incl. custom)
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


def ha_ws_command(payload):
    """Send one WebSocket command to HA and return its result, or raise ApiError.
    Used for registry writes (e.g. moving a device to an area)."""
    try:
        ws = websocket.create_connection(_ws_url(), timeout=10)
    except Exception:  # noqa: BLE001
        raise ApiError("Could not reach Home Assistant", 502)
    try:
        json.loads(ws.recv())  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
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


_REG_CACHE = {"ts": 0.0, "data": None}
_REG_LOCK = threading.Lock()


def _invalidate_registries():
    with _REG_LOCK:
        _REG_CACHE["ts"] = 0.0


def ha_registries_cached(ttl=300):
    """ha_registries() with a short TTL cache - the registries rarely change,
    and this avoids opening a WebSocket on every dashboard refresh."""
    now = time.time()
    with _REG_LOCK:
        cached = _REG_CACHE["data"]
        if cached is not None and (now - _REG_CACHE["ts"]) < ttl:
            return cached
    reg = ha_registries()
    if reg:  # only cache a successful query
        with _REG_LOCK:
            _REG_CACHE["data"] = reg
            _REG_CACHE["ts"] = now
    return reg


def _location_lookup(reg):
    """Build an entity_id -> (room, floor) resolver from HA's registries."""
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
