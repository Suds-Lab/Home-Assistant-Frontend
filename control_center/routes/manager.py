"""Manager API routes.

Organize Home Assistant devices into areas and group areas into floors, writing
through to HA's device/area registries. Gated by require_manager (the signed-in
user must have the manager flag). Registered as a blueprint on the app in core.py.

Device IDs and area IDs from remote instances are namespaced as
`{instance_id}:{raw_id}` so the backend can route reads and writes to the
correct HA. The frontend treats them as opaque strings.
"""
from flask import Blueprint, jsonify, request

from access import _domain_assignable
from config import REMOTE_INSTANCES
from errors import ApiError
from ha import _invalidate_registries, ha_registries, ha_request, ha_ws_command
from security import current_user

bp = Blueprint("manager", __name__)


def require_manager():
    user = current_user()
    if not user.get("manager"):
        raise ApiError("Manager access required", 403)
    return user


def _prefix(instance_id, raw_id):
    """Namespace a HA device/area/floor ID with the instance prefix."""
    return f"{instance_id}:{raw_id}" if instance_id and raw_id else raw_id


def _split_id(namespaced_id):
    """Return (instance_id, raw_id). instance_id is None for main instance."""
    if namespaced_id and ":" in namespaced_id:
        iid, rid = namespaced_id.split(":", 1)
        return iid, rid
    return None, namespaced_id


@bp.get("/api/manager/devices")
def manager_devices():
    """Devices the manager can organize, aggregated across all HA instances.
    Device and area IDs are namespaced for remote instances. Each device carries
    an `instance` field (None = main) so the frontend can filter areas correctly."""
    require_manager()

    all_devices = []
    all_areas = []

    def _collect(instance_id, instance_name):
        try:
            reg = ha_registries(instance_id=instance_id)
        except Exception:  # noqa: BLE001
            return
        floors = {f["floor_id"]: f.get("name") for f in reg.get("floors", [])}
        area_by_id = {a["area_id"]: a for a in reg.get("areas", [])}
        integrations = reg.get("integrations", {})

        for a in reg.get("areas", []):
            fid = a.get("floor_id")
            all_areas.append({
                "area_id": _prefix(instance_id, a["area_id"]),
                "name": a.get("name"),
                "floor": floors.get(fid),
                "instance": instance_id,
                "instance_name": instance_name,
            })

        names = {}
        try:
            for s in ha_request("/api/states", instance_id=instance_id):
                names[s["entity_id"]] = s.get("attributes", {}).get("friendly_name") or s["entity_id"]
        except ApiError:
            pass

        ents_by_dev = {}
        ids_by_dev = {}
        plat_by_dev = {}
        for e in reg.get("entities", []):
            did = e.get("device_id")
            if did:
                ents_by_dev.setdefault(did, []).append(names.get(e["entity_id"], e["entity_id"]))
                ids_by_dev.setdefault(did, []).append(e["entity_id"])
                plat = e.get("platform")
                if plat:
                    counts = plat_by_dev.setdefault(did, {})
                    counts[plat] = counts.get(plat, 0) + 1

        for d in reg.get("devices", []):
            raw_eids = ids_by_dev.get(d["id"], [])
            full_eids = [_prefix(instance_id, eid) for eid in raw_eids]
            if not any(_domain_assignable(feid) for feid in full_eids):
                continue
            aid = d.get("area_id")
            area = area_by_id.get(aid)
            ents = sorted(ents_by_dev.get(d["id"], []))
            counts = plat_by_dev.get(d["id"])
            integ = max(counts, key=counts.get) if counts else None
            all_devices.append({
                "id": _prefix(instance_id, d["id"]),
                "name": d.get("name_by_user") or d.get("name") or "Unnamed device",
                "manufacturer": d.get("manufacturer"),
                "model": d.get("model"),
                "area_id": _prefix(instance_id, aid) if aid else None,
                "area": area.get("name") if area else None,
                "floor": floors.get(area.get("floor_id")) if area else None,
                "integration": integ,
                "integration_name": integrations.get(integ) if integ else None,
                "entities": ents,
                "instance": instance_id,
                "instance_name": instance_name,
            })

    _collect(None, "Main")
    for r in REMOTE_INSTANCES:
        _collect(r["id"], r["name"])

    all_devices.sort(key=lambda x: (x["area"] or "￿", x["name"].lower()))
    all_areas.sort(key=lambda a: (a["name"] or "").lower())
    return jsonify(devices=all_devices, areas=all_areas)


