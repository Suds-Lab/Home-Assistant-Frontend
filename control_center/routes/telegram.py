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
    return jsonify({"ok": True})


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
