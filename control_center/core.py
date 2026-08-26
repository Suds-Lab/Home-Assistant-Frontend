"""Control Center - a small Home Assistant companion app (Flask backend).

Shows each logged-in person only their own lights and AC and lets them control
them, talking to Home Assistant through this backend so the HA token never
reaches the browser.

Runs two ways:
  * Standalone (dev): reads HA_URL / HA_TOKEN from the environment (.env).
  * Home Assistant add-on: reads settings from /data/options.json and reaches
    HA through the Supervisor proxy using the auto-injected SUPERVISOR_TOKEN,
    so no long-lived token is needed.
"""

import json
import mimetypes
import os
import re
import threading
import time
from queue import Empty, Queue

# Ensure PWA assets are served with the right Content-Type.
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

import websocket  # websocket-client
from flask import Flask, Response, jsonify, request
from flask_sock import Sock

from config import HA_TOKEN, REMOTE_INSTANCES
from errors import ApiError
from ha import _inst_url, _invalidate_registries, _ws_url, _ws_token, cache_registries_from_ws
from access import user_can_access
from security import user_from_token



# --- App -----------------------------------------------------------------

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB cap on uploads
sock = Sock(app)


@app.errorhandler(ApiError)
def handle_api_error(err):
    # Log every API error so failures are visible in the add-on log. gunicorn runs
    # with no access log, and this handler previously returned JSON silently, so a
    # rejected request (e.g. a validation 400) left no trace - which made a broken
    # remote command impossible to diagnose from the log.
    print(f"[api-error {err.status}] {request.method} {request.path}: {err.message}")
    payload = {"error": err.message}
    payload.update(err.extra)
    return jsonify(payload), err.status


# --- Device view helper (also used by _broadcast below) ------------------


def _device_view(s):
    attrs = s.get("attributes", {})
    full_id = s["entity_id"]
    # Strip instance prefix (e.g. "garage:light.bedroom") to get the real HA entity id
    # so domain extraction is always correct regardless of namespacing.
    real_id = full_id.split(":", 1)[1] if ":" in full_id else full_id
    return {
        "entity_id": full_id,
        "domain": real_id.split(".")[0],
        "name": attrs.get("friendly_name") or real_id,
        "state": s.get("state"),
        "attributes": attrs,
        "last_changed": s.get("last_changed"),
        "last_updated": s.get("last_updated"),
    }


# --- Real-time updates (HA WebSocket -> cache -> SSE) ---------------------

# A single background WebSocket to Home Assistant keeps this live cache of
# entity states; connected browsers get pushed only the entities they own.
STATE_CACHE = {}  # entity_id -> raw HA state dict
SUBSCRIBERS = []  # each item: {"q": Queue, "owned": set(entity_id)}
_SUB_LOCK = threading.Lock()
_CACHE_READY = threading.Event()




def _broadcast(entity_id, new_state):
    view = _device_view(new_state) if new_state else None
    payload = json.dumps({"entity_id": entity_id, "state": view})
    with _SUB_LOCK:
        subs = list(SUBSCRIBERS)
    for sub in subs:
        # Push to anyone who can see this entity - which for "All devices" users
        # and managers is everything they're allowed, not a fixed list. (Using
        # a static owned-set missed them, since they own devices via the flag,
        # not an explicit entity list.)
        if user_can_access(sub["user"], entity_id):
            sub["q"].put(payload)


# ANSI colors for the add-on log (Supervisor's log viewer renders them).
_C_RESET, _C_RED, _C_AMBER, _C_GREEN, _C_DIM = "\x1b[0m", "\x1b[31m", "\x1b[33m", "\x1b[32m", "\x1b[2m"

# Per-instance reconnect-failure state, so an offline remote doesn't flood the log.
_ws_fail = {}  # label -> {"count": int, "sig": str|None, "last_log": float}


