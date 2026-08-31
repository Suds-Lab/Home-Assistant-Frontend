"""Persistence for the Telegram feature. Two JSON files under the same /data dir
as the rest of the app (via store.ICON_DIR), kept here rather than in the shared
store.py so the feature stays self-contained and removable.

  telegram_config.json -> {"creds": {api_id, api_hash, session}, "channels": [{id, name}]}
  telegram_perms.json  -> {username: [channel_id, ...]}  ("*" = all configured channels)

Credentials are only USED by the live (Phase B) backend; Phase A stores them but
the mock backend ignores them. They are never echoed back to the UI (write-only).
"""
import json

from store import ICON_DIR

TELEGRAM_CONFIG_FILE = ICON_DIR / "telegram_config.json"
TELEGRAM_PERMS_FILE = ICON_DIR / "telegram_perms.json"

# Sentinel in a user's perms list meaning "every configured channel, present and
# future" - mirrors the scheduling ALL_CLIMATE wildcard.
ALL_CHANNELS = "*"


def _load(path, default):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def load_config():
    cfg = _load(TELEGRAM_CONFIG_FILE, {})
    cfg.setdefault("creds", {})
    cfg.setdefault("channels", [])
    return cfg


def save_config(cfg):
    _save(TELEGRAM_CONFIG_FILE, cfg)


def load_perms():
    return _load(TELEGRAM_PERMS_FILE, {})


def save_perms(perms):
    _save(TELEGRAM_PERMS_FILE, perms)


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
