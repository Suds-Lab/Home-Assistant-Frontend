"""Persistence for the Telegram feature, backed by SQLite (see db.py) since
v2.10.0. Kept here rather than in the shared store.py so the feature stays
self-contained and removable. Shapes are unchanged from the JSON era:

  config -> {"creds": {api_id, api_hash, session}, "channels": [{id, name}]}
  perms  -> {username: [channel_id, ...]}  ("*" = all configured channels)
  read   -> {username: {channel_id: last_seen_message_id}}

Credentials are only USED by the live backend and are never echoed back to the
UI (write-only). Read state is per Control-Center-user and never touches
Telegram's own read state.
"""
import json

import db

# Sentinel in a user's perms list meaning "every configured channel, present and
# future" - mirrors the scheduling ALL_CLIMATE wildcard.
ALL_CHANNELS = "*"


def load_config():
    conn = db.get()
    creds = {}
    for r in conn.execute("SELECT key, value FROM telegram_creds"):
        try:
            creds[r["key"]] = json.loads(r["value"]) if r["value"] is not None else None
        except (ValueError, TypeError):
            creds[r["key"]] = r["value"]
    chans = [{"id": r["id"], "name": r["name"]}
             for r in conn.execute("SELECT id, name FROM telegram_channels ORDER BY ordinal")]
    return {"creds": creds, "channels": chans}


def save_config(cfg):
    creds = (cfg or {}).get("creds") or {}
    chans = (cfg or {}).get("channels") or []
    with db.transaction() as conn:
        conn.execute("DELETE FROM telegram_creds")
        conn.execute("DELETE FROM telegram_channels")
        for k, v in creds.items():
            conn.execute("INSERT INTO telegram_creds(key, value) VALUES(?, ?)",
                         (k, json.dumps(v)))
        for ordinal, c in enumerate(chans):
            if not isinstance(c, dict):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO telegram_channels(id, name, ordinal) VALUES(?, ?, ?)",
                (c.get("id"), c.get("name"), ordinal))


def load_perms():
    perms = {}
    for r in db.get().execute("SELECT username, channel_id FROM telegram_perms ORDER BY rowid"):
        perms.setdefault(r["username"], []).append(r["channel_id"])
    return perms


def save_perms(perms):
    with db.transaction() as conn:
        conn.execute("DELETE FROM telegram_perms")
        for username, ids in (perms or {}).items():
            for cid in (ids or []):
                conn.execute(
                    "INSERT OR IGNORE INTO telegram_perms(username, channel_id) VALUES(?, ?)",
                    (username, cid))


def channels():
    """Admin-defined channels as [{"id","name"}], skipping blanks/dupes."""
    out, seen = [], set()
    for c in load_config().get("channels", []):
        cid = str((c or {}).get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "name": str((c or {}).get("name") or "").strip() or cid})
    return out


def channel_ids():
    return [c["id"] for c in channels()]


def load_read():
    data = {}
    for r in db.get().execute("SELECT username, channel_id, last_seen FROM telegram_read"):
        data.setdefault(r["username"], {})[r["channel_id"]] = r["last_seen"]
    return data


def save_read(data):
    with db.transaction() as conn:
        conn.execute("DELETE FROM telegram_read")
        for username, per_user in (data or {}).items():
            for cid, seen in (per_user or {}).items():
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO telegram_read(username, channel_id, last_seen) VALUES(?, ?, ?)",
                        (username, cid, int(seen)))
                except (TypeError, ValueError):
                    continue


def get_last_seen(username, channel_id):
    row = db.get().execute(
        "SELECT last_seen FROM telegram_read WHERE username=? AND channel_id=?",
        (username, channel_id)).fetchone()
    return row["last_seen"] if row else None


def set_last_seen(username, channel_id, msg_id):
    """Advance the read high-water mark (never moves backward)."""
    msg_id = int(msg_id)
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT last_seen FROM telegram_read WHERE username=? AND channel_id=?",
            (username, channel_id)).fetchone()
        if row is None or msg_id > row["last_seen"]:
            conn.execute(
                "INSERT OR REPLACE INTO telegram_read(username, channel_id, last_seen) VALUES(?, ?, ?)",
                (username, channel_id, msg_id))


def resolve_allowed(username):
    """Channel ids this user may see. "*" -> all configured channels; otherwise
    the stored ids intersected with channels that still exist (so a deleted
    channel silently drops out of everyone's access)."""
    raw = load_perms().get(username, [])
    existing = channel_ids()
    if ALL_CHANNELS in raw:
        return list(existing)
    keep = {str(x) for x in raw}
    return [cid for cid in existing if cid in keep]
