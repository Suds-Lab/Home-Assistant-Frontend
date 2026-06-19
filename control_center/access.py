"""Access control and input validation.

Decides which entities a user owns (per-user assignment, the 'all'/manager
grant, and per-user expiry) and validates entity ids / timestamps before they
reach an HA URL.
"""
import re

from errors import ApiError
from security import _entity_expired_for
from store import enabled_domains, included_entities

def _domain_assignable(entity_id):
    """Whether this entity is offered/granted: within the allowed device types,
    or explicitly added to the global included-entities list."""
    allowed = enabled_domains()
    if allowed is None or entity_id.split(".")[0] in allowed:
        return True
    return entity_id in included_entities()


def user_can_access(user, entity_id):
    """Explicitly-assigned entities are always owned (any type - that's the
    per-user 'add a specific device' override). Beyond that, an 'all' user (and
    managers, who get all devices) owns every assignable entity."""
    # An admin-set per-user expiry on this entity makes it disappear for this
    # user once the date passes, even if they'd otherwise own it.
    if _entity_expired_for(user, entity_id):
        return False
    if entity_id in user.get("entities", []):
        return True
    if user.get("all") or user.get("manager"):
        return _domain_assignable(entity_id)
    return False


_ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_SAFE_TS_RE = re.compile(r"^[0-9T:.+\- Zz]+$")


def valid_entity_id(entity_id):
    """A real HA entity id is `domain.object_id`, lowercase a-z/0-9/_ only.
    Reject anything else so it can't be smuggled into an HA API URL (e.g.
    `light.x/../../config` traversing out of /api/states/)."""
    return isinstance(entity_id, str) and bool(_ENTITY_ID_RE.match(entity_id))


def _safe_ts(s):
    """Accept only timestamp-ish characters (ISO 8601 / epoch); never a path."""
    return isinstance(s, str) and bool(_SAFE_TS_RE.match(s)) and ".." not in s


def assert_owned(user, entity_id):
    """Reject any entity the logged-in user is not allowed to see/control."""
    if not valid_entity_id(entity_id):
        raise ApiError("Invalid entity id", 400)
    if not user_can_access(user, entity_id):
        raise ApiError("You do not have access to that device", 403)
