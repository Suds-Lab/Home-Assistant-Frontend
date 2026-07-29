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


def _fire_event(entry, target):
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
        return

    hvac_mode = _MODE_TO_HVAC.get(mode, mode)
    call_service("climate", "set_hvac_mode", real_target, {"hvac_mode": hvac_mode}, instance_id=instance_id)
    if temp is not None:
        call_service("climate", "set_temperature", real_target, {"temperature": float(temp)}, instance_id=instance_id)
    if fan:
        call_service("climate", "set_fan_mode", real_target, {"fan_mode": fan}, instance_id=instance_id)


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
                                _fire_event(entry, target)
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
