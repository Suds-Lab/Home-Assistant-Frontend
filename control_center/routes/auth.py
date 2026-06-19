"""Auth routes: login, session probe, self-service password change, /me, OAuth.

All routes that establish or describe a session. Registered as a blueprint on
the app in core.py.
"""
import hmac
import secrets
import time

import jwt
import requests
from flask import Blueprint, jsonify, redirect, request
from urllib.parse import urlencode

from config import (
    OAUTH_ALLOW_ANY,
    OAUTH_ALLOWED_DOMAINS,
    OAUTH_ALLOWED_EMAILS,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_LOGO_URL,
    OAUTH_PROVIDER_NAME,
    OAUTH_REDIRECT_BASE,
    OAUTH_SCOPES,
    OAUTH_TOKEN_URL,
    OAUTH_USERINFO_URL,
    STREAM_ENABLED,
    oauth_configured,
    oauth_is_google,
)
from errors import ApiError
from security import (
    JWT_SECRET,
    _DUMMY_PW_HASH,
    _EXPIRED_MSG,
    _email_allowed,
    _is_hashed,
    _issue_token,
    _join_natural,
    _login_blocked,
    _login_clear,
    _login_key,
    _login_note_fail,
    _oauth_error_page,
    _oauth_redirect_uri,
    _password_problems,
    _password_rules,
    _user_expired,
    _user_for_email,
    current_user,
    hash_password,
    is_management,
    verify_password,
)
from store import (
    _app_image_url,
    cfg_emoji,
    cfg_name,
    cfg_providers,
    cfg_title,
    load_users,
    save_users,
)

bp = Blueprint("auth", __name__)


@bp.post("/api/login")
def login():
    if not cfg_providers()["local"]:
        raise ApiError("Password sign-in is disabled", 403)
    body = request.get_json(silent=True) or {}
    username = body.get("username")
    password = body.get("password")
    key = _login_key(username)
    if _login_blocked(key):
        raise ApiError("Too many attempts. Please wait a few minutes and try again.", 429)
    user = next((u for u in load_users() if u["username"] == username), None)
    # Always run a hash check (a dummy when the user is missing) so the response
    # time doesn't reveal whether the username exists.
    stored = user.get("password") if user else _DUMMY_PW_HASH
    if not verify_password(stored, password) or not user:
        _login_note_fail(key)
        raise ApiError("Invalid username or password", 401)
    _login_clear(key)
    if _user_expired(user):
        raise ApiError(_EXPIRED_MSG, 403, {"expired": True})
    # Lazy upgrade: if this account still had a plaintext password, hash it now.
    if not _is_hashed(user.get("password")):
        users = load_users()
        for u in users:
            if u["username"] == user["username"]:
                u["password"] = hash_password(password)
        save_users(users)
    return jsonify(
        token=_issue_token(user),
        displayName=user.get("displayName") or user["username"],
    )


@bp.post("/api/me/password")
def change_my_password():
    """Let a signed-in local user change their own password: verifies the current
    one first (throttled), then enforces the admin's complexity rules."""
    user = current_user()
    if not cfg_providers()["local"]:
        raise ApiError("Password sign-in is disabled.", 403)
    if not user.get("password"):
        raise ApiError("This account signs in with OAuth and has no password.", 400)
    body = request.get_json(silent=True) or {}
    current = body.get("current") or ""
    new = body.get("new") or ""
    key = _login_key(user["username"])
    if _login_blocked(key):
        raise ApiError("Too many attempts. Please wait a few minutes and try again.", 429)
    if not verify_password(user.get("password"), current):
        _login_note_fail(key)
        raise ApiError("Current password is incorrect.", 401)
    _login_clear(key)
    problems = _password_problems(new)
    if problems:
        raise ApiError("Password must have " + _join_natural(problems) + ".", 400)
    users = load_users()
    for u in users:
        if u["username"] == user["username"]:
            u["password"] = hash_password(new)
    save_users(users)
    return jsonify(ok=True)


@bp.get("/api/me")
def me():
    """The signed-in user's display name + role (so the dashboard can show the
    manager-only area organizer) plus avatar and password-change availability."""
    user = current_user()
    return jsonify(
        username=user["username"],
        displayName=user.get("displayName") or user["username"],
        manager=bool(user.get("manager")),
        picture=user.get("picture") or None,
        canChangePassword=bool(cfg_providers()["local"] and user.get("password")),
        passwordRules=_password_rules(),
    )


