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
import threading
import time
from queue import Empty, Queue

# Ensure PWA assets are served with the right Content-Type.
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

import websocket  # websocket-client
from flask import Flask, Response, jsonify, request
from flask_sock import Sock

from config import HA_TOKEN
from errors import ApiError
from ha import _invalidate_registries, _ws_url, ha_request
from access import user_can_access
from security import user_from_token



# --- App -----------------------------------------------------------------

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB cap on uploads
sock = Sock(app)


@app.errorhandler(ApiError)
def handle_api_error(err):
    payload = {"error": err.message}
    payload.update(err.extra)
    return jsonify(payload), err.status


# --- Device view helper (also used by _broadcast below) ------------------


def _device_view(s):
    attrs = s.get("attributes", {})
    return {
        "entity_id": s["entity_id"],
        "domain": s["entity_id"].split(".")[0],
        "name": attrs.get("friendly_name") or s["entity_id"],
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


def _ws_loop():
    """Maintain the HA WebSocket connection, refilling the cache and fanning
    state_changed events out to subscribers. Reconnects forever on failure."""
    while True:
        try:
            ws = websocket.create_connection(_ws_url(), timeout=30)
            json.loads(ws.recv())  # auth_required
            ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
            if json.loads(ws.recv()).get("type") != "auth_ok":
                print("HA WebSocket auth failed")
                ws.close()
                time.sleep(15)
                continue

            # Seed the cache with a full snapshot, then subscribe to changes.
            try:
                for s in ha_request("/api/states"):
                    STATE_CACHE[s["entity_id"]] = s
            except Exception as err:  # noqa: BLE001
                print("State snapshot failed:", err)
            ws.send(json.dumps(
                {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
            ))
            ws.recv()  # subscribe ack
            _CACHE_READY.set()
            print("HA WebSocket connected; streaming state changes")

            while True:
                msg = json.loads(ws.recv())
                if msg.get("type") != "event":
                    continue
                data = msg["event"]["data"]
                eid = data["entity_id"]
                new = data.get("new_state")
                if new is None:
                    STATE_CACHE.pop(eid, None)
                else:
                    STATE_CACHE[eid] = new
                # A brand-new entity (no prior state) may belong to a device/area
                # we haven't cached - refresh the registry so its room/floor is
                # right on the next fetch.
                if data.get("old_state") is None and new is not None:
                    _invalidate_registries()
                _broadcast(eid, new)
        except Exception as err:  # noqa: BLE001
            _CACHE_READY.clear()
            print("HA WebSocket error, reconnecting in 5s:", err)
            time.sleep(5)


_ws_started = False


def ensure_realtime():
    """Start the HA WebSocket thread once per process."""
    global _ws_started
    if not _ws_started:
        _ws_started = True
        threading.Thread(target=_ws_loop, daemon=True).start()


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


# App-level middleware (security headers) and the dev-server entry point live in
# app.py, the thin composition/entry module that imports this one.


# --- Route blueprints (split out of this module by feature area) ----------
from routes.admin import bp as _admin_bp  # noqa: E402
from routes.auth import bp as _auth_bp  # noqa: E402
from routes.devices import bp as _devices_bp  # noqa: E402
from routes.manager import bp as _manager_bp  # noqa: E402
from routes.pwa import bp as _pwa_bp  # noqa: E402
app.register_blueprint(_admin_bp)
app.register_blueprint(_auth_bp)
app.register_blueprint(_devices_bp)
app.register_blueprint(_manager_bp)
app.register_blueprint(_pwa_bp)
