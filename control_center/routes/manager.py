"""Manager API routes.

Organize Home Assistant devices into areas and group areas into floors, writing
through to HA's device/area registries. Gated by require_manager (the signed-in
user must have the manager flag). Registered as a blueprint on the app in core.py.
"""
from flask import Blueprint, jsonify, request

from access import _domain_assignable
from errors import ApiError
from ha import _invalidate_registries, ha_registries, ha_request, ha_ws_command
from security import current_user

bp = Blueprint("manager", __name__)


def require_manager():
    user = current_user()
    if not user.get("manager"):
        raise ApiError("Manager access required", 403)
    return user


@bp.get("/api/manager/devices")
def manager_devices():
    """Devices the manager can organize - those with at least one entity the app
    exposes (an enabled type, or in the Included list) - with their current area
    plus the list of areas."""
    require_manager()
    reg = ha_registries()
    floors = {f["floor_id"]: f.get("name") for f in reg.get("floors", [])}
    area_by_id = {a["area_id"]: a for a in reg.get("areas", [])}
    integrations = reg.get("integrations", {})  # platform domain -> friendly name
    # Friendly names for a device's entities (from current states).
    names = {}
    try:
        for s in ha_request("/api/states"):
            names[s["entity_id"]] = s.get("attributes", {}).get("friendly_name") or s["entity_id"]
    except ApiError:
        pass
    ents_by_dev = {}   # device_id -> [friendly name]
    ids_by_dev = {}    # device_id -> [entity_id]
    plat_by_dev = {}   # device_id -> {platform: count} (which integration provides it)
    for e in reg.get("entities", []):
        did = e.get("device_id")
        if did:
            ents_by_dev.setdefault(did, []).append(names.get(e["entity_id"], e["entity_id"]))
            ids_by_dev.setdefault(did, []).append(e["entity_id"])
            plat = e.get("platform")
            if plat:
                counts = plat_by_dev.setdefault(did, {})
                counts[plat] = counts.get(plat, 0) + 1

    devices = []
    for d in reg.get("devices", []):
        # Only devices with at least one app-relevant entity (enabled type or
        # explicitly included) - not every HA device.
        if not any(_domain_assignable(eid) for eid in ids_by_dev.get(d["id"], [])):
            continue
        aid = d.get("area_id")
        area = area_by_id.get(aid)
        ents = sorted(ents_by_dev.get(d["id"], []))
        # The integration (HA platform) that provides this device, e.g.
        # "shelly"/"mqtt"/"hue" - used for the brand badge. Pick the most common
        # platform across the device's entities; pair it with the friendly
        # manifest name so acronyms and special casing aren't mangled.
        counts = plat_by_dev.get(d["id"])
        integ = max(counts, key=counts.get) if counts else None
        devices.append({
            "id": d["id"],
            "name": d.get("name_by_user") or d.get("name") or "Unnamed device",
            "manufacturer": d.get("manufacturer"),
            "model": d.get("model"),
            "area_id": aid,
            "area": area.get("name") if area else None,
            "floor": floors.get(area.get("floor_id")) if area else None,
            "integration": integ,
            "integration_name": integrations.get(integ) if integ else None,
            "entities": ents,
        })
    devices.sort(key=lambda x: (x["area"] or "￿", x["name"].lower()))
    areas = [
        {"area_id": a["area_id"], "name": a.get("name"), "floor": floors.get(a.get("floor_id"))}
        for a in reg.get("areas", [])
    ]
    areas.sort(key=lambda a: (a["name"] or "").lower())
    return jsonify(devices=devices, areas=areas)


@bp.post("/api/manager/device")
def manager_update_device():
    """Update a device's area and/or name (name_by_user). Only the fields
    present in the body are changed. Writes through to Home Assistant's device
    registry, so the change is reflected in HA."""
    require_manager()
    body = request.get_json(silent=True) or {}
    device_id = body.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        raise ApiError("device_id is required", 400)
    # Only devices the app actually exposes (an enabled-type/included entity)
    # may be managed - matches what the organizer shows.
    reg = ha_registries()
    dev_eids = [e["entity_id"] for e in reg.get("entities", []) if e.get("device_id") == device_id]
    if not any(_domain_assignable(eid) for eid in dev_eids):
        raise ApiError("That device isn't available to manage", 403)
    update = {"type": "config/device_registry/update", "device_id": device_id}
    if "area_id" in body:
        update["area_id"] = body.get("area_id") or None
    if "name" in body:
        update["name_by_user"] = (body.get("name") or "").strip() or None
    ha_ws_command(update)
    _invalidate_registries()  # so dashboards/picker pick up the change
    return jsonify(ok=True)


@bp.get("/api/manager/areas")
def manager_areas():
    """Floors and areas, for the manager's area organizer. Floors are read-only
    here (created in Home Assistant); areas can be created and moved between
    floors via the POST below."""
    require_manager()
    reg = ha_registries()
    floors = [{"floor_id": f["floor_id"], "name": f.get("name")} for f in reg.get("floors", [])]
    floors.sort(key=lambda f: (f["name"] or "").lower())
    fname = {f["floor_id"]: f["name"] for f in floors}
    areas = [
        {"area_id": a["area_id"], "name": a.get("name"),
         "floor_id": a.get("floor_id"), "floor": fname.get(a.get("floor_id")),
         "icon": a.get("icon")}  # e.g. "mdi:sofa" (or None)
        for a in reg.get("areas", [])
    ]
    areas.sort(key=lambda a: (a["name"] or "").lower())
    return jsonify(floors=floors, areas=areas)


@bp.post("/api/manager/area")
def manager_save_area():
    """Create an area or update an existing one (rename / move to a floor).
    Writes through to Home Assistant's area registry. Floors themselves are not
    created or deleted here - only assigned. Pass `area_id` to update, or omit
    it (with a `name`) to create."""
    require_manager()
    body = request.get_json(silent=True) or {}
    area_id = body.get("area_id")
    name = (body.get("name") or "").strip()
    has_floor = "floor_id" in body
    floor_id = body.get("floor_id") or None
    # If a floor was given, it must be a real one.
    if has_floor and floor_id is not None:
        reg = ha_registries()
        if not any(f.get("floor_id") == floor_id for f in reg.get("floors", [])):
            raise ApiError("That floor no longer exists", 400)
    if area_id:
        cmd = {"type": "config/area_registry/update", "area_id": area_id}
        if name:
            cmd["name"] = name
        if has_floor:
            cmd["floor_id"] = floor_id
    else:
        if not name:
            raise ApiError("An area name is required", 400)
        cmd = {"type": "config/area_registry/create", "name": name}
        if has_floor and floor_id is not None:
            cmd["floor_id"] = floor_id
    ha_ws_command(cmd)
    _invalidate_registries()
    return jsonify(ok=True)
