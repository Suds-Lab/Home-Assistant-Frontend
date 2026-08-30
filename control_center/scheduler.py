"""Background climate scheduler for Control Center.

A daemon thread wakes every 30 seconds, checks the current HH:MM and day-of-week
against all enabled schedules, and fires HA climate services for each matching entry.
Edge-triggered: the thermostat holds whatever state the last fired event set it to
until the next event fires.
"""
import re
import threading
import time
from datetime import datetime

_SCHEDULER_STARTED = False
_SCHEDULER_LOCK = threading.Lock()

_MODE_TO_HVAC = {
    "cool": "cool",
    "heat": "heat",
    "auto": "heat_cool",
    "dry": "dry",
    "fan": "fan_only",
}

# Human-readable mode names for the activity log (match the schedule editor).
_MODE_DISPLAY = {
    "off": "Off", "cool": "Cool", "heat": "Heat",
    "auto": "Auto", "dry": "Dry", "fan": "Fan only",
}

# States that mean the thermostat isn't actually reachable, so a fired event
# changes nothing and must not be logged as if the owner acted on it.
_OFFLINE_STATES = (None, "unavailable", "unknown")

# Some thermostats (e.g. Honeywell) don't take a change on the first command -
# HA shows it, then polls the device and bounces it back. After an event fires we
# keep an eye on the target for a short window and re-send if its live mode/temp
# drifts from what the schedule set. The window is deliberately short so we heal a
# bounce-back (which happens within minutes) without fighting a later MANUAL
# override. Touched only by the single scheduler thread, so no lock is needed.
_pending = {}  # target -> {mode, temp, sched_id, name, owner, first_ts, last_try}
_VERIFY_WINDOW_SECS = 600   # keep verifying for ~10 min after a fire
_VERIFY_RETRY_SECS = 120    # re-send at most every ~2 min per target
_VERIFY_TEMP_TOL = 0.6      # degrees; tolerate float/unit rounding
_VERIFY_MAX_TRIES = 2       # give up after this many re-applies that didn't stick
                            # (a real bounce-back heals in 1; more means we're
                            #  fighting the device, so stand down instead of flapping)


# ANSI colors for the add-on log (Supervisor's log viewer renders them).
_C_RESET, _C_RED, _C_AMBER, _C_CYAN = "\x1b[0m", "\x1b[31m", "\x1b[33m", "\x1b[36m"


def _log(msg):
    """One timestamped diagnostics line in the add-on log, prefixed [sched] so it
    is easy to grep, and coloured by severity. flush=True so lines appear promptly."""
    ts = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    low = msg.lower()
    if any(k in low for k in ("fail", "error", "giving up", "could not", "couldn't")):
        print(f"{_C_RED}[sched {ts}] {msg}{_C_RESET}", flush=True)          # error: whole line red
    elif any(k in low for k in ("warning", "skip", "drift", "backing off", "stopped", "stand down")):
        print(f"{_C_AMBER}[sched {ts}] {msg}{_C_RESET}", flush=True)        # warning: whole line amber
    else:
        print(f"{_C_CYAN}[sched {ts}]{_C_RESET} {msg}", flush=True)         # info: cyan tag


# A person changing a thermostat (directly in Home Assistant, or via Control
# Center) should win: we stop enforcing the schedule on that device until its
# next event. `_manual` records the time of the last human change per target.
# `_own_cmd` records our own sends, so a change WE caused (which HA may attribute
# to our token's user) is never mistaken for a human override. A change with no
# human behind it (a device bouncing back) is a "mystery" and still gets
# re-applied.
_manual = {}    # target -> ts of last human change
_own_cmd = {}   # target -> ts of our last send
_OWN_CMD_GRACE = 10  # sec; a HA change within this of our send is ours, not a person's


def note_user_change(target, who=None):
    """Record that a person changed this target. Called from the HA event loop
    (for user-driven state changes) and from the Control Center control route."""
    _manual[target] = time.time()
    _log(f"user change on {target}" + (f" by {who}" if who else "") + " - will not fight it")


def was_recently_commanded(target):
    """True if the scheduler itself sent to this target moments ago, so the
    resulting HA state change must not be counted as a human override."""
    return time.time() - _own_cmd.get(target, 0) < _OWN_CMD_GRACE