@bp.post("/api/manager/device")
def manager_update_device():
    """Update a device's area and/or name (name_by_user). The device_id may be
    namespaced (`garage:{uuid}`) for remote instances; the write is routed to
    the correct HA automatically."""
    require_manager()
    body = request.get_json(silent=True) or {}
    full_device_id = body.get("device_id")
    if not isinstance(full_device_id, str) or not full_device_id:
        raise ApiError("device_id is required", 400)

    instance_id, device_id = _split_id(full_device_id)

    reg = ha_registries(instance_id=instance_id)
    raw_eids = [e["entity_id"] for e in reg.get("entities", []) if e.get("device_id") == device_id]
    full_eids = [_prefix(instance_id, eid) for eid in raw_eids]
    if not any(_domain_assignable(feid) for feid in full_eids):
        raise ApiError("That device isn't available to manage", 403)

    update = {"type": "config/device_registry/update", "device_id": device_id}
    if "area_id" in body:
        raw_area = body.get("area_id")
        _, real_area_id = _split_id(raw_area) if raw_area else (None, None)
        update["area_id"] = real_area_id
    if "name" in body:
        update["name_by_user"] = (body.get("name") or "").strip() or None

    ha_ws_command(update, instance_id=instance_id)
    _invalidate_registries(instance_id)
    return jsonify(ok=True)


@bp.get("/api/manager/areas")
def manager_areas():
    """Floors and areas across all HA instances. IDs are namespaced for remote
    instances. Each item carries an `instance` field so the frontend can group
    and filter correctly."""
    require_manager()

    all_floors = []
    all_areas = []

    def _collect(instance_id, instance_name):
        try:
            reg = ha_registries(instance_id=instance_id)
        except Exception:  # noqa: BLE001
            return
        fname = {f["floor_id"]: f.get("name") for f in reg.get("floors", [])}
        for f in reg.get("floors", []):
            all_floors.append({
                "floor_id": _prefix(instance_id, f["floor_id"]),
                "name": f.get("name"),
                "instance": instance_id,
                "instance_name": instance_name,
            })
        for a in reg.get("areas", []):
            fid = a.get("floor_id")
            all_areas.append({
                "area_id": _prefix(instance_id, a["area_id"]),
                "name": a.get("name"),
                "floor_id": _prefix(instance_id, fid) if fid else None,
                "floor": fname.get(fid),
                "icon": a.get("icon"),
                "instance": instance_id,
                "instance_name": instance_name,
            })

    _collect(None, "Main")
    for r in REMOTE_INSTANCES:
        _collect(r["id"], r["name"])

    all_floors.sort(key=lambda f: ((f["instance"] or ""), (f["name"] or "").lower()))
    all_areas.sort(key=lambda a: (a["name"] or "").lower())
    return jsonify(floors=all_floors, areas=all_areas)


@bp.post("/api/manager/area")
def manager_save_area():
    """Create or update an area. For new areas without a floor, pass `instance`
    (None = main). For existing areas, the instance is derived from the
    namespaced area_id. Writes through to the correct HA registry."""
    require_manager()
    body = request.get_json(silent=True) or {}
    full_area_id = body.get("area_id")
    name = (body.get("name") or "").strip()
    has_floor = "floor_id" in body
    full_floor_id = body.get("floor_id") or None

    if full_area_id:
        instance_id, area_id = _split_id(full_area_id)
    else:
        # New area: infer instance from the floor, or fall back to the `instance` field.
        if full_floor_id:
            instance_id, _ = _split_id(full_floor_id)
        else:
            raw_inst = body.get("instance")
            instance_id = raw_inst if isinstance(raw_inst, str) and raw_inst else None
        area_id = None

    real_floor_id = None
    if has_floor and full_floor_id is not None:
        _, real_floor_id = _split_id(full_floor_id)
        reg = ha_registries(instance_id=instance_id)
        if not any(f.get("floor_id") == real_floor_id for f in reg.get("floors", [])):
            raise ApiError("That floor no longer exists", 400)

    if area_id:
        cmd = {"type": "config/area_registry/update", "area_id": area_id}
        if name:
            cmd["name"] = name
        if has_floor:
            cmd["floor_id"] = real_floor_id
    else:
        if not name:
            raise ApiError("An area name is required", 400)
        cmd = {"type": "config/area_registry/create", "name": name}
        if has_floor and real_floor_id is not None:
            cmd["floor_id"] = real_floor_id

    ha_ws_command(cmd, instance_id=instance_id)
    _invalidate_registries(instance_id)
    return jsonify(ok=True)
