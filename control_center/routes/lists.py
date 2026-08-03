"""Per-user device lists (user-created tags / filters).

Any authenticated user manages their own lists over their own devices:
  GET    /api/lists          - this user's lists
  POST   /api/lists          - create a list {name}
  PUT    /api/lists/<id>     - rename / set members {name?, entities?}
  DELETE /api/lists/<id>     - delete a list

Lists are per-user (keyed by username), so a user only ever sees or edits their
own. Members are validated against the devices the user actually owns.
"""
import uuid

from flask import Blueprint, jsonify, request

from access import user_can_access, valid_entity_id
from errors import ApiError
from security import current_user
from store import load_lists, save_lists

bp = Blueprint("lists", __name__)

_MAX_NAME = 60


def _clean_name(raw):
    name = str(raw or "").strip()[:_MAX_NAME]
    if not name:
        raise ApiError("A list name is required", 400)
    return name


def _clean_entities(raw, user):
    """Keep only valid entity ids the user actually owns (dedup, order-preserving)."""
    if not isinstance(raw, list):
        return []
    seen = set()
    out = []
    for e in raw:
        if (
            isinstance(e, str)
            and e not in seen
            and valid_entity_id(e)
            and user_can_access(user, e)
        ):
            seen.add(e)
            out.append(e)
    return out


@bp.get("/api/lists")
def get_lists():
    username = current_user()["username"]
    return jsonify(load_lists().get(username, []))


@bp.post("/api/lists")
def create_list():
    user = current_user()
    body = request.get_json(force=True) or {}
    new = {
        "id": str(uuid.uuid4()),
        "name": _clean_name(body.get("name")),
        "entities": _clean_entities(body.get("entities", []), user),
    }
    all_lists = load_lists()
    all_lists.setdefault(user["username"], []).append(new)
    save_lists(all_lists)
    return jsonify(new), 201


@bp.put("/api/lists/<list_id>")
def update_list(list_id):
    user = current_user()
    body = request.get_json(force=True) or {}
    all_lists = load_lists()
    user_lists = all_lists.get(user["username"], [])
    lst = next((item for item in user_lists if item.get("id") == list_id), None)
    if lst is None:
        raise ApiError("List not found", 404)
    if "name" in body:
        lst["name"] = _clean_name(body["name"])
    if "entities" in body:
        lst["entities"] = _clean_entities(body["entities"], user)
    save_lists(all_lists)
    return jsonify(lst)


@bp.delete("/api/lists/<list_id>")
def delete_list(list_id):
    username = current_user()["username"]
    all_lists = load_lists()
    user_lists = all_lists.get(username, [])
    remaining = [item for item in user_lists if item.get("id") != list_id]
    if len(remaining) == len(user_lists):
        raise ApiError("List not found", 404)
    all_lists[username] = remaining
    save_lists(all_lists)
    return jsonify({"ok": True})
