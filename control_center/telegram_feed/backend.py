"""Read-only message sources for the Telegram feature.

The Flask routes call a tiny sync interface - history() and search() - so the
backend can be swapped without touching the routes or the frontend:

  - MockSource: deterministic fake messages, active under MOCK_HA, so the whole
    view+search UX is buildable and verifiable in dev with no real credentials.
  - NullSource: reports "not configured" (production, until the Phase B live
    MTProto/Telethon adapter lands here).

get_source() picks one. Messages are dicts: {id:int, date:int(unix secs),
text:str, sender:str|None, media:dict|None}, returned ascending by id (oldest
first) in chunks of at most `limit`; pass before_id to page backwards. A message
with media carries {"type": "photo"}; its bytes are fetched separately via
media(channel_id, message_id) -> (mimetype, bytes) | None so the image can be
streamed on demand (Phase B: Telethon download_media; here: a placeholder).
"""
import hashlib
import os
import time

_LIMIT = 30


class NullSource:
    mode = "off"

    def __init__(self, detail=None):
        self._detail = detail or "Telegram live backend is not configured yet."

    def available(self):
        return False

    def status(self):
        return {"available": False, "mode": self.mode, "detail": self._detail}

    def history(self, channel_id, before_id=None, limit=_LIMIT):
        return []

    def search(self, channel_id, query, before_id=None, limit=_LIMIT):
        return []

    def media(self, channel_id, message_id):
        return None


class MockSource:
    """A fixed, deterministic feed per channel so the UI has something real to
    page and search through in dev. Ids are 1.._COUNT, one message per minute."""
    mode = "mock"
    _COUNT = 120
    _SAMPLES = [
        "backup completed ok", "sensor battery low", "front door opened",
        "living room 21.4C", "automation 'night' ran", "cpu load 3%",
        "disk 42% used", "guest wifi connected", "motion in hallway",
        "update available", "heartbeat", "camera snapshot saved",
        "garage closed", "water leak sensor ok", "reboot finished",
    ]

    def available(self):
        return True

    def status(self):
        return {"available": True, "mode": self.mode,
                "detail": "Showing mock data (dev mode)."}

    def _all(self, channel_id):
        base = int(hashlib.sha256(channel_id.encode()).hexdigest(), 16)
        now = int(time.time())
        out = []
        for i in range(1, self._COUNT + 1):
            # Every 5th message is a "photo" (with the text as its caption), so
            # the viewer's image handling is exercised alongside plain messages.
            is_photo = i % 5 == 0
            text = self._SAMPLES[(base + i) % len(self._SAMPLES)]
            out.append({
                "id": i,
                "date": now - (self._COUNT - i) * 60,
                "text": (f"snapshot: {text} (#{i})" if is_photo else f"{text} (#{i})"),
                "sender": None,
                "media": {"type": "photo"} if is_photo else None,
            })
        return out

    def _page(self, msgs, before_id, limit):
        if before_id is not None:
            msgs = [m for m in msgs if m["id"] < before_id]
        return msgs[-limit:]

    def history(self, channel_id, before_id=None, limit=_LIMIT):
        return self._page(self._all(channel_id), before_id, limit)

    def search(self, channel_id, query, before_id=None, limit=_LIMIT):
        q = (query or "").lower()
        hits = [m for m in self._all(channel_id) if q in m["text"].lower()]
        return self._page(hits, before_id, limit)

    def media(self, channel_id, message_id):
        """A deterministic placeholder 'photo' for image messages (Phase B streams
        the real downloaded bytes instead). Returns (mimetype, bytes) or None."""
        msg = next((m for m in self._all(channel_id) if m["id"] == message_id), None)
        if not msg or not msg.get("media"):
            return None
        seed = int(hashlib.sha256(f"{channel_id}:{message_id}".encode()).hexdigest(), 16)
        hue = seed % 360
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300">'
            f'<rect width="100%" height="100%" fill="hsl({hue},55%,45%)"/>'
            f'<rect x="0" y="0" width="100%" height="100%" fill="hsl({(hue + 40) % 360},55%,35%)" opacity="0.35"/>'
            '<text x="50%" y="50%" fill="#fff" font-family="sans-serif" font-size="30" '
            f'text-anchor="middle" dominant-baseline="middle">photo #{message_id}</text>'
            '</svg>'
        )
        return ("image/svg+xml", svg.encode("utf-8"))


_SOURCE = None
_SIG = None


def _creds_sig(creds):
    return (creds.get("api_id"), creds.get("api_hash"), bool(creds.get("session")))


def get_source():
    """The active message source. MOCK_HA -> mock feed. Otherwise, when the admin
    has set api_id/api_hash/session, the live Telethon source (built lazily, so a
    missing Telethon or a bad session degrades to a NullSource that explains why);
    with nothing configured, a NullSource. Rebuilt when the credentials change."""
    global _SOURCE, _SIG
    if os.environ.get("MOCK_HA"):
        if not isinstance(_SOURCE, MockSource):
            _SOURCE = MockSource()
        return _SOURCE

    from telegram_feed import store as tstore
    creds = tstore.load_config().get("creds") or {}
    sig = _creds_sig(creds)
    if sig != _SIG or _SOURCE is None:
        _teardown()
        _SIG = sig
        if creds.get("api_id") and creds.get("api_hash") and creds.get("session"):
            try:
                from telegram_feed.live import TelethonSource
                _SOURCE = TelethonSource(creds["api_id"], creds["api_hash"], creds["session"])
            except Exception as exc:  # noqa: BLE001 - never break the app on a bad backend
                _SOURCE = NullSource(f"Telegram backend unavailable: {exc}")
        else:
            _SOURCE = NullSource()
    return _SOURCE


def _teardown():
    global _SOURCE
    try:
        if _SOURCE is not None and hasattr(_SOURCE, "close"):
            _SOURCE.close()
    except Exception:  # noqa: BLE001
        pass
    _SOURCE = None


def reset_source():
    """Force get_source() to rebuild on next call (call after credentials change)."""
    global _SIG
    _teardown()
    _SIG = None


def get_login_manager():
    """The in-app login state machine (lazy; requires Telethon)."""
    from telegram_feed.live import login_manager
    return login_manager()