# --- Fan speed: per-vocabulary, exact values only ---------------------------
# A schedule event stores fan as a LIST of vocabulary groups:
#   [{"modes": [...that unit's real fan_modes...], "fan": "<a value from them>"}]
# Each targeted thermostat is matched to its group by SET EQUALITY of its LIVE
# fan_modes to the group's stored modes (order- and encoding-independent, so it is
# correct for any characters - no cross-language sort/signature to get wrong), and
# gets that group's chosen value. Because the value came from a real unit's own
# fan_modes, it is always something that unit supports; a unit in no group is left
# alone. Legacy schedules stored a single generic string (auto/low/medium/high);
# those still resolve via _exact_fan below.
def _exact_fan(fan, target):
    """The device's own fan mode that EXACTLY equals the requested level
    (case-insensitive), or None when there is no single unambiguous match. Used
    for LEGACY string fan values only."""
    from core import STATE_CACHE
    modes = ((STATE_CACHE.get(target) or {}).get("attributes") or {}).get("fan_modes") or []
    want = str(fan).strip().lower()
    matches = [m for m in modes if str(m).strip().lower() == want]
    return matches[0] if len(matches) == 1 else None


def _resolve_fan(fan, target):
    """The fan mode to send to `target` for this event, or None to leave the fan
    alone. `fan` is a list of {modes, fan} vocabulary groups (new shape), a single
    generic string (legacy), or falsy."""
    if not fan:
        return None
    if isinstance(fan, str):
        return _exact_fan(fan, target)
    from core import STATE_CACHE
    modes = ((STATE_CACHE.get(target) or {}).get("attributes") or {}).get("fan_modes") or []
    live = set(str(m) for m in modes)
    if not live:
        return None
    for grp in fan:
        if not isinstance(grp, dict):
            continue
        if set(str(m) for m in (grp.get("modes") or [])) == live:
            want = grp.get("fan")
            # Belt-and-braces: only send a value the unit actually has right now.
            return want if want is not None and str(want) in live else None
    return None


# --- Failure reporting: a red add-on-log line + one HA notification per unit --
_notified = set()  # targets we currently have a failure notification up for


def _notif_id(target):
    return "cc_sched_fail_" + re.sub(r"[^a-zA-Z0-9_]", "_", target)


def _entity_disabled(target):
    """Best-effort: True if HA's entity registry marks this entity disabled."""
    try:
        from ha import ha_registries_cached
        inst = target.split(":", 1)[0] if ":" in target else None
        real = target.split(":", 1)[1] if ":" in target else target
        reg = ha_registries_cached(instance_id=inst) or {}
        return any(e.get("entity_id") == real and e.get("disabled_by")
                   for e in reg.get("entities", []))
    except Exception:  # noqa: BLE001
        return False


def _fire_failure_reason(target, b_state):
    """Why a fire can't land right now, as a short phrase (None if reachable)."""
    if _entity_disabled(target):
        return "disabled in Home Assistant"
    if b_state is None:
        return "offline (no live state in Home Assistant)"
    if b_state == "unavailable":
        return "unavailable"
    if b_state == "unknown":
        return "state unknown"
    return None


def _report_failure(sched_name, target, reason):
    """One concise red add-on-log line plus a persistent notification in HA, so a
    unit that didn't get its scheduled setting is visible in both places."""
    from core import STATE_CACHE
    from ha import create_notification
    name = ((STATE_CACHE.get(target) or {}).get("attributes") or {}).get("friendly_name") \
        or target.split(":", 1)[-1]
    _log(f"FAILED: '{sched_name}' could not set {target} - {reason}")
    create_notification(
        _notif_id(target),
        f"Schedule couldn't set {name}",
        f"**{sched_name}** could not set **{name}** (`{target}`): {reason}.",
    )
    _notified.add(target)


def _clear_failure(target):
    """Dismiss a unit's failure notification once it has been set successfully."""
    if target in _notified:
        from ha import dismiss_notification
        dismiss_notification(_notif_id(target))
        _notified.discard(target)


