"""Live MTProto backend for the Telegram feature (Phase B).

Isolated here so all Telethon + asyncio code stays out of the interface module;
imported lazily (only when credentials are configured), so the app runs fine
without Telethon installed. Read-only: history, search, and photo download.

Telethon is async and binds a client to one event loop, so we run a single loop
on a daemon thread and marshal every call onto it. Downloaded photos are held in
a bounded in-memory LRU and never written to disk (Telegram stays the store).
"""
import asyncio
import threading
import time
from collections import OrderedDict

_MEDIA_CACHE_BYTES = 64 * 1024 * 1024  # photos kept in RAM, oldest evicted past this
_SEARCH_PAGE = 100        # messages fetched per Telegram request while scanning
_SEARCH_SCAN_CAP = 1000   # stop scanning after this many messages (a no-match cap)


class _AsyncLoop:
    """One asyncio loop on a daemon thread; run(coro) blocks for its result."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="tg-loop").start()

    def run(self, coro, timeout=30):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)


_LOOP = None


def _loop():
    global _LOOP
    if _LOOP is None:
        _LOOP = _AsyncLoop()
    return _LOOP


class _Lru:
    """Byte-bounded LRU. Values are (mime, data) tuples; size is len(data)."""

    def __init__(self, max_bytes):
        self._max = max_bytes
        self._d = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self._d:
                return None
            self._d.move_to_end(key)
            return self._d[key][0]

    def put(self, key, value, size):
        with self._lock:
            if key in self._d:
                self._bytes -= self._d[key][1]
            self._d[key] = (value, size)
            self._d.move_to_end(key)
            self._bytes += size
            while self._bytes > self._max and len(self._d) > 1:
                _, (_, sz) = self._d.popitem(last=False)
                self._bytes -= sz


def _norm(channel_id):
    """Admin channel id -> a reference Telethon.get_entity accepts: a numeric id
    becomes an int; @username / t.me links pass through as-is."""
    cid = str(channel_id).strip()
    return int(cid) if cid.lstrip("-").isdigit() else cid


def _to_dict(m):
    """Telethon Message -> our message dict (photos flagged; bytes fetched later)."""
    date = int(m.date.timestamp()) if getattr(m, "date", None) else 0
    return {
        "id": m.id,
        "date": date,
        "text": getattr(m, "message", None) or "",
        "sender": None,
        "media": {"type": "photo"} if getattr(m, "photo", None) else None,
    }


def _client(api_id, api_hash, session):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    # A recognizable device name so the account owner can spot (and revoke) this
    # session in Telegram -> Settings -> Devices.
    return TelegramClient(
        StringSession(session), int(api_id), str(api_hash),
        device_model="Control Center", app_version="cc", system_version="HA add-on",
    )


class TelethonSource:
    """Read-only live source backed by a logged-in user session."""

    mode = "live"

    def __init__(self, api_id, api_hash, session):
        self._loop = _loop()
        self._media = _Lru(_MEDIA_CACHE_BYTES)
        self._latest = {}  # channel_id -> (newest_id, fetched_at), short-TTL cache
        self._client = None
        self._authed = False
        self._error = None
        try:
            # Build AND connect on the loop thread: Telethon's constructor calls
            # asyncio.get_event_loop(), which raises off a running loop (there's no
            # current loop in a Flask worker thread on Python 3.12+).
            async def _setup():
                client = _client(api_id, api_hash, session)
                if not client.is_connected():
                    await client.connect()
                return client, await client.is_user_authorized()
            self._client, authed = self._loop.run(_setup(), timeout=30)
            self._authed = bool(authed)
            if not self._authed:
                self._error = "session is not authorized - sign in again"
        except Exception as exc:  # noqa: BLE001 - surface, never raise on construct
            self._error = str(exc)

    def available(self):
        return self._authed

    def status(self):
        return {"available": self._authed, "mode": self.mode,
                "detail": self._error or "Connected to Telegram."}

    async def _messages(self, channel_id, before_id, limit, search=None):
        ent = await self._client.get_entity(_norm(channel_id))
        kw = {"limit": limit}
        if before_id:
            kw["offset_id"] = int(before_id)
        if search:
            kw["search"] = search
        return [_to_dict(m) for m in await self._client.get_messages(ent, **kw)]

    def history(self, channel_id, before_id=None, limit=30):
        out = self._loop.run(self._messages(channel_id, before_id, limit), timeout=45)
        out.reverse()  # Telethon yields newest-first; our contract is ascending
        return out

    def search(self, channel_id, query, before_id=None, limit=30):
        # Telegram's own search matches whole words only. To match partial words
        # (like the mock did), scan message text for the query as a case-insensitive
        # substring, paging back through history until we have `limit` hits or hit
        # the scan cap. before_id continues an older page (for "load older").
        q = (query or "").lower()
        if not q:
            return []

        async def _f():
            ent = await self._client.get_entity(_norm(channel_id))
            matches, scanned = [], 0
            offset = int(before_id) if before_id else 0
            while len(matches) < limit and scanned < _SEARCH_SCAN_CAP:
                batch = await self._client.get_messages(ent, limit=_SEARCH_PAGE, offset_id=offset)
                if not batch:
                    break
                for m in batch:
                    scanned += 1
                    offset = m.id  # advance to older messages
                    if q in (getattr(m, "message", None) or "").lower():
                        matches.append(_to_dict(m))
                        if len(matches) >= limit:
                            break
                if len(batch) < _SEARCH_PAGE:
                    break  # reached the oldest message
            return matches

        out = self._loop.run(_f(), timeout=90)
        out.reverse()  # ascending (oldest first), matching the history contract
        return out

    def media(self, channel_id, message_id):
        key = f"{channel_id}:{message_id}"
        hit = self._media.get(key)
        if hit is not None:
            return hit

        async def _dl():
            ent = await self._client.get_entity(_norm(channel_id))
            msg = await self._client.get_messages(ent, ids=int(message_id))
            if not msg or not getattr(msg, "media", None):
                return None
            return await self._client.download_media(msg, file=bytes)

        data = self._loop.run(_dl(), timeout=60)
        if not data:
            return None
        result = ("image/jpeg", data)
        self._media.put(key, result, len(data))
        return result

    def latest_id(self, channel_id):
        """Newest message id in a channel, cached ~30s and shared across users so
        the unread poll never makes a call per user per channel."""
        now = time.time()
        hit = self._latest.get(channel_id)
        if hit and now - hit[1] < 30:
            return hit[0]

        async def _f():
            ent = await self._client.get_entity(_norm(channel_id))
            msgs = await self._client.get_messages(ent, limit=1)
            return msgs[0].id if msgs else None

        try:
            val = self._loop.run(_f(), timeout=30)
        except Exception:  # noqa: BLE001 - keep the stale value on a hiccup
            return hit[0] if hit else None
        self._latest[channel_id] = (val, now)
        return val

    def close(self):
        try:
            self._loop.run(self._client.disconnect(), timeout=10)
        except Exception:  # noqa: BLE001
            pass


class _LoginManager:
    """In-app login state machine: start(phone) -> submit_code(code) -> optional
    submit_password(2fa). Holds the half-finished client between HTTP requests
    until a session string is produced, then discards it."""

    def __init__(self):
        self._loop = _loop()
        self._client = None
        self._phone = None
        self._hash = None

    def start(self, api_id, api_hash, phone):
        self._teardown()
        self._phone = phone

        async def _f():
            client = _client(api_id, api_hash, "")   # built on the loop thread
            await client.connect()
            res = await client.send_code_request(phone)
            return client, res.phone_code_hash

        self._client, self._hash = self._loop.run(_f(), timeout=30)
        return {"stage": "code"}

    def submit_code(self, code):
        from telethon.errors import SessionPasswordNeededError

        async def _f():
            try:
                await self._client.sign_in(self._phone, code, phone_code_hash=self._hash)
                return ("done", self._client.session.save())
            except SessionPasswordNeededError:
                return ("password", None)

        stage, session = self._loop.run(_f(), timeout=30)
        if stage == "password":
            return {"stage": "password"}
        self._teardown()
        return {"stage": "done", "session": session}

    def submit_password(self, password):
        async def _f():
            await self._client.sign_in(password=password)
            return self._client.session.save()

        session = self._loop.run(_f(), timeout=30)
        self._teardown()
        return {"stage": "done", "session": session}

    def _teardown(self):
        if self._client is not None:
            try:
                self._loop.run(self._client.disconnect(), timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self._client = None
        self._phone = None
        self._hash = None


_LOGIN = None


def login_manager():
    global _LOGIN
    if _LOGIN is None:
        _LOGIN = _LoginManager()
    return _LOGIN
