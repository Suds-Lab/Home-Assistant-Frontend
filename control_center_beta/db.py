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
  mode TEXT NOT NULL DEFAULT 'heat', temp REAL, fan TEXT);
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
        for version, step in MIGRATIONS:
            if version > current:
                step(conn)                          # manages its own transactions
                with transaction() as c:
                    _meta_set(c, "schema_version", version)
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
    _verify_import(lj, store, tstore, seeded_users=users)

    # Step 3: only now archive the JSON (rename, never delete).
    for p in present:
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


def _entry_key(e):
    temp = e.get("temp")
    return (
        e.get("time") or "",
        e.get("mode") or "heat",
        float(temp) if temp is not None else None,
        [int(d) for d in (e.get("days") or [])],
        _fan_norm(e.get("fan")),
    )


def _sched_key(s):
    return (
        s.get("owner") or "", s.get("name") or "", bool(s.get("enabled", True)),
        s.get("type") or None, s.get("start") or None, s.get("end") or None,
        list(s.get("targets") or []),
        [_entry_key(e) for e in (s.get("entries") or [])],
    )


def _schedules_match(got, want):
    """Semantic equality: same schedules (name-sorted on both sides) with the same
    owner/name/enabled/type/dates/targets and the same ordered entries by value -
    tolerant of legacy key-presence, entry-id backfill, and int/float temp."""
    if len(got) != len(want):
        return False
    return all(_sched_key(g) == _sched_key(w) for g, w in zip(got, want))


def _verify_import(lj, store, tstore, seeded_users):
    """Prove the DB round-trips every store identically to the source JSON.
    Any mismatch raises MigrationError (fail loud; JSON stays intact)."""
    from user import User

    def norm_users(lst):
        # Compare through the model so key-omission/coercion is source-agnostic.
        return [User.from_dict(u).to_dict() for u in lst]

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
    exact = [
        ("settings", store._load_settings(), lj.read_settings()),
        ("schedule_perms", store.load_schedule_perms(), lj.read_schedule_perms()),
        ("lists", store.load_lists(), lj.read_lists()),
        ("telegram_config", tstore.load_config(), lj.read_telegram_config()),
        ("telegram_perms", tstore.load_perms(), lj.read_telegram_perms()),
        ("telegram_read", tstore.load_read(), lj.read_telegram_read()),
    ]
    for name, got, want in exact:
        if got != want:
            fail(name)
    print("[db] import verified: all stores match the source JSON", flush=True)