def _send_climate(target, mode, temp, fan=None):
    """Issue the HA climate service calls for one target. Shared by the initial
    fire and the verification re-send.

    `fan` is passed ONLY on the initial fire, never on a verify re-apply, so a
    finicky fan can't be re-hammered into a flap. It is sent only when it exactly
    matches one of the unit's live fan_modes (see _exact_fan); otherwise the fan
    is left untouched."""
    from ha import call_service, split_instance_entity

    _own_cmd[target] = time.time()  # so the resulting HA change isn't read as a person's

    # Targets are stored namespaced for remote entities (e.g. "garage:climate.ac").
    # Split so the command routes to the owning instance; without this the call
    # goes to the main HA with a bogus id and silently no-ops.
    instance_id, real_target = split_instance_entity(target)

    if mode == "off":
        call_service("climate", "turn_off", real_target, instance_id=instance_id)
        return

    hvac_mode = _MODE_TO_HVAC.get(mode, mode)
    call_service("climate", "set_hvac_mode", real_target, {"hvac_mode": hvac_mode}, instance_id=instance_id)
    if temp is not None:
        call_service("climate", "set_temperature", real_target, {"temperature": float(temp)}, instance_id=instance_id)
    if fan:
        resolved = _resolve_fan(fan, target)
        if resolved is not None:
            call_service("climate", "set_fan_mode", real_target, {"fan_mode": resolved}, instance_id=instance_id)
        else:
            _log(f"  fan left alone on {target} - none of its fan modes matched the schedule")


def _fire_event(sched, entry, target):
    from core import STATE_CACHE
    mode = entry.get("mode", "heat")
    temp = entry.get("temp")
    fan = entry.get("fan")
    name = sched.get("name") or "Schedule"

    # Snapshot the device before we touch it, so the log shows what actually
    # changed (and whether the device was even reachable).
    before = STATE_CACHE.get(target) or {}
    b_state = before.get("state")
    b_temp = (before.get("attributes") or {}).get("temperature")
    offline = b_state in _OFFLINE_STATES
    want = "off" if mode == "off" else _MODE_TO_HVAC.get(mode, mode)
    detail = want
    if mode != "off" and temp is not None:
        detail += f" {temp}°"
    if mode != "off" and fan:
        shown = _resolve_fan(fan, target)  # what this specific unit will get, if anything
        if shown is not None:
            detail += f" fan~{shown}"
    was = "offline" if offline else (f"{b_state}" + (f" {b_temp}°" if b_temp is not None else ""))
    _log(f"FIRE '{name}' [{entry.get('time')}] -> {target}: set {detail} (was {was})")

    t0 = time.time()
    try:
        _send_climate(target, mode, temp, fan)
    except Exception as exc:  # noqa: BLE001
        _log(f"  FAILED to send to {target} after {int((time.time() - t0) * 1000)}ms: {exc}")
        _report_failure(name, target, f"send error: {exc}")
        return  # don't watch a send that never went out
    _log(f"  sent to {target} in {int((time.time() - t0) * 1000)}ms")

    # Record in the activity log only if the thermostat is actually online, so a
    # schedule firing at an offline device doesn't show up as if its owner acted.
    # An offline fire is a failure the owner should see; an online one clears any
    # standing failure for that unit.
    if offline:
        reason = _fire_failure_reason(target, b_state) or "unavailable"
        _log(f"  {target} was {b_state}; command was still sent but may not arrive "
             f"(not recorded in the activity log)")
        _report_failure(name, target, reason)
    else:
        _clear_failure(target)
        _log_scheduled(sched, entry, target)

    # Watch this target for a short window and re-send if the thermostat drifts
    # from what we just set (see _verify_pending). A new fire for the same target
    # overwrites the prior entry - the latest event wins.
    now = time.time()
    _pending[target] = {
        "mode": mode, "temp": temp,
        "sched_id": sched.get("id"), "name": name,
        "owner": sched.get("owner"), "first_ts": now, "last_try": now, "tries": 0,
    }
    _log(f"  confirming {target} for up to {_VERIFY_WINDOW_SECS // 60} min")


def _log_scheduled(sched, entry, target):
    """Append a scheduled climate change to the activity log, attributed to the
    schedule's owner and marked as coming from that schedule."""
    from store import _append_activity, load_users
    from core import STATE_CACHE

    owner = sched.get("owner")
    display = next(
        (u.get("displayName") or u.get("username")
         for u in load_users() if u.get("username") == owner),
        owner,
    ) or "A user"

    attrs = (STATE_CACHE.get(target) or {}).get("attributes") or {}
    entity_name = attrs.get("friendly_name") or target.split(":", 1)[-1]

    mode = entry.get("mode", "heat")
    temp = entry.get("temp")
    mode_label = _MODE_DISPLAY.get(mode, mode)
    if mode == "off":
        verb = "was turned off"
    elif temp is not None:
        t = float(temp)
        temp_str = str(int(t)) if t.is_integer() else str(t)
        verb = f"was set to {mode_label} {temp_str}°"
    else:
        verb = f"was set to {mode_label}"

    _append_activity({
        "ts": time.time(),
        "username": owner,
        "name": display,
        "entity_id": target,
        "entity": entity_name,
        "domain": "climate",
        "service": "schedule",
        "verb": verb,
        "source": "schedule",
        "schedule": sched.get("name") or "Schedule",
    })


