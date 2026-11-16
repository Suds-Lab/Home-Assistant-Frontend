"""SQLite engine for Control Center persistence.

store.py and telegram_feed/store.py sit on top of this; nothing else touches the
database. Design notes:

* **Thread-local connections.** gunicorn runs one worker with many threads, plus
  the scheduler and HA-WebSocket daemon threads. Each thread gets its own
  connection (a shared connection would make one thread's reads land inside
  another thread's open transaction). WAL then gives every reader a consistent
  snapshot concurrent with the single writer.
* **One global write lock.** All `save_*` run their replace-the-collection work
  inside `transaction()`, which serializes writers process-wide (so no
  SQLITE_BUSY between our own threads) AND makes the read-modify-write save
  functions atomic - closing the last-writer-wins races the JSON layer had.
* **Forward migrations.** `bootstrap()` applies ordered `MIGRATIONS` steps whose
  version is above the stored `schema_version`. v1 creates the schema and imports
  any legacy JSON. Future schema changes add a `(n, fn)` step.

The one-time JSON->SQLite import is deliberately fail-safe: it writes a full
pre-migration backup, imports inside the migration, VERIFIES the DB matches the
source, and only then archives the JSON (rename, never delete). Any failure
raises so the add-on restarts and retries on intact source data rather than ever
serving partial data.
"""
import base64
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from config import SUPERVISOR_TOKEN, BASE_DIR


class MigrationError(RuntimeError):
    """Raised when the one-time import can't be proven intact. Fail loud."""


def _db_path():
    """Where the SQLite file lives - mirrors store._store_path() precedence so
    the DB sits in the same data dir as the icon/jwt files it replaces nothing of."""
    if os.environ.get("USERS_STORE"):
        return Path(os.environ["USERS_STORE"]).parent / "control_center.db"
    if SUPERVISOR_TOKEN and Path("/data").is_dir():
        return Path("/data/control_center.db")
    return BASE_DIR / "control_center.db"


DB_PATH = _db_path()
DATA_DIR = DB_PATH.parent

# --- Connections ----------------------------------------------------------
_local = threading.local()
_write_lock = threading.RLock()


def get():
    """This thread's connection, opened lazily (post-fork safe under gunicorn)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


class transaction:
    """Serialized write transaction: BEGIN IMMEDIATE ... COMMIT/ROLLBACK, guarded
    by the process-wide write lock. `with db.transaction() as conn:`"""

    def __enter__(self):
        _write_lock.acquire()
        self.conn = get()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            _write_lock.release()
        return False


# --- Schema ---------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '',
  admin INTEGER NOT NULL DEFAULT 0, manager INTEGER NOT NULL DEFAULT 0,
  all_devices INTEGER NOT NULL DEFAULT 0,
  password TEXT, email TEXT, provider TEXT, picture TEXT NOT NULL DEFAULT '',
  expires TEXT NOT NULL DEFAULT '', seq INTEGER);
CREATE TABLE IF NOT EXISTS user_entities (
  username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
  entity_id TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (username, entity_id));
CREATE TABLE IF NOT EXISTS user_entity_expires (
  username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
  entity_id TEXT NOT NULL, expires TEXT NOT NULL,
  PRIMARY KEY (username, entity_id));

CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1, type TEXT, start TEXT, end TEXT);
CREATE INDEX IF NOT EXISTS idx_schedules_owner ON schedules(owner);
CREATE TABLE IF NOT EXISTS schedule_targets (
  schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (schedule_id, entity_id));
CREATE TABLE IF NOT EXISTS schedule_entries (
  id TEXT PRIMARY KEY,
  schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL DEFAULT 0, time TEXT NOT NULL DEFAULT '',
  -- temp and fan are both polymorphic and never queried server-side, so they are
  -- stored as JSON: temp is null | a number | a per-unit list [{unit,temp}]
  -- (v2.9.52); fan is null | a legacy string | [{modes,fan}] groups.
  mode TEXT NOT NULL DEFAULT 'heat', temp TEXT, fan TEXT);
CREATE TABLE IF NOT EXISTS schedule_entry_days (
  entry_id TEXT NOT NULL REFERENCES schedule_entries(id) ON DELETE CASCADE,
  day INTEGER NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (entry_id, day));

CREATE TABLE IF NOT EXISTS schedule_perms (
  username TEXT NOT NULL, entity_id TEXT NOT NULL,
  PRIMARY KEY (username, entity_id));

CREATE TABLE IF NOT EXISTS lists (
  id TEXT PRIMARY KEY, username TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
  ordinal INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_lists_user ON lists(username, ordinal);
CREATE TABLE IF NOT EXISTS list_entities (
  list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (list_id, entity_id));

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, username TEXT, name TEXT,
  entity_id TEXT, entity TEXT, domain TEXT, service TEXT, verb TEXT,
  source TEXT, schedule TEXT);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity(username);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS telegram_creds (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS telegram_channels (
  id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', ordinal INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS telegram_perms (
  username TEXT NOT NULL, channel_id TEXT NOT NULL, PRIMARY KEY (username, channel_id));
CREATE TABLE IF NOT EXISTS telegram_read (
  username TEXT NOT NULL, channel_id TEXT NOT NULL, last_seen INTEGER NOT NULL,
  PRIMARY KEY (username, channel_id));
"""