def _log_ws_failure(label, err):
    """Log a WebSocket failure without flooding. The raw exception can be a wall of
    text (Cloudflare's whole header dump), so we boil it down to a status + error
    code and log it on the first failure, then at most every 5 minutes until it
    changes or the link recovers."""
    s = str(err)
    status = (s.split(" -+-+- ", 1)[0] if " -+-+- " in s else s).strip()
    if len(status) > 120:
        status = status[:117] + "..."
    m = re.search(r"error code:\s*(\d+)", s)
    cf = f" (Cloudflare {m.group(1)})" if m else ""
    low = s.lower()
    bot = "403" in status and "cloudflare" in low
    down = ("530" in status) or (m and m.group(1) in ("1033", "1016"))
    sig = status + cf
    st = _ws_fail.setdefault(label, {"count": 0, "sig": None, "last_log": 0.0})
    st["count"] += 1
    now = time.time()
    if st["sig"] == sig and now - st["last_log"] <= 300:
        return  # same failure, logged recently - stay quiet
    st["sig"] = sig
    st["last_log"] = now
    more = f" (x{st['count']})" if st["count"] > 1 else ""
    if bot:
        print(f"{_C_AMBER}HA WebSocket blocked by Cloudflare ({label}): the remote URL is behind "
              f"bot protection. Use the direct local IP/port, or add a WAF bypass for "
              f"/api/websocket. Retrying every 5s{more}.{_C_RESET}", flush=True)
    elif down:
        print(f"{_C_AMBER}HA WebSocket: remote '{label}' is unreachable{cf} - its origin is down or "
              f"Cloudflare can't reach it. Retrying every 5s{more}; silenced until it changes or "
              f"recovers.{_C_RESET}", flush=True)
    else:
        print(f"{_C_RED}HA WebSocket error ({label}): {status}{cf}. Retrying every 5s{more}; "
              f"silenced until it changes or recovers.{_C_RESET}", flush=True)