def _log_giveup(p, target):
    """Activity-feed line when the scheduler stops fighting a device that keeps
    reverting, so the owner sees it in the app without reading the add-on log."""
    from store import _append_activity, load_users
    from core import STATE_CACHE

    owner = p.get("owner")
    display = next(
        (u.get("displayName") or u.get("username")
         for u in load_users() if u.get("username") == owner),
        owner,
    ) or "A user"
    attrs = (STATE_CACHE.get(target) or {}).get("attributes") or {}
    entity_name = attrs.get("friendly_name") or target.split(":", 1)[-1]
    want = "Off" if p["mode"] == "off" else _MODE_DISPLAY.get(p["mode"], p["mode"])
    _append_activity({
        "ts": time.time(),
        "username": owner,
        "name": display,
        "entity_id": target,
        "entity": entity_name,
        "domain": "climate",
        "service": "schedule",
        "verb": f"could not be held at {want} (it kept reverting)",
        "source": "schedule",
        "schedule": p.get("name") or "Schedule",
    })


def _matches_desired(target, mode, temp):
    """Whether the target's live state already matches the scheduled mode/temp.
    Returns None when the device is offline (can't tell), True/False otherwise."""
    from core import STATE_CACHE
    st = STATE_CACHE.get(target) or {}
    cur = st.get("state")
    if cur in _OFFLINE_STATES:
        return None
    want = "off" if mode == "off" else _MODE_TO_HVAC.get(mode, mode)
    if cur != want:
        return False
    if mode != "off" and temp is not None:
        cur_temp = (st.get("attributes") or {}).get("temperature")
        if cur_temp is None or abs(float(cur_temp) - float(temp)) > _VERIFY_TEMP_TOL:
            return False
    return True


def _verify_pending(schedules, override_targets=None):
    """For each recently-fired target still inside its verification window, re-send
    the scheduled mode/temp if the thermostat has drifted from it (rate-limited).
    Drops entries once the window closes or their schedule is gone/disabled."""
    if not _pending:
        return
    from core import STATE_CACHE
    now = time.time()
    enabled_ids = {s.get("id") for s in schedules if s.get("enabled", True)}
    override_targets = override_targets or set()
    weekly_ids = {s.get("id") for s in schedules if s.get("type") != "override"}
    for target in list(_pending.keys()):
        p = _pending[target]
        if now - p["first_ts"] > _VERIFY_WINDOW_SECS:
            _pending.pop(target, None)
            _log(f"done confirming {target} ('{p['name']}') - window ended")
            continue
        if p["sched_id"] not in enabled_ids:
            _pending.pop(target, None)
            _log(f"stopped confirming {target} ('{p['name']}') - schedule disabled or removed")
            continue
        # An override now governs this target: stop enforcing a stale weekly value.
        if target in override_targets and p["sched_id"] in weekly_ids:
            _pending.pop(target, None)
            _log(f"stopped confirming {target} ('{p['name']}') - an override now governs it")
            continue
        m = _matches_desired(target, p["mode"], p["temp"])
        if m:
            _clear_failure(target)  # it took (perhaps after a retry) - drop any failure note
            continue
        if m is None:
            continue  # offline (can't tell) - leave it watching
        # Drifted. If a PERSON moved it (in HA or via Control Center) after we
        # fired, stand down - they win until the next event. A drift with no
        # human behind it (a device bouncing back) is a mystery we keep enforcing.
        ov = _manual.get(target)
        if ov is not None and ov >= p["first_ts"]:
            _pending.pop(target, None)
            _log(f"backing off {target} ('{p['name']}') - a person changed it; "
                 f"schedule resumes at its next event")
            continue
        if now - p["last_try"] < _VERIFY_RETRY_SECS:
            continue  # drifted (mystery), but re-sent too recently - wait
        # Already re-applied the max number of times and it STILL drifted: we're
        # fighting the device (e.g. it rejects a command and drops to off). Stand
        # down instead of flapping, and tell the owner in the activity feed.
        if p.get("tries", 0) >= _VERIFY_MAX_TRIES:
            _pending.pop(target, None)
            _log(f"giving up on {target} ('{p['name']}') after {p['tries']} re-applies - "
                 f"it keeps reverting; standing down until the next event")
            _log_giveup(p, target)
            _report_failure(p.get("name") or "Schedule", target,
                            "didn't respond (kept reverting after retries)")
            continue
        p["last_try"] = now
        p["tries"] = p.get("tries", 0) + 1
        st = STATE_CACHE.get(target) or {}
        cur = st.get("state")
        cur_t = (st.get("attributes") or {}).get("temperature")
        want = "off" if p["mode"] == "off" else _MODE_TO_HVAC.get(p["mode"], p["mode"])
        want_t = "" if (p["mode"] == "off" or p["temp"] is None) else f" {p['temp']}°"
        have = f"{cur}" + (f" {cur_t}°" if cur_t is not None else "")
        _log(f"DRIFT {target} ('{p['name']}'): want {want}{want_t}, have {have} - "
             f"re-applying (try {p['tries']}/{_VERIFY_MAX_TRIES})")
        try:
            _send_climate(target, p["mode"], p["temp"])
        except Exception as exc:  # noqa: BLE001
            _log(f"  re-apply FAILED on {target}: {exc}")


