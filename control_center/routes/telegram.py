"""Telegram Notifications API (part of the self-contained telegram feature).

User endpoints (any authenticated user, user port):
  GET /api/telegram/status                        - available? + this user's channels
  GET /api/telegram/messages?channel=&before=     - history for a permitted channel
  GET /api/telegram/search?channel=&q=&before=    - search within a permitted channel

Admin endpoints (management/Ingress port only):
  GET/POST /api/admin/telegram-config             - creds (write-only) + channel list
  GET/POST /api/admin/telegram-perms              - per-user channel visibility
"""
from flask import Blueprint, Response, jsonify, request

from errors import ApiError
from security import current_user, require_admin
from telegram_feed import store as tstore
from telegram_feed.backend import get_source

bp = Blueprint("telegram", __name__)

_LIMIT = 30


def _allowed(user):
    return set(tstore.resolve_allowed(user["username"]))


def _before_arg():
    raw = request.args.get("before")
    return int(raw) if raw and raw.isdigit() else None


def _require_channel(user):
    channel = (request.args.get("channel") or "").strip()
    if not channel or channel not in _allowed(user):
        raise ApiError("No access to that channel", 403)
    return channel


@bp.get("/api/telegram/status")
def telegram_status():
    user = current_user()
    src = get_source()
    st = src.status()
    allowed = _allowed(user)
    chans = [c for c in tstore.channels() if c["id"] in allowed]
    return jsonify({
        "available": bool(st.get("available")) and len(chans) > 0,
        "mode": st.get("mode"),
        "detail": st.get("detail"),
        "channels": chans,
    })


@bp.get("/api/telegram/messages")
def telegram_messages():
    user = current_user()
    channel = _require_channel(user)
    msgs = get_source().history(channel, before_id=_before_arg(), limit=_LIMIT)
    return jsonify({"messages": msgs})


@bp.get("/api/telegram/search")
def telegram_search():
    user = current_user()
    channel = _require_channel(user)
    q = (request.args.get("q") or "").strip()
    msgs = get_source().search(channel, q, before_id=_before_arg(), limit=_LIMIT) if q else []
    return jsonify({"messages": msgs})


@bp.get("/api/telegram/unread")
def telegram_unread():
    """Per-user unread flag for each of this user's channels. A channel is unread
    when its newest message id is past what this user has already seen. First
    exposure initializes the mark to the current newest (so no day-one flood)."""
    user = current_user()
    src = get_source()
    allowed = _allowed(user)
    out = []
    for c in tstore.channels():
        if c["id"] not in allowed:
            continue
        latest = src.latest_id(c["id"])
        seen = tstore.get_last_seen(user["username"], c["id"])
        if latest is None:
            unread = False
        elif seen is None:
            tstore.set_last_seen(user["username"], c["id"], latest)
            unread = False
        else:
            unread = latest > seen
        out.append({"id": c["id"], "name": c["name"], "unread": unread})
    return jsonify({"channels": out, "any": any(x["unread"] for x in out)})


@bp.post("/api/telegram/read")
def telegram_read():
    """Mark a channel read up to `last_id` (or its current newest) for this user."""
    user = current_user()
    data = request.get_json(force=True) or {}
    channel = (data.get("channel") or "").strip()
    if not channel or channel not in _allowed(user):
        raise ApiError("No access to that channel", 403)
    last_id = data.get("last_id")
    if last_id is None:
        last_id = get_source().latest_id(channel)
    if last_id is not None:
        tstore.set_last_seen(user["username"], channel, int(last_id))
    return jsonify({"ok": True})


@bp.get("/api/telegram/media")
def telegram_media():
    """Image bytes for one message (permission-checked). The frontend fetches this
    with its auth header and turns it into a blob URL, so no token ever lands in an
    <img> URL."""
    user = current_user()
    channel = _require_channel(user)
    raw = request.args.get("id")
    if not raw or not raw.isdigit():
        raise ApiError("bad message id", 400)
    got = get_source().media(channel, int(raw))
    if not got:
        raise ApiError("no media", 404)
    mime, data = got
    resp = Response(data, mimetype=mime)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@bp.get("/api/admin/telegram-config")
