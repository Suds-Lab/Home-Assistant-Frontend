"""My Home - a small Home Assistant companion app (Flask backend).

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
import threading
import time
from pathlib import Path
from queue import Empty, Queue

# Ensure PWA assets are served with the right Content-Type.
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

import jwt
import requests
import websocket  # websocket-client
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_sock import Sock

try:
    # Load .env for standalone/dev runs. Optional - absent in the add-on.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# --- Configuration -------------------------------------------------------

# Home Assistant writes add-on settings here and injects SUPERVISOR_TOKEN.
OPTIONS_FILE = Path(os.environ.get("OPTIONS_FILE", "/data/options.json"))
addon_options = {}
if OPTIONS_FILE.exists():
    try:
        addon_options = json.loads(OPTIONS_FILE.read_text())
    except (ValueError, OSError) as err:
        print(f"Could not read add-on options at {OPTIONS_FILE}: {err}")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")

# As an add-on, reach HA through the Supervisor proxy with the injected token.
# Standalone (dev), fall back to HA_URL / HA_TOKEN from the environment.
HA_URL = (
    os.environ.get("HA_URL")
    or ("http://supervisor/core" if SUPERVISOR_TOKEN else "")
).rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN") or SUPERVISOR_TOKEN
JWT_SECRET = (
    addon_options.get("jwt_secret")
    or os.environ.get("JWT_SECRET")
    or "dev-secret-change-me"
)
# Display name shown in the UI / browser tab / installed PWA. Configurable.
APP_NAME = addon_options.get("app_name") or os.environ.get("APP_NAME") or "My Home"

# The app listens on TWO ports:
#  - INGRESS_PORT: the management UI, reached only through HA's Ingress (the
#    sidebar tab). This port is never published, so a request arriving on it is
#    trusted as an authenticated HA admin (same model as the Terminal add-on).
#  - USER_PORT: the household-facing device dashboard, published to the network
#    and protected by the app's own per-user login.
INGRESS_PORT = int(os.environ.get("INGRESS_PORT") or os.environ.get("PORT") or 4000)
USER_PORT = int(os.environ.get("USER_PORT") or 8099)

# Real-time push (SSE) is on by default. Set STREAM=0 to make the dashboard
# poll instead - useful for local preview tools that wait for network-idle
# (an always-open stream never idles).
STREAM_ENABLED = os.environ.get("STREAM", "1") != "0"

if not HA_URL or not HA_TOKEN:
    raise SystemExit(
        "Missing Home Assistant connection. As an add-on this is automatic; "
        "standalone, set HA_URL and HA_TOKEN in your environment (.env)."
    )


def _store_path():
    """Where accounts are read/written.

    In the add-on this lives under /data (persisted across restarts and
    updates); standalone it's users.json next to this file.
    """
    if os.environ.get("USERS_STORE"):
        return Path(os.environ["USERS_STORE"])
    if SUPERVISOR_TOKEN and Path("/data").is_dir():
        return Path("/data/users.json")
    return BASE_DIR / "users.json"


STORE_FILE = _store_path()


def _seed_users():
    """Initial accounts used the first time the store is created."""
    users = addon_options.get("users")
    if isinstance(users, list) and users:
        seed = [dict(u) for u in users]
    else:
        bundled = BASE_DIR / "users.json"
        seed = json.loads(bundled.read_text())["users"] if bundled.exists() else []
    return seed


def save_users(users):
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_name(STORE_FILE.name + ".tmp")
    tmp.write_text(json.dumps({"users": users}, indent=2))
    tmp.replace(STORE_FILE)


def load_users():
    """User -> entities mapping (read fresh each call, so edits are live)."""
    if not STORE_FILE.exists():
        save_users(_seed_users())
    return json.loads(STORE_FILE.read_text())["users"]


# --- Home Assistant helpers ----------------------------------------------


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
        raise ApiError(f"Could not reach Home Assistant: {err}", 502)
    if not res.ok:
        raise ApiError(f"HA request failed ({res.status_code}): {res.text}", 502)
    # Some service calls return an empty body.
    return res.json() if res.text else None


def call_service(domain, service, entity_id, extra=None):
    body = {"entity_id": entity_id, **(extra or {})}
    return ha_request(f"/api/services/{domain}/{service}", "POST", body)


# --- App + auth ----------------------------------------------------------

app = Flask(__name__, static_folder=None)
sock = Sock(app)


class ApiError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify(error=err.message), err.status


def user_from_token(token):
    """Resolve a session token to a user, or raise ApiError."""
    if not token:
        raise ApiError("Not authenticated", 401)
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ApiError("Session expired", 401)
    user = next(
        (u for u in load_users() if u["username"] == payload.get("username")), None
    )
    if not user:
        raise ApiError("Unknown user", 401)
    return user


def current_user():
    """Validate the Bearer session token and return the matching user."""
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else None
    return user_from_token(token)


def is_management():
    """True when the request arrived on the (unpublished) Ingress port - i.e.
    through HA's sidebar, where the user is already an authenticated HA admin."""
    return str(request.environ.get("SERVER_PORT")) == str(INGRESS_PORT)