def _meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _meta_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))


# --- Bootstrap / migrations ----------------------------------------------
_bootstrapped = False
_bootstrap_lock = threading.Lock()


def bootstrap():
    """Create/upgrade the DB and run the one-time legacy import. Idempotent;
    safe to call from every module import. Raises MigrationError if the import
    can't be proven intact (fail-safe: the add-on restarts, JSON untouched)."""
    global _bootstrapped
    if _bootstrapped:
        return
    with _bootstrap_lock:
        if _bootstrapped:
            return
        conn = get()
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        current = int(_meta_get(conn, "schema_version", "0"))
        migrated = False
        for version, step in MIGRATIONS:
            if version > current:
                step(conn)                          # import + verify; does NOT archive
                with transaction() as c:
                    _meta_set(c, "schema_version", version)
                migrated = True
        # Archive the consumed JSON ONLY after the version is stamped. A crash in the
        # window between stamp and archive just leaves the next boot to see the new
        # version and skip the migration (DB is authoritative), with the JSON as
        # harmless leftovers. Archiving BEFORE the stamp would let a crash re-run the
        # import against already-renamed (empty) source and seed defaults over real
        # data - and the verify gate would pass (empty == empty), losing it silently.
        if migrated:
            _archive_imported_json()
        _bootstrapped = True


def _migrate_to_1(conn):
    """Baseline: create the schema, then import any legacy JSON."""
    conn.executescript(_SCHEMA_SQL)
    _import_legacy_json()


MIGRATIONS = [(1, _migrate_to_1)]
SCHEMA_VERSION = MIGRATIONS[-1][0]


# --- One-time JSON -> SQLite import ---------------------------------------
# Order matters only in that users should exist before things that reference a
# username, but there are no FKs across those, so any order is fine.
def _import_legacy_json():
    import legacy_json as lj
    import store
    from telegram_feed import store as tstore

    present = [p for p in lj.all_files() if p.exists()]

    # Step 0: a full pre-migration backup, before any DB write. Required.
    if present:
        _write_premigration_backup(lj)

    # Step 1: read each store via the legacy loaders, write via the new save_*.
    # Users seed a default admin on an empty store, exactly as the old first-run.
    users = lj.read_users() or store._seed_users()
    store.save_users(users)
    store._save_settings(lj.read_settings())
    store.save_schedules(lj.read_schedules())
    store.save_schedule_perms(lj.read_schedule_perms())
    store.save_lists(lj.read_lists())
    store.replace_activity(lj.read_activity())
    tstore.save_config(lj.read_telegram_config())
    tstore.save_perms(lj.read_telegram_perms())
    tstore.save_read(lj.read_telegram_read())

    # Step 2: verification gate - the DB must match the source, or we abort.
    # Archiving the JSON is deferred to bootstrap(), AFTER schema_version is stamped,
    # so a crash can never leave an archived-but-unstamped state that re-imports
    # empty source over real data.
    _verify_import(lj, store, tstore, seeded_users=users)