def _ws_loop(instance_id=None):
    """Maintain one HA WebSocket connection (main or a remote instance), refilling
    the state cache and fanning state_changed events to subscribers. Reconnects
    forever on failure. Remote entity IDs are namespaced as `{instance_id}:{eid}`
    so they never collide with main-instance entities."""
    label = instance_id or "main"
    # Headers that make the connection look enough like a browser to pass
    # Cloudflare's basic bot checks. Without these, CF returns a 403 challenge.
    _ws_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Origin": _inst_url(instance_id) if instance_id else "",
    }
    while True:
        try:
            ws = websocket.create_connection(
                _ws_url(instance_id), timeout=30, header=_ws_headers
            )
            json.loads(ws.recv())  # auth_required
            ws.send(json.dumps({"type": "auth", "access_token": _ws_token(instance_id)}))
            if json.loads(ws.recv()).get("type") != "auth_ok":
                print(f"HA WebSocket auth failed ({label})")
                ws.close()
                time.sleep(15)
                continue

            # Phase 1: seed state cache immediately - don't block on registries.
            ws.send(json.dumps({"id": 1, "type": "get_states"}))
            ws.send(json.dumps({"id": 2, "type": "subscribe_events", "event_type": "state_changed"}))
            got = {}
            while len(got) < 2:
                msg = json.loads(ws.recv())
                if msg.get("type") == "result" and msg.get("id") in (1, 2):
                    got[msg["id"]] = msg
            states_result = got.get(1, {})
            for s in (states_result.get("result") or []):
                fid = f"{instance_id}:{s['entity_id']}" if instance_id else s["entity_id"]
                s = dict(s)
                s["entity_id"] = fid
                if instance_id:
                    s["_instance"] = instance_id
                STATE_CACHE[fid] = s
            if not states_result.get("success"):
                print(f"State snapshot via WebSocket failed ({label}):", states_result.get("error"))

            # Phase 2: request registries over the same connection so area/floor
            # lookups are served from cache without a separate Cloudflare handshake.
            # Results arrive async and are collected in the event loop below.
            _REG_IDS = {3: "config/floor_registry/list", 4: "config/area_registry/list",
                        5: "config/entity_registry/list", 6: "config/device_registry/list",
                        7: "manifest/list"}
            for mid, t in _REG_IDS.items():
                ws.send(json.dumps({"id": mid, "type": t}))
            _reg_buf = {}
            if instance_id is None:
                _CACHE_READY.set()
            prev = _ws_fail.pop(label, {}).get("count", 0)
            if prev:
                print(f"{_C_GREEN}HA WebSocket recovered ({label}) after {prev} failed attempt(s); "
                      f"streaming state changes{_C_RESET}", flush=True)
            else:
                print(f"HA WebSocket connected ({label}); streaming state changes", flush=True)

            # Keepalive: the socket timeout also governs recv() in this loop, so
            # an idle instance (no state_changed for the timeout window) would
            # otherwise trip a recv() timeout and force a needless reconnect - a
            # reconnect storm for quiet remotes. Instead, when the socket goes
            # quiet we send an HA-level ping and only reconnect if the pong never
            # comes back (i.e. the connection is genuinely dead).
            ping_pending = False
            ping_id = 100
            while True:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    if ping_pending:
                        raise  # our ping went unanswered - the link is dead
                    ping_id += 1
                    ws.send(json.dumps({"id": ping_id, "type": "ping"}))
                    ping_pending = True
                    continue
                ping_pending = False  # any inbound traffic proves the link is alive
                msg = json.loads(raw)
                if msg.get("type") == "pong":
                    continue
                # Collect pending registry results (arrive after phase-2 requests).
                mid = msg.get("id")
                if mid in _REG_IDS and msg.get("type") == "result" and mid not in _reg_buf:
                    _reg_buf[mid] = msg.get("result") or []
                    if len(_reg_buf) == len(_REG_IDS):
                        cache_registries_from_ws(
                            instance_id,
                            floors=_reg_buf.get(3, []),
                            areas=_reg_buf.get(4, []),
                            entities=_reg_buf.get(5, []),
                            devices=_reg_buf.get(6, []),
                            integrations=_reg_buf.get(7, []),
                        )
                        print(f"Registry data cached for remote instance '{label}'")
                if msg.get("type") != "event":
                    continue
                data = msg["event"]["data"]
                eid = data["entity_id"]
                fid = f"{instance_id}:{eid}" if instance_id else eid
                new = data.get("new_state")
                if new is None:
                    STATE_CACHE.pop(fid, None)
                else:
                    new = dict(new)
                    new["entity_id"] = fid
                    if instance_id:
                        new["_instance"] = instance_id
                    STATE_CACHE[fid] = new
                # Diagnostics: log thermostat mode / setpoint changes (skip the
                # noisy current-temperature-only updates) so a schedule's effect,
                # and any bounce-back, is visible next to the [sched] log lines.
                if eid.startswith("climate."):
                    old = data.get("old_state") or {}
                    o_s, n_s = old.get("state"), (new.get("state") if new else None)
                    o_t = (old.get("attributes") or {}).get("temperature")
                    n_t = (new.get("attributes") or {}).get("temperature") if new else None
                    if o_s != n_s or o_t != n_t:
                        o_str = f"{o_s}" + (f" {o_t}°" if o_t is not None else "")
                        n_str = f"{n_s}" + (f" {n_t}°" if n_t is not None else "")
                        # If a real HA user made this change (and it wasn't our own
                        # command echoing back), tell the scheduler so it stops
                        # enforcing the schedule on this device (the person wins).
                        uid = (new.get("context") or {}).get("user_id") if new else None
                        by = ""
                        if uid:
                            import scheduler
                            if not scheduler.was_recently_commanded(fid):
                                scheduler.note_user_change(fid, who=uid)
                                by = " (by user)"
                        print(f"{_C_DIM}[device {time.strftime('%Y-%m-%d %H:%M:%S')}] {fid}: "
                              f"{o_str} -> {n_str}{by}{_C_RESET}", flush=True)
                if data.get("old_state") is None and new is not None:
                    _invalidate_registries(instance_id)
                _broadcast(fid, new)
        except Exception as err:  # noqa: BLE001
            if instance_id is None:
                _CACHE_READY.clear()
            _log_ws_failure(label, err)
            time.sleep(5)


_ws_started = False


def ensure_realtime():
    """Start one WebSocket thread per HA instance (main + all remotes)."""
    global _ws_started
    if not _ws_started:
        _ws_started = True
        threading.Thread(target=_ws_loop, daemon=True).start()
        for inst in REMOTE_INSTANCES:
            print(f"Launching WebSocket thread for remote instance '{inst['id']}' at {inst['url']}")
            threading.Thread(target=_ws_loop, args=(inst["id"],), daemon=True).start()


