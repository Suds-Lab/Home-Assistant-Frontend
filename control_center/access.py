"""Access control and input validation.

Decides which entities a user owns (per-user assignment, the 'all'/manager
grant, and per-user expiry) and validates entity ids / timestamps before they
reach an HA URL.

Remote-instance entities are namespaced as `{instance_id}:{entity_id}` (e.g.
`garage:light.bedroom`). All access checks and validation accept both forms.
"""
import re

from config import REMOTE_INSTANCES
from errors import ApiError
from security import _entity_expired_for
from store import enabled_domains, included_entities

# Instance ids configured for remote instances. An `id` may contain any
# characters (hyphens, dots, uppercase); it is only ever used to look up a
# URL/token, never placed into an HA API URL, so it is validated by membership
# here rather than by a charset regex.
_KNOWN_INSTANCE_IDS = {r["id"] for r in REMOTE_INSTANCES}


def _real_entity_id(entity_id):
    """Strip the instance prefix to get the bare HA entity id."""
    return entity_id.split(":", 1)[1] if ":" in entity_id else entity_id


def _domain_assignable(entity_id):
    """Whether this entity is offered/granted: within the allowed device types,
    or explicitly added to the global included-entities list."""
    allowed = enabled_domains()
    domain = _real_entity_id(entity_id).split(".")[0]
    if allowed is None or domain in allowed:
        return True
    return entity_id in included_entities()


def user_can_access(user, entity_id):
    """Explicitly-assigned entities are always owned (any type - that's the
    per-user 'add a specific device' override). Beyond that, an 'all' user (and
    managers, who get all devices) owns every assignable entity."""
    if _entity_expired_for(user, entity_id):
        return False
    if entity_id in user.get("entities", []):
        return True
    if user.get("all") or user.get("manager"):
        return _domain_assignable(entity_id)
    return False


# Plain HA entity id: domain.object_id (lowercase a-z/0-9/_ only).
_ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_SAFE_TS_RE = re.compile(r"^[0-9T:.+\- Zz]+$")


def valid_entity_id(entity_id):
    """Accept plain (`light.bedroom`) and namespaced (`garage:light.bedroom`)
    entity ids. The real entity id (the only part that reaches an HA API URL) is
    checked with the strict regex; the instance prefix is accepted only if it is a
    configured remote instance. Its characters are unrestricted on purpose - an
    instance id like `ha-2` or `Cabin` must not make its entities un-controllable
    (the previous charset regex silently rejected every such remote command)."""
    if not isinstance(entity_id, str):
        return False
    if ":" in entity_id:
        inst, real = entity_id.split(":", 1)
        if inst not in _KNOWN_INSTANCE_IDS:
            return False
    else:
        real = entity_id
    return bool(_ENTITY_ID_RE.match(real))


def _safe_ts(s):
    """Accept only timestamp-ish characters (ISO 8601 / epoch); never a path."""
    return isinstance(s, str) and bool(_SAFE_TS_RE.match(s)) and ".." not in s


def assert_owned(user, entity_id):
    """Reject any entity the logged-in user is not allowed to see/control."""
    if not valid_entity_id(entity_id):
        raise ApiError("Invalid entity id", 400)
    if not user_can_access(user, entity_id):
        raise ApiError("You do not have access to that device", 403)