def _archive_imported_json():
    """Rename the consumed legacy JSON files to *.imported (kept, never deleted).
    Called from bootstrap() only after schema_version is committed."""
    import legacy_json as lj
    for p in lj.all_files():
        if p.exists():
            try:
                p.replace(p.with_name(p.name + ".imported"))
            except OSError as exc:  # pragma: no cover - leave it; DB is authoritative
                print(f"[db] could not archive {p.name}: {exc}", flush=True)


def _write_premigration_backup(lj):
    """A full snapshot in the backup/export format, written beside the DB before
    anything is imported. Raises on failure (we don't migrate without a net)."""
    data = {
        "type": "my_home_backup", "version": 1,
        "users": lj.read_users(), "settings": lj.read_settings(),
        "schedules": lj.read_schedules(), "schedule_perms": lj.read_schedule_perms(),
        "lists": lj.read_lists(),
        "telegram": {"config": lj.read_telegram_config(),
                     "perms": lj.read_telegram_perms(),
                     "read": lj.read_telegram_read()},
        "activity": lj.read_activity(),
    }
    icon = lj.read_icon()
    if icon:
        data["icon"] = icon
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = DATA_DIR / f"pre-2.10-backup-{stamp}.json"
    try:
        dest.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        raise MigrationError(f"could not write pre-migration backup: {exc}")
    print(f"[db] wrote pre-migration backup {dest.name}", flush=True)


def _fan_norm(v):
    """Canonicalise a schedule entry's fan to compare source vs DB regardless of
    the legacy shape (string | list-of-groups | absent/None)."""
    if isinstance(v, str):
        return v or None
    if isinstance(v, list):
        out = []
        for g in v:
            if isinstance(g, dict):
                modes = [str(m) for m in (g.get("modes") or [])]
                val = g.get("fan")
                if modes and val is not None and str(val) != "":
                    out.append((tuple(modes), str(val)))
        return out or None
    return None


def _temp_norm(t):
    """Normalize an entry's temp for comparison. Polymorphic (v2.9.52): None, a
    number (float-coerced, so legacy 68 == 68.0), or a per-unit list [{unit,temp}]
    reduced to an order-independent set of (unit, value)."""
    if t is None:
        return None
    if isinstance(t, list):
        groups = []
        for g in t:
            if isinstance(g, dict):
                u = str(g.get("unit") or "").strip().upper().lstrip("°")
                v = g.get("temp")
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    pass
                groups.append((u, v))
        return ("list", tuple(sorted(groups, key=lambda x: x[0])))
    try:
        return float(t)
    except (TypeError, ValueError):
        return t


def _dedup(seq):
    """Order-preserving de-duplication. The store's child tables carry a composite
    PRIMARY KEY, so a repeated target/day/entity is collapsed to its first
    occurrence on save; the comparison must do the same or benign duplicates in the
    source JSON would read as a (false) mismatch and abort the whole migration."""
    seen = set()
    out = []
    for x in seq or []:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _days_norm(days):
    out = []
    for d in days or []:
        try:
            out.append(int(d))
        except (TypeError, ValueError):
            continue
    return _dedup(out)


def _entry_key(e):
    return (
        e.get("time") or "",
        e.get("mode") or "heat",
        _temp_norm(e.get("temp")),
        _days_norm(e.get("days")),
        _fan_norm(e.get("fan")),
    )


def _sched_key(s):
    return (
        s.get("owner") or "", s.get("name") or "", bool(s.get("enabled", True)),
        s.get("type") or None, s.get("start") or None, s.get("end") or None,
        _dedup(list(s.get("targets") or [])),
        [_entry_key(e) for e in (s.get("entries") or [])],
    )