def require_admin():
    """Admin endpoints are served ONLY on the management (Ingress) port. A
    request there is trusted as the HA admin, so no app login is required."""
    if not is_management():
        raise ApiError("Admin access required", 403)
    return {"username": None, "admin": True}


def assert_owned(user, entity_id):
    """Reject any entity the logged-in user is not allowed to see/control."""
    if entity_id not in user.get("entities", []):
        raise ApiError("You do not have access to that device", 403)


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    username = body.get("username")
    password = body.get("password")
    user = next((u for u in load_users() if u["username"] == username), None)
    if not user or user.get("password") != password:
        raise ApiError("Invalid username or password", 401)
    token = jwt.encode(
        {"username": user["username"], "exp": int(time.time()) + 7 * 24 * 3600},
        JWT_SECRET,
        algorithm="HS256",
    )
    return jsonify(
        token=token,
        displayName=user.get("displayName") or user["username"],
    )


@app.get("/api/session")
def session():
    """Tells the UI which experience to render based on the port it arrived on:
    'manage' (Ingress/sidebar) or 'user' (published dashboard)."""
    return jsonify(
        mode="manage" if is_management() else "user",
        stream=STREAM_ENABLED,
        appName=APP_NAME,
    )


# --- Device routes -------------------------------------------------------


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


@app.get("/api/devices")
def devices():
    """Every entity assigned to the user, with full state + attributes."""
    user = current_user()
    owned = set(user.get("entities", []))
    result = [
        _device_view(s)
        for s in ha_request("/api/states")
        if s["entity_id"] in owned
    ]
    result.sort(key=lambda d: (d["domain"], d["name"].lower()))
    return jsonify(devices=result)


@app.get("/api/entity/<path:entity_id>")
def entity_detail(entity_id):
    """Fresh full state for a single owned entity (for the detail panel)."""
    user = current_user()
    assert_owned(user, entity_id)
    return jsonify(_device_view(ha_request(f"/api/states/{entity_id}")))


# Services the app may call, per domain. Calls are always scoped to an entity
# the user owns and to that entity's own domain - never an arbitrary HA service.
ALLOWED_SERVICES = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "input_boolean": {"turn_on", "turn_off", "toggle"},
    "fan": {"turn_on", "turn_off", "toggle", "set_percentage", "oscillate"},
    "climate": {"turn_on", "turn_off", "set_hvac_mode", "set_temperature", "set_fan_mode"},
    "cover": {
        "open_cover", "close_cover", "stop_cover", "toggle", "set_cover_position"
    },
    "lock": {"lock", "unlock", "open"},
    "media_player": {
        "turn_on", "turn_off", "media_play", "media_pause", "media_stop",
        "media_play_pause", "media_next_track", "media_previous_track",
        "volume_set", "volume_up", "volume_down", "volume_mute",
    },
    "scene": {"turn_on"},
    "script": {"turn_on", "turn_off", "toggle"},
    "automation": {"turn_on", "turn_off", "toggle", "trigger"},
    "button": {"press"},
    "input_button": {"press"},
    "vacuum": {"start", "pause", "stop", "return_to_base", "locate"},
}


@app.post("/api/control")
def control():
    """Call a whitelisted service on an entity the user owns."""
    user = current_user()
    body = request.get_json(silent=True) or {}
    entity_id = body.get("entity_id")
    service = body.get("service")
    data = body.get("data") or {}
    if not isinstance(entity_id, str) or not isinstance(service, str):
        raise ApiError("entity_id and service are required", 400)
    if not isinstance(data, dict):
        raise ApiError("data must be an object", 400)
    assert_owned(user, entity_id)
    domain = entity_id.split(".")[0]
    if service not in ALLOWED_SERVICES.get(domain, set()):
        raise ApiError(f"Service '{service}' is not allowed for {domain} entities", 400)
    call_service(domain, service, entity_id, data)
    return jsonify(ok=True)


# --- Real-time updates (HA WebSocket -> cache -> SSE) ---------------------

# A single background WebSocket to Home Assistant keeps this live cache of
# entity states; connected browsers get pushed only the entities they own.
STATE_CACHE = {}  # entity_id -> raw HA state dict
SUBSCRIBERS = []  # each item: {"q": Queue, "owned": set(entity_id)}
_SUB_LOCK = threading.Lock()
_CACHE_READY = threading.Event()


def _ws_url():
    base = HA_URL.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/api/websocket"


