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


def _fire_event(sched, entry, target):
    from ha import call_service, split_instance_entity
    mode = entry.get("mode", "heat")
    temp = entry.get("temp")
    fan = entry.get("fan")

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

    # The service calls above succeeded (or we'd have raised). Record it in the
    # activity log - but only if the thermostat is actually online, so a schedule
    # firing at an offline device doesn't show up as if its owner changed it.
    from core import STATE_CACHE
    state = (STATE_CACHE.get(target) or {}).get("state")
    if state not in _OFFLINE_STATES:
        _log_scheduled(sched, entry, target)


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


def _scheduler_loop():
    from store import load_schedules
    last_hhmm = None
    while True:
        try:
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            dow = now.weekday()  # 0=Mon..6=Sun, matching data shape
            # Only fire once per minute even if we wake up multiple times in it
            if hhmm != last_hhmm:
                last_hhmm = hhmm
                schedules = load_schedules()
                for sched in schedules:
                    if not sched.get("enabled", True):
                        continue
                    for entry in sched.get("entries", []):
                        if entry.get("time") != hhmm:
                            continue
                        if dow not in entry.get("days", []):
                            continue
                        for target in sched.get("targets", []):
                            try:
                                _fire_event(sched, entry, target)
                            except Exception as exc:  # noqa: BLE001
                                print(f"[scheduler] error firing {entry.get('id')} on {target}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] loop error: {exc}")
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