def _schedules_match(got, want):
    """Semantic equality: the same schedules with the same owner/name/enabled/type/
    dates/targets and the same ordered entries by value. Both sides are re-sorted in
    Python (SQLite's lower() is ASCII-only, so a name-sort on non-ASCII names can
    diverge from the legacy loader's full-Unicode sort), and set-like fields
    (targets, days) are de-duplicated to match the store. Tolerant of legacy
    key-presence, id backfill, and int/float temp."""
    if len(got) != len(want):
        return False
    key = lambda s: ((s.get("name") or "").lower(), s.get("id") or "")
    return all(_sched_key(a) == _sched_key(b)
               for a, b in zip(sorted(got, key=key), sorted(want, key=key)))


def _verify_import(lj, store, tstore, seeded_users):
    """Prove the DB round-trips every store identically to the source JSON.
    Any mismatch raises MigrationError (fail loud; JSON stays intact)."""
    from user import User

    def norm_users(lst):
        # Compare through the model so key-omission/coercion is source-agnostic, and
        # normalize the same way the store does: entities are a set (the child table
        # PK dedups them), and a null per-entity expiry is dropped (NOT NULL column).
        out = []
        for u in lst:
            d = User.from_dict(u).to_dict()
            if "entities" in d:
                d["entities"] = _dedup(d["entities"])
            if d.get("entity_expires"):
                kept = {k: v for k, v in d["entity_expires"].items() if v is not None}
                if kept:
                    d["entity_expires"] = kept
                else:
                    d.pop("entity_expires", None)
            out.append(d)
        return out

    def drop_empty(d):
        # A user with an empty collection leaves no child rows, so the key is absent
        # on load; treat absent == empty on both sides (an empty bucket is no data).
        return {k: v for k, v in (d or {}).items() if v}

    def tg_config_norm(cfg):
        # Channel ids are TEXT in the DB (SQLite coerces an int id to its string);
        # a missing/null channel name stores as '' (NOT NULL). Canonicalize both.
        cfg = cfg or {}
        chans = [{"id": None if c.get("id") is None else str(c.get("id")),
                  "name": c.get("name") or ""}
                 for c in (cfg.get("channels") or []) if isinstance(c, dict)]
        return {"creds": cfg.get("creds") or {}, "channels": chans}

    def tg_perms_norm(perms):
        return {u: [str(c) for c in (ids or [])] for u, ids in (perms or {}).items()}

    def fail(name):
        raise MigrationError(
            f"import verification failed for '{name}': the database does not "
            f"match the source JSON. Aborting; your JSON files are untouched.")

    if norm_users(store.load_users()) != norm_users(seeded_users):
        fail("users")
    if not _schedules_match(store.load_schedules(), lj.read_schedules()):
        fail("schedules")
    # Activity is a disposable rolling log (capped at ACTIVITY_MAX); verify no
    # entries were dropped rather than byte-for-byte.
    if len(store._load_activity()) != min(len(lj.read_activity()), store.ACTIVITY_MAX):
        fail("activity")
    # Read settings from the DB, not the cache _save_settings just populated, so the
    # gate actually proves the table round-trips.
    cached = store._SETTINGS_CACHE["data"]
    store._SETTINGS_CACHE["data"] = None
    try:
        db_settings = store._load_settings()
    finally:
        store._SETTINGS_CACHE["data"] = cached
    exact = [
        ("settings", db_settings, lj.read_settings()),
        ("schedule_perms", drop_empty(store.load_schedule_perms()), drop_empty(lj.read_schedule_perms())),
        ("lists", drop_empty(store.load_lists()), drop_empty(lj.read_lists())),
        ("telegram_config", tg_config_norm(tstore.load_config()), tg_config_norm(lj.read_telegram_config())),
        ("telegram_perms", tg_perms_norm(tstore.load_perms()), tg_perms_norm(lj.read_telegram_perms())),
        ("telegram_read", tstore.load_read(), lj.read_telegram_read()),
    ]
    for name, got, want in exact:
        if got != want:
            fail(name)
    print("[db] import verified: all stores match the source JSON", flush=True)
