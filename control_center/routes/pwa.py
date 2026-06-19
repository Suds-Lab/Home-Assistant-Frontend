"""PWA / static-client routes.

Serves the web-app manifest (with the configured name + optional custom icon),
the app icon, the version probe (for new-build detection), and index.html with
the SPA fallback. Registered as a blueprint on the app in core.py.
"""
import json
import mimetypes

from flask import Blueprint, Response, jsonify, send_from_directory

from config import APP_VERSION, STATIC_DIR
from store import _app_image_url, _find_icon, cfg_name

bp = Blueprint("pwa", __name__)


@bp.get("/manifest.webmanifest")
def manifest():
    """Serve the PWA manifest with the configured app name (and custom icon, if
    set), so the installed home-screen app uses them too."""
    data = json.loads((STATIC_DIR / "manifest.webmanifest").read_text())
    data["name"] = cfg_name()
    data["short_name"] = cfg_name()
    icon = _find_icon()
    if icon:
        src = _app_image_url()
        icon_type = mimetypes.guess_type(icon.name)[0] or "image/png"
        data["icons"] = [
            {"src": src, "sizes": "192x192", "type": icon_type, "purpose": "any"},
            {"src": src, "sizes": "512x512", "type": icon_type, "purpose": "any"},
            {"src": src, "sizes": "512x512", "type": icon_type, "purpose": "maskable"},
        ]
    return Response(json.dumps(data), mimetype="application/manifest+json")


@bp.get("/app-icon")
def app_icon():
    """The custom uploaded PWA/home-screen icon, or the bundled default."""
    icon = _find_icon()
    if icon:
        return send_from_directory(icon.parent, icon.name)
    return send_from_directory(STATIC_DIR / "icons", "icon-512.png")


def _serve_index():
    """index.html with the current version stamped into the asset URLs (?v=) so
    each release busts the browser/service-worker cache. Served no-cache so the
    page itself is always fresh and can point at the right asset versions."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__APP_VERSION__", APP_VERSION)
    return Response(html, mimetype="text/html",
                    headers={"Cache-Control": "no-cache"})


@bp.get("/api/version")
def api_version():
    """The running build version, so an open page can detect a new deploy and
    reload itself (no manual refresh needed). Public - it's just a string."""
    return jsonify(version=APP_VERSION)


@bp.get("/")
def index():
    return _serve_index()


@bp.get("/<path:filename>")
def static_files(filename):
    target = STATIC_DIR / filename
    if target.is_file():
        return send_from_directory(STATIC_DIR, filename)
    # SPA fallback: anything that isn't a real file returns index.html.
    return _serve_index()