@app.get("/api/stream")
def stream():
    """Server-Sent Events: an initial snapshot of the user's devices, then a
    live update whenever one changes. EventSource can't set headers, so the
    session token comes as a query param."""
    user = user_from_token(request.args.get("token"))
    sub = {"q": Queue(), "user": user}

    def events():
        with _SUB_LOCK:
            SUBSCRIBERS.append(sub)
        try:
            # Flush headers immediately so the client's onopen fires and it can
            # (re)sync the full list via REST. We only stream incremental
            # updates here, so a down WebSocket can never blank the dashboard.
            yield ": connected\n\n"
            while True:
                try:
                    payload = sub["q"].get(timeout=20)
                    yield f"event: update\ndata: {payload}\n\n"
                except Empty:
                    yield ": keepalive\n\n"  # comment line keeps the socket warm
        finally:
            with _SUB_LOCK:
                SUBSCRIBERS[:] = [s for s in SUBSCRIBERS if s is not sub]

    return Response(
        events(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@sock.route("/api/ws")
def ws_stream(ws):
    """Live updates over a WebSocket (proxy-friendly: traverses reverse proxies
    and Cloudflare better than SSE). Pushes the user's owned-entity changes; the
    client re-syncs the full list over REST when it (re)connects."""
    try:
        user = user_from_token(request.args.get("token"))
    except ApiError:
        ws.close()
        return
    sub = {"q": Queue(), "user": user}
    with _SUB_LOCK:
        SUBSCRIBERS.append(sub)
    try:
        while True:
            try:
                ws.send(sub["q"].get(timeout=25))
            except Empty:
                ws.send('{"type":"ping"}')  # keepalive + detect a closed socket
    except Exception:  # noqa: BLE001  (client went away)
        pass
    finally:
        with _SUB_LOCK:
            SUBSCRIBERS[:] = [s for s in SUBSCRIBERS if s is not sub]


# --- Admin: manage users (admin-only) ------------------------------------






# --- Static client (served by Flask; SPA fallback) -----------------------




# Start the HA WebSocket listener as soon as the module is imported, so it runs
# under gunicorn (the add-on) as well as the dev server below.
ensure_realtime()

if os.environ.get("MOCK_HA"):
    _MOCK_CLIMATE = [
        ("climate.living_room_ac", "Living Room AC",  "cool",     22.0, 20.0),
        ("climate.bedroom_ac",     "Bedroom AC",       "heat",     21.0, 19.5),
        ("climate.office_ac",      "Office AC",        "off",      24.0, 23.0),
        ("climate.guest_room_ac",  "Guest Room AC",    "fan_only", 23.0, 22.5),
    ]
    _MOCK_FAN_MODES   = ["auto", "low", "medium", "high"]
    _MOCK_HVAC_MODES  = ["off", "cool", "heat", "heat_cool", "dry", "fan_only"]
    for _eid, _name, _state, _target, _current in _MOCK_CLIMATE:
        STATE_CACHE[_eid] = {
            "entity_id": _eid,
            "state": _state,
            "attributes": {
                "friendly_name": _name,
                "temperature": _target,
                "current_temperature": _current,
                "hvac_modes": _MOCK_HVAC_MODES,
                "fan_modes": _MOCK_FAN_MODES,
                "min_temp": 16.0,
                "max_temp": 30.0,
            },
        }
    print(f"[mock_ha] Seeded {len(_MOCK_CLIMATE)} fake climate entities into STATE_CACHE")

# App-level middleware (security headers) and the dev-server entry point live in
# app.py, the thin composition/entry module that imports this one.


# --- Route blueprints (split out of this module by feature area) ----------
from routes.admin import bp as _admin_bp  # noqa: E402
from routes.auth import bp as _auth_bp  # noqa: E402
from routes.devices import bp as _devices_bp  # noqa: E402
from routes.lists import bp as _lists_bp  # noqa: E402
from routes.manager import bp as _manager_bp  # noqa: E402
from routes.pwa import bp as _pwa_bp  # noqa: E402
from routes.schedules import bp as _schedules_bp  # noqa: E402
app.register_blueprint(_admin_bp)
app.register_blueprint(_auth_bp)
app.register_blueprint(_devices_bp)
app.register_blueprint(_lists_bp)
app.register_blueprint(_manager_bp)
app.register_blueprint(_pwa_bp)
app.register_blueprint(_schedules_bp)
from scheduler import ensure_scheduler  # noqa: E402
ensure_scheduler()
