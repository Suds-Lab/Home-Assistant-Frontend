"""Application entry point.

The application lives in core.py (assembled from config.py / errors.py and the
route blueprints in routes/). This thin module:
  * imports core, which creates the Flask app and registers the routes,
  * installs app-level middleware (the auth gate + security headers),
  * runs the dev server when executed directly.

It stays named `app.py` with an `app` object so gunicorn (`app:app`), the add-on
container, and the test harness (`import app`) all resolve the application here.
"""
import threading

from flask import g, request

import config  # live config module (single source of truth for runtime toggles)
from config import HA_URL, INGRESS_BIND, INGRESS_PORT, USER_PORT
from core import app
from errors import ApiError
from security import is_management, user_from_token

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
    unauthenticated unless it's explicitly public. The resolved user is stashed
    on flask.g so current_user() reuses it instead of validating twice. Routes
    still run their own current_user / require_admin / require_manager checks."""
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
    # Resolve once (raises 401/403 on a bad/expired token) and cache for the
    # route's current_user() so the session isn't validated twice per request.
    g.current_user = user_from_token(token)


@app.after_request
def _security_headers(resp):
    """Defense-in-depth headers. nosniff everywhere; deny framing only on the
    published user dashboard (NOT the Ingress/management port, which must stay
    framable inside Home Assistant). Framing-deny is opt-in so it can't break a
    panel_iframe embed."""
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