# ---------------------------------------------------------------------------
# Override (holiday) schedules
#
# An override schedule (type == "override") is active only within its inclusive
# [start, end] date window ("YYYY-MM-DD"). While active it GOVERNS its target
# thermostats: its own time events fire every day in the window (day-of-week is
# ignored), and every weekly schedule is suppressed for those targets. A weekly
# schedule has no type (or type == "weekly").
# ---------------------------------------------------------------------------

# Remembers that we've already applied the "in effect now" override event for a
# (sched_id, target, YYYY-MM-DD), so a window that opens mid-day takes effect at
# once (not only at the next event) without re-firing every tick.
_override_applied = {}


def _in_window(sched, today):
    start = sched.get("start", "")
    return start <= today <= (sched.get("end") or start)


def _override_active(sched, today):
    """An override that should govern its targets today: enabled, in its date
    window, and with at least one event. An enabled-but-empty override is inert -
    it must NOT suppress weekly (that would silently freeze the device)."""
    return (
        sched.get("type") == "override"
        and sched.get("enabled", True)
        and bool(sched.get("entries"))
        and _in_window(sched, today)
    )


def _override_covered_targets(schedules, today):
    """Every thermostat governed by an active override today."""
    covered = set()
    for s in schedules:
        if _override_active(s, today):
            covered.update(s.get("targets", []))
    return covered


def compute_due(schedules, hhmm, dow, today):
    """Pure: what should fire at this minute, and which targets an override governs
    today. Returns (due, override_targets):
      - due: list of (sched, entry) for ENABLED schedules whose entry.time == hhmm;
        override entries match by date window (any day), weekly entries by day-of-week.
      - override_targets: set of thermostats governed by an active override today.
    The caller suppresses weekly fires for any target in override_targets."""
    override_targets = _override_covered_targets(schedules, today)
    due = []
    for sched in schedules:
        if not sched.get("enabled", True):
            continue
        is_ovr = sched.get("type") == "override"
        if is_ovr and not _in_window(sched, today):
            continue
        for entry in sched.get("entries", []):
            if entry.get("time") != hhmm:
                continue
            if not is_ovr and dow not in entry.get("days", []):
                continue
            due.append((sched, entry))
    return due, override_targets


def resolve_scheduled_fires(schedules, hhmm, dow, today):
    """Pure: the exact (sched, entry, target) tuples to fire this minute AFTER an
    active override wins over weekly on the thermostats it governs, plus the
    weekly (sched, target) pairs that were SKIPPED for that reason. This is the
    real precedence the loop applies, so a test can assert the skip on the same
    code the scheduler runs (not a copy)."""
    due, override_targets = compute_due(schedules, hhmm, dow, today)
    fires, skipped = [], []
    for sched, entry in due:
        is_ovr = sched.get("type") == "override"
        for target in sched.get("targets", []):
            if not is_ovr and target in override_targets:
                skipped.append((sched, target))
            else:
                fires.append((sched, entry, target))
    return fires, skipped


