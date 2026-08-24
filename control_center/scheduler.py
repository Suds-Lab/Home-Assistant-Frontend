"""Background climate scheduler for Control Center.

A daemon thread wakes every 30 seconds, checks the current HH:MM and day-of-week
against all enabled schedules, and fires HA climate services for each matching entry.
Edge-triggered: the thermostat holds whatever state the last fired event set it to
until the next event fires.
"""
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
_pending = {}  # target -> {mode, temp, fan, sched_id, name, owner, first_ts, last_try}
_VERIFY_WINDOW_SECS = 600   # keep verifying for ~10 min after a fire
_VERIFY_RETRY_SECS = 120    # re-send at most every ~2 min per target
_VERIFY_TEMP_TOL = 0.6      # degrees; tolerate float/unit rounding


def _log(msg):
    """One timestamped diagnostics line in the add-on log, prefixed [sched] so it
    is easy to grep. flush=True so lines appear promptly, not buffered."""
    print(f"[sched {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


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


def _send_climate(target, mode, temp, fan):
    """Issue the HA climate service calls for one target. Shared by the initial
    fire and the verification re-send so they behave identically."""
    from ha import call_service, split_instance_entity

    _own_cmd[target] = time.time()  # so the resulting HA change isn't read as a person's

    # Targets are stored namespaced for remote entities (e.g. "garage:climate.ac").
    # Split so the command routes to the owning instance; without this the call
    # goes to the main HA with a bogus id and silently no-ops.
    instance_id, real_target = split_instance_entity(target)

    if mode == "off":
        call_service("climate", "turn_off", real_target, instance_id=instance_id)
    else:
        hvac_mode = _MODE_TO_HVAC.get(mode, mode)
        call_service("climate", "set_hvac_mode", real_target, {"hvac_mode": hvac_mode}, instance_id=instance_id)
        if temp is not None:
            call_service("climate", "set_temperature", real_target, {"temperature": float(temp)}, instance_id=instance_id)
        if fan:
            call_service("climate", "set_fan_mode", real_target, {"fan_mode": fan}, instance_id=instance_id)


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
        detail += f" fan={fan}"
    was = "offline" if offline else (f"{b_state}" + (f" {b_temp}°" if b_temp is not None else ""))
    _log(f"FIRE '{name}' [{entry.get('time')}] -> {target}: set {detail} (was {was})")

    t0 = time.time()
    try:
        _send_climate(target, mode, temp, fan)
    except Exception as exc:  # noqa: BLE001
        _log(f"  FAILED to send to {target} after {int((time.time() - t0) * 1000)}ms: {exc}")
        return  # don't watch a send that never went out
    _log(f"  sent to {target} in {int((time.time() - t0) * 1000)}ms")

    # Record in the activity log only if the thermostat is actually online, so a
    # schedule firing at an offline device doesn't show up as if its owner acted.
    if offline:
        _log(f"  {target} was {b_state}; command was still sent but may not arrive "
             f"(not recorded in the activity log)")
    else:
        _log_scheduled(sched, entry, target)

    # Watch this target for a short window and re-send if the thermostat drifts
    # from what we just set (see _verify_pending). A new fire for the same target
    # overwrites the prior entry - the latest event wins.
    now = time.time()
    _pending[target] = {
        "mode": mode, "temp": temp, "fan": fan,
        "sched_id": sched.get("id"), "name": name,
        "owner": sched.get("owner"), "first_ts": now, "last_try": now,
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


def _verify_pending(schedules):
    """For each recently-fired target still inside its verification window, re-send
    the scheduled mode/temp if the thermostat has drifted from it (rate-limited).
    Drops entries once the window closes or their schedule is gone/disabled."""
    if not _pending:
        return
    from core import STATE_CACHE
    now = time.time()
    enabled_ids = {s.get("id") for s in schedules if s.get("enabled", True)}
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
        m = _matches_desired(target, p["mode"], p["temp"])
        if m is None or m:
            continue  # offline (can't tell) or already correct - leave it watching
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
        p["last_try"] = now
        st = STATE_CACHE.get(target) or {}
        cur = st.get("state")
        cur_t = (st.get("attributes") or {}).get("temperature")
        want = "off" if p["mode"] == "off" else _MODE_TO_HVAC.get(p["mode"], p["mode"])
        want_t = "" if (p["mode"] == "off" or p["temp"] is None) else f" {p['temp']}°"
        have = f"{cur}" + (f" {cur_t}°" if cur_t is not None else "")
        _log(f"DRIFT {target} ('{p['name']}'): want {want}{want_t}, have {have} - re-applying")
        try:
            _send_climate(target, p["mode"], p["temp"], p["fan"])
        except Exception as exc:  # noqa: BLE001
            _log(f"  re-apply FAILED on {target}: {exc}")


def _scheduler_loop():
    from store import load_schedules
    _log("scheduler started (checks every 30s)")
    last_hhmm = None
    last_minute = None    # the minute we last evaluated, to spot skipped minutes
    last_beat_hour = None  # so the "alive" heartbeat prints once per hour
    while True:
        try:
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            dow = now.weekday()  # 0=Mon..6=Sun, matching data shape
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

                due = []
                for sched in schedules:
                    for entry in sched.get("entries", []):
                        if entry.get("time") == hhmm and dow in entry.get("days", []):
                            if sched.get("enabled", True):
                                due.append((sched, entry))
                            else:
                                _log(f"{hhmm}: SKIP '{sched.get('name') or 'Schedule'}' - schedule is disabled")

                if due:
                    total = sum(len(s.get("targets", [])) for s, _ in due)
                    _log(f"{hhmm} {now:%a}: {len(due)} event(s) due across {total} target(s)")
                    for sched, entry in due:
                        targets = sched.get("targets", [])
                        if not targets:
                            _log(f"  '{sched.get('name') or 'Schedule'}' has no targets - nothing to do")
                        for target in targets:
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
            _verify_pending(schedules)
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
