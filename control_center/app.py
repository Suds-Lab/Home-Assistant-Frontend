"""Application entry point.

The application itself lives in core.py (and the helper modules config.py /
errors.py, with route modules to follow). This thin module composes it:
  * imports core (which creates the Flask app and registers routes on import),
  * re-exports core's public API so tools/tests can reach it as `app.<name>`,
  * registers app-level middleware (security headers),
  * runs the dev server when executed directly.

It stays named `app.py` with an `app` object so gunicorn (`app:app`), the add-on
container, and the test harness (`import app`) all resolve the application here.
"""
import threading

import config  # live config module (single source of truth for runtime toggles)
from core import *  # noqa: F401,F403 - re-export constants/helpers; registers routes
from core import app  # the Flask instance (routes are registered when core imports)

# `import *` skips underscore-prefixed names, but tools/tests reach several of
# them via `app.<name>`, so re-export those explicitly.
from core import (  # noqa: F401
    _DUMMY_PW_HASH,
    _KNOWN_DEFAULT_SECRETS,
    _email_allowed,
    _is_hashed,
    _issue_token,
    _join_natural,
    _load_settings,
    _login_key,
    _migrate_passwords,
    _password_problems,
    _safe_ts,
    _save_settings,
)


# Endpoints reachable WITHOUT authentication, by necessity (the login page and
# the OAuth handshake run before a session exists). Everything else under /api/
# is default-deny: it needs a valid session token or the trusted management
# port. Non-/api paths are static/PWA assets and are served openly.
_PUBLIC_API = {
    "/api/login",
    "/api/session",
    "/api/oauth/login",
    "/api/oauth/callback",
    "/api/version",
}


def _request_token():
    """Bearer token from the header, or the ?token= query param (an <img>, an
    EventSource, or a WebSocket can't set an Authorization header)."""
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        return h[7:]
    return request.args.get("token")


@app.before_request
def _require_auth():
    """Default-deny gate for the API: no /api/* endpoint is reachable
    unauthenticated unless it's explicitly public. Per-route handlers still run
    their own current_user / require_admin / require_manager checks on top."""
    p = request.path
    if not p.startswith("/api/") or request.method in ("OPTIONS", "HEAD"):
        return  # static/PWA assets and preflight: not gated here
    if p in _PUBLIC_API:
        return
    if is_management():
        return  # trusted Ingress/management port (HA-authenticated admin)
    token = _request_token()
    if not token:
        raise ApiError("Not authenticated", 401)
    user_from_token(token)  # raises ApiError (401/403) on a bad/expired token


@app.after_request
def _security_headers(resp):
    """Defense-in-depth headers. nosniff everywhere; deny framing only on the
    published user dashboard (NOT the Ingress/management port, which must stay
    framable inside Home Assistant). Framing-deny is opt-in so it can't break a
    panel_iframe embed. Lives here so the test can toggle `app.BLOCK_IFRAME`."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    if config.BLOCK_IFRAME and not is_management():
        resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


if __name__ == "__main__":
    # Dev: serve both ports so each mode is reachable. In the add-on, gunicorn
    # binds both (see Dockerfile). threaded=True so SSE streams don't block.
    from werkzeug.serving import make_server

    user_srv = make_server("0.0.0.0", USER_PORT, app, threaded=True)
    threading.Thread(target=user_srv.serve_forever, daemon=True).start()
    print(f"Control Center -> HA at {HA_URL}")
    print(f"  management (admin):   http://{INGRESS_BIND}:{INGRESS_PORT}")
    print(f"  user dashboard:       http://0.0.0.0:{USER_PORT}")
    make_server(INGRESS_BIND, INGRESS_PORT, app, threaded=True).serve_forever()