def _scheduler_loop():
    from store import load_schedules
    _log("scheduler started (checks every 30s)")
    last_hhmm = None
    last_minute = None    # the minute we last evaluated, to spot skipped minutes
    last_beat_hour = None  # so the "alive" heartbeat prints once per hour
    last_today = None      # to reset the override catch-up log at midnight
    while True:
        try:
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            dow = now.weekday()  # 0=Mon..6=Sun, matching data shape
            today = now.strftime("%Y-%m-%d")  # naive local date, same clock as hhmm/dow
            schedules = load_schedules()
            # Only evaluate once per minute even if we wake up multiple times in it
            if hhmm != last_hhmm:
                this_minute = now.replace(second=0, microsecond=0)
                # If the loop didn't run for a whole minute (blocked by a slow
                # command, or the add-on was down), those minutes were never
                # evaluated - so any events in them did NOT fire. Call it out.
                if last_minute is not None:
                    gap = round((this_minute - last_minute).total_seconds() / 60)
                    if gap > 1:
                        _log(f"WARNING: {gap - 1} minute(s) not checked between "
                             f"{last_minute:%H:%M} and {hhmm} (loop blocked or add-on was "
                             f"down); any events scheduled in that window did NOT fire")
                last_minute = this_minute
                last_hhmm = hhmm

                # New day: forget yesterday's override catch-up markers.
                if today != last_today:
                    _override_applied.clear()
                    last_today = today

                due, _ = compute_due(schedules, hhmm, dow, today)  # for diagnostics below

                # Keep the v2.9.14 diagnostic: note a disabled schedule that would
                # otherwise have fired now.
                for sched in schedules:
                    if sched.get("enabled", True):
                        continue
                    is_ovr = sched.get("type") == "override"
                    if is_ovr and not _in_window(sched, today):
                        continue
                    for entry in sched.get("entries", []):
                        if entry.get("time") == hhmm and (is_ovr or dow in entry.get("days", [])):
                            _log(f"{hhmm}: SKIP '{sched.get('name') or 'Schedule'}' - schedule is disabled")
                            break

                # Window-open catch-up: the first time we see an override active on a
                # target today, apply the event "in effect now" (latest time <= now),
                # so an all-day hold set mid-day takes effect at once. The exact-minute
                # event is left to the normal fire below (avoids a double send).
                for sched in schedules:
                    if not _override_active(sched, today):
                        continue
                    past = [e for e in sched.get("entries", []) if e.get("time", "") <= hhmm]
                    ineffect = max(past, key=lambda e: e.get("time", "")) if past else None
                    for target in sched.get("targets", []):
                        key = (sched.get("id"), target, today)
                        if key in _override_applied:
                            continue
                        _override_applied[key] = True
                        if ineffect is not None and ineffect.get("time") != hhmm:
                            try:
                                _log(f"{hhmm}: override '{sched.get('name') or 'Override'}' now in "
                                     f"effect on {target} - applying its {ineffect.get('time')} event")
                                _fire_event(sched, ineffect, target)
                            except Exception as exc:  # noqa: BLE001
                                _log(f"  override catch-up error on {target}: {exc}")

                # An active override governs its targets, so weekly stands aside.
                fires, skipped = resolve_scheduled_fires(schedules, hhmm, dow, today)
                for sched, target in skipped:
                    _log(f"  SKIP weekly '{sched.get('name') or 'Schedule'}' on "
                         f"{target} - an override governs it today")
                if due:
                    total = sum(len(s.get("targets", [])) for s, _ in due)
                    _log(f"{hhmm} {now:%a}: {len(due)} event(s) due across {total} target(s)")
                    for sched, _entry in due:
                        if not sched.get("targets", []):
                            _log(f"  '{sched.get('name') or 'Schedule'}' has no targets - nothing to do")
                for sched, entry, target in fires:
                    try:
                        _fire_event(sched, entry, target)
                    except Exception as exc:  # noqa: BLE001
                        _log(f"  unexpected error firing on {target}: {exc}")

                # Hourly heartbeat so the log confirms the scheduler is alive even
                # on quiet days.
                if now.minute == 0 and last_beat_hour != now.hour:
                    last_beat_hour = now.hour
                    enabled = sum(1 for s in schedules if s.get("enabled", True))
                    _log(f"alive - {enabled}/{len(schedules)} schedule(s) enabled, "
                         f"{len(_pending)} awaiting confirmation")
            # Every tick: re-apply any recently-fired target that didn't take.
            _verify_pending(schedules, _override_covered_targets(schedules, today))
        except Exception as exc:  # noqa: BLE001
            _log(f"loop error: {exc}")
        time.sleep(30)


def ensure_scheduler():
    """Start the background scheduler thread (idempotent - safe to call multiple times)."""
    global _SCHEDULER_STARTED
    with _SCHEDULER_LOCK:
        if _SCHEDULER_STARTED:
            return
        t = threading.Thread(target=_scheduler_loop, daemon=True, name="climate-scheduler")
        t.start()
        _SCHEDULER_STARTED = True