def _broadcast(entity_id, new_state):
    view = _device_view(new_state) if new_state else None
    payload = json.dumps({"entity_id": entity_id, "state": view})
    with _SUB_LOCK:
        for sub in list(SUBSCRIBERS):
            if entity_id in sub["owned"]:
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
    owned = set(user.get("entities", []))
    sub = {"q": Queue(), "owned": owned}

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
    sub = {"q": Queue(), "owned": set(user.get("entities", []))}
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
        }
        for i, t in cmds.items():
            ws.send(json.dumps({"id": i, "type": t}))
        got = {}
        while len(got) < len(cmds):
            msg = json.loads(ws.recv())
            if msg.get("type") == "result" and msg.get("id") in cmds:
                got[msg["id"]] = msg.get("result") or []
        return {"floors": got[1], "areas": got[2], "entities": got[3], "devices": got[4]}
    except Exception:  # noqa: BLE001
        return {}
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


@app.get("/api/admin/entities")
def admin_entities():
    """All entities (any domain), for the device picker - annotated with their
    floor and room (area) when Home Assistant knows them."""
    require_admin()
    reg = ha_registries()
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
            return (None, None)
        return (area.get("name"), floors.get(area.get("floor_id")))

    items = []
    for s in ha_request("/api/states"):
        eid = s["entity_id"]
        area, floor = locate(eid)
        items.append({
            "entity_id": eid,
            "name": s.get("attributes", {}).get("friendly_name") or eid,
            "domain": eid.split(".")[0],
            "area": area,
            "floor": floor,
        })
    items.sort(key=lambda i: (i["domain"], i["name"].lower()))
    return jsonify(entities=items)


@app.get("/api/admin/users")
def admin_list_users():
    require_admin()
    safe = [
        {
            "username": u["username"],
            "displayName": u.get("displayName", ""),
            "entities": u.get("entities", []),
        }
        for u in load_users()
    ]
    return jsonify(users=safe)


@app.post("/api/admin/users")
def admin_save_user():
    """Create or update a user. Username is the key; blank password keeps the
    existing one (and is required when creating)."""
    require_admin()
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    if not username:
        raise ApiError("Username is required", 400)

    users = load_users()
    # Editing an existing user (possibly renaming): `original` is the old name.
    original = (body.get("original") or "").strip()
    existing = next((u for u in users if u["username"] == original), None) if original else None
    if existing is None:
        existing = next((u for u in users if u["username"] == username), None)

    # Renaming to a name another account already uses is not allowed.
    if username != (existing["username"] if existing else None):
        if any(u["username"] == username for u in users):
            raise ApiError(f"The username '{username}' is already taken", 400)

    password = body.get("password")
    if existing is None and not password:
        raise ApiError("A password is required for a new user", 400)

    record = existing if existing is not None else {"username": username}
    record["username"] = username  # apply rename
    record["displayName"] = body.get("displayName") or username
    record["entities"] = [e for e in body.get("entities", []) if isinstance(e, str)]
    if password:
        record["password"] = password
    if existing is None:
        users.append(record)

    save_users(users)
    return jsonify(ok=True)


@app.delete("/api/admin/users/<username>")
def admin_delete_user(username):
    require_admin()
    users = load_users()
    remaining = [u for u in users if u["username"] != username]
    if len(remaining) == len(users):
        raise ApiError("No such user", 404)
    save_users(remaining)
    return jsonify(ok=True)


# --- Static client (served by Flask; SPA fallback) -----------------------


@app.get("/manifest.webmanifest")
def manifest():
    """Serve the PWA manifest with the configured app name (so the installed
    home-screen app uses it too)."""
    data = json.loads((STATIC_DIR / "manifest.webmanifest").read_text())
    data["name"] = APP_NAME
    data["short_name"] = APP_NAME
    return Response(json.dumps(data), mimetype="application/manifest+json")


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    target = STATIC_DIR / filename
    if target.is_file():
        return send_from_directory(STATIC_DIR, filename)
    # SPA fallback: anything that isn't a real file returns index.html.
    return send_from_directory(STATIC_DIR, "index.html")


# Start the HA WebSocket listener as soon as the module is imported, so it runs
# under gunicorn (the add-on) as well as the dev server below.
ensure_realtime()


if __name__ == "__main__":
    # Dev: serve both ports so each mode is reachable. In the add-on, gunicorn
    # binds both (see Dockerfile). threaded=True so SSE streams don't block.
    from werkzeug.serving import make_server

    user_srv = make_server("0.0.0.0", USER_PORT, app, threaded=True)
    threading.Thread(target=user_srv.serve_forever, daemon=True).start()
    print(f"My Home -> HA at {HA_URL}")
    print(f"  management (Ingress): http://0.0.0.0:{INGRESS_PORT}")
    print(f"  user dashboard:       http://0.0.0.0:{USER_PORT}")
    make_server("0.0.0.0", INGRESS_PORT, app, threaded=True).serve_forever()
