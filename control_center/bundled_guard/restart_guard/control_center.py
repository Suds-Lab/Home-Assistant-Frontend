"""Upcoming climate-schedule fires from the Control Center add-on.

Control Center (a separate Home Assistant add-on) publishes its own upcoming
schedule fires on one sensor, by default ``sensor.control_center_schedules``,
with the full list in the sensor's ``items`` attribute and a marker attribute
``rg_source == "control_center"``. Each item is one climate change about to
happen: which thermostat, when, and what it will be set to.

This reshapes those items into the same row shape the rest of Restart Guard
uses, so a Control Center schedule warns in the restart dialog exactly like a
Scheduler schedule or an automation does.

Deliberately free of Home Assistant imports so it can be unit tested on its own.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

SOURCE = "control_center"

ParseDateTime = Callable[[str], "dt.datetime | None"]


def collect(
    state: Any,
    now: dt.datetime,
    lookahead: int,
    parse: ParseDateTime,
) -> tuple[list[dict[str, Any]], int]:
    """Control Center fires due inside the lookahead window.

    Returns (items, scanned). ``state`` is the Control Center sensor's state
    object, or None when the sensor is missing; ``scanned`` is how many
    published fires were looked at, so "it isn't warning me" can be told apart
    from "it never saw the sensor at all".
    """
    if state is None:
        return [], 0
    attrs = getattr(state, "attributes", None) or {}
    raw_items = attrs.get("items") or []
    horizon = now + dt.timedelta(minutes=lookahead)
    # A Control Center row opens the add-on's own panel, not a per-thermostat
    # page, so every row taps through to the sensor entity itself.
    sensor_id = getattr(state, "entity_id", "") or ""

    items: list[dict[str, Any]] = []
    scanned = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        scanned += 1
        at = raw.get("at")
        if not at:
            continue
        try:
            moment = parse(str(at))
        except Exception:  # noqa: BLE001 - a malformed value must not break the sensor
            continue
        if moment is None:
            continue
        if moment.tzinfo is None and now.tzinfo is not None:
            moment = moment.replace(tzinfo=now.tzinfo)
        if not (now < moment <= horizon):
            continue

        name = str(raw.get("name") or "Schedule").strip()
        friendly = str(raw.get("friendly") or raw.get("entity_id") or "").strip()
        detail = str(raw.get("detail") or "").strip()
        alias = name
        if friendly:
            alias = f"{name}: {friendly}"
        if detail:
            alias = f"{alias} → {detail}"

        items.append(
            {
                "entity_id": sensor_id,
                "alias": alias,
                "at": moment.isoformat(),
                "at_ts": int(moment.timestamp()),
                "when": moment.strftime("%H:%M"),
                "minutes": round((moment - now).total_seconds() / 60.0, 1),
                "source": SOURCE,
                "tags": [],
            }
        )

    items.sort(key=lambda i: i["minutes"])
    return items, scanned