def admin_get_config():
    require_admin()
    creds = tstore.load_config().get("creds") or {}
    # Secrets are write-only: report only whether each is set, never the value.
    return jsonify({
        "channels": tstore.channels(),
        "creds_set": {k: bool(creds.get(k)) for k in ("api_id", "api_hash", "session")},
    })


@bp.post("/api/admin/telegram-config")
def admin_set_config():
    require_admin()
    data = request.get_json(force=True) or {}
    cfg = tstore.load_config()
    if "channels" in data:
        chans, seen = [], set()
        for c in data.get("channels") or []:
            cid = str((c or {}).get("id") or "").strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            chans.append({"id": cid, "name": str((c or {}).get("name") or "").strip() or cid})
        cfg["channels"] = chans
    if "creds" in data:
        # Only overwrite a field when a non-empty value is sent, so a blank field
        # in the UI leaves the stored secret untouched.
        creds = cfg.get("creds") or {}
        for k in ("api_id", "api_hash", "session"):
            v = (data.get("creds") or {}).get(k)
            if v:
                creds[k] = str(v)
        cfg["creds"] = creds
    tstore.save_config(cfg)
    from telegram_feed.backend import reset_source
    reset_source()  # pick up new creds/channels on the next request
    return jsonify({"ok": True})


@bp.get("/api/admin/telegram-status")
def admin_status():
    require_admin()
    return jsonify(get_source().status())


# --- In-app login (mints a session from phone + code, an alternative to pasting
#     a session string generated offline) ------------------------------------

def _store_session(session):
    cfg = tstore.load_config()
    creds = cfg.get("creds") or {}
    creds["session"] = session
    cfg["creds"] = creds
    tstore.save_config(cfg)


@bp.post("/api/admin/telegram-login/start")
def tg_login_start():
    require_admin()
    data = request.get_json(force=True) or {}
    cfg = tstore.load_config()
    creds = cfg.get("creds") or {}
    api_id = data.get("api_id") or creds.get("api_id")
    api_hash = data.get("api_hash") or creds.get("api_hash")
    phone = (data.get("phone") or "").strip()
    if not api_id or not api_hash or not phone:
        raise ApiError("api_id, api_hash and phone are required", 400)
    # Remember the api creds so the user doesn't retype them and the built source
    # can use them later.
    creds["api_id"] = str(api_id)
    creds["api_hash"] = str(api_hash)
    cfg["creds"] = creds
    tstore.save_config(cfg)
    try:
        from telegram_feed.backend import get_login_manager
        return jsonify(get_login_manager().start(api_id, api_hash, phone))
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"could not start login: {exc}", 502)


@bp.post("/api/admin/telegram-login/code")
def tg_login_code():
    require_admin()
    code = str((request.get_json(force=True) or {}).get("code") or "").strip()
    if not code:
        raise ApiError("code required", 400)
    from telegram_feed.backend import get_login_manager, reset_source
    try:
        res = get_login_manager().submit_code(code)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"sign-in failed: {exc}", 502)
    if res.get("stage") == "done":
        _store_session(res["session"])
        reset_source()
        return jsonify({"stage": "done"})
    return jsonify(res)  # {"stage": "password"}


@bp.post("/api/admin/telegram-login/password")
def tg_login_password():
    require_admin()
    pw = str((request.get_json(force=True) or {}).get("password") or "")
    if not pw:
        raise ApiError("password required", 400)
    from telegram_feed.backend import get_login_manager, reset_source
    try:
        res = get_login_manager().submit_password(pw)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"two-step password failed: {exc}", 502)
    _store_session(res["session"])
    reset_source()
    return jsonify({"stage": "done"})


@bp.get("/api/admin/telegram-perms")
def admin_get_perms():
    require_admin()
    return jsonify(tstore.load_perms())


@bp.post("/api/admin/telegram-perms")
def admin_set_perms():
    require_admin()
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    if not username:
        raise ApiError("username required", 400)
    ids = data.get("channel_ids") or []
    perms = tstore.load_perms()
    if data.get("all") or tstore.ALL_CHANNELS in ids:
        perms[username] = [tstore.ALL_CHANNELS]
    elif ids:
        perms[username] = [str(x) for x in ids]
    else:
        perms.pop(username, None)
    tstore.save_perms(perms)
    return jsonify({"ok": True})