@bp.get("/api/session")
def session():
    """Tells the UI which experience to render based on the port it arrived on:
    'manage' (Ingress/sidebar) or 'user' (published dashboard)."""
    providers = cfg_providers()
    return jsonify(
        mode="manage" if is_management() else "user",
        stream=STREAM_ENABLED,
        appName=cfg_name(),   # browser tab + installed-app (PWA) name
        title=cfg_title(),    # heading shown on the login page + dashboard
        appIcon=cfg_emoji(),
        appImage=_app_image_url(),
        providers=providers,            # which login methods to show
        oauthName=OAUTH_PROVIDER_NAME,  # label for the OAuth button
        oauthIsGoogle=oauth_is_google(),  # show the Google logo
        oauthLogo=OAUTH_LOGO_URL,         # custom provider logo (non-Google)
    )


# --- OAuth sign-in (user dashboard only) ----------------------------------


@bp.get("/api/oauth/login")
def oauth_login():
    if not oauth_configured():
        return _oauth_error_page("OAuth is not configured on this server.")
    # CSRF: a short-lived signed `state` bound to a matching HttpOnly cookie, so
    # only the browser that started the flow can complete it (stops an attacker
    # pre-minting a state and logging a victim into the attacker's account).
    nonce = secrets.token_urlsafe(16)
    state = jwt.encode(
        {"n": nonce, "exp": int(time.time()) + 600},
        JWT_SECRET,
        algorithm="HS256",
    )
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(),
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    if len(OAUTH_ALLOWED_DOMAINS) == 1:
        params["hd"] = OAUTH_ALLOWED_DOMAINS[0]  # Google domain hint
    resp = redirect(f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}")
    resp.set_cookie(
        "cc_oauth_state", nonce, max_age=600, httponly=True,
        secure=True, samesite="Lax", path="/api/oauth/",
    )
    return resp


@bp.get("/api/oauth/callback")
def oauth_callback():
    if not oauth_configured():
        return _oauth_error_page("OAuth is not configured on this server.")
    if request.args.get("error"):
        return _oauth_error_page("Access was denied at the provider.")
    code = request.args.get("code")
    state = request.args.get("state")
    try:
        claims = jwt.decode(state, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return _oauth_error_page("This sign-in link expired. Please try again.")
    cookie_nonce = request.cookies.get("cc_oauth_state") or ""
    if not cookie_nonce or not hmac.compare_digest(cookie_nonce, claims.get("n") or ""):
        return _oauth_error_page("This sign-in couldn't be verified. Please start again.")
    if not code:
        return _oauth_error_page("No authorization code was returned.")

    try:
        tok = requests.post(
            OAUTH_TOKEN_URL,
            data={
                "code": code,
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "redirect_uri": _oauth_redirect_uri(),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        tok.raise_for_status()
        access_token = tok.json().get("access_token")
        info = requests.get(
            OAUTH_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        info.raise_for_status()
        profile = info.json()
    except requests.RequestException:
        return _oauth_error_page("Could not reach the identity provider.")

    email = (profile.get("email") or "").strip()
    if not email:
        return _oauth_error_page("The provider did not return an email address.")
    if not profile.get("email_verified"):
        return _oauth_error_page("Your email address is not verified by the provider.")
    if not _email_allowed(email):
        if not OAUTH_ALLOWED_DOMAINS and not OAUTH_ALLOWED_EMAILS and not OAUTH_ALLOW_ANY:
            print("OAuth sign-in refused: enabled but no allowed emails/domains are "
                  "configured (set oauth_allowed_emails / oauth_allowed_domains, or "
                  "oauth_allow_any: true).")
            return _oauth_error_page(
                "Sign-in isn't configured: no allowed emails or domains are set. "
                "Ask your administrator."
            )
        return _oauth_error_page("Your account isn't allowed to use this app.")

    user = _user_for_email(email, profile.get("name"), profile.get("picture") or "")
    if _user_expired(user):
        return _oauth_error_page(_EXPIRED_MSG, "expired")
    # Hand the session token + display name to the SPA via the URL fragment
    # (never sent to a server or written to logs); it stores and strips them.
    frag = urlencode({"oauth_token": _issue_token(user), "oauth_name": user.get("displayName") or ""})
    resp = redirect(f"{OAUTH_REDIRECT_BASE}/#{frag}")
    resp.delete_cookie("cc_oauth_state", path="/api/oauth/")
    return resp
