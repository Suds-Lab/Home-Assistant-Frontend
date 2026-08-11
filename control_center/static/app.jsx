// Control Center - React UI loaded from a CDN and transformed in the browser by
// Babel (no npm, no build step). All components live in this one file.

const { useState, useEffect, useCallback, useRef } = React;

// --- API client ----------------------------------------------------------

const TOKEN_KEY = 'ha_app_token';
const NAME_KEY = 'ha_app_name';
const COMPACT_KEY = 'ha_app_compact';
const GROUPBY_KEY = 'ha_app_groupby'; // 'type' | 'area' | 'floor'
const COLLAPSED_KEY = 'ha_app_collapsed';
const OPEN_AREAS_KEY = 'ha_app_open_areas'; // areas under a floor are collapsed by default
const GROUPING_THRESHOLD = 8; // show grouping controls once a user has this many devices

// Resolve the API base relative to the current document so it works in dev
// and behind Home Assistant Ingress (where the app sits under a /…/ prefix).
const API_BASE = new URL('api', document.baseURI).href;

const getToken = () => localStorage.getItem(TOKEN_KEY);
function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request(path, options = {}) {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  // Rolling session: the server hands back a refreshed token once ours is past
  // halfway through its life, so active users stay signed in. Store it for the
  // next request.
  const refreshed = res.headers.get('X-Session-Token');
  if (refreshed && refreshed !== token) setToken(refreshed);
  if (res.status === 401) {
    const body = await res.json().catch(() => ({}));
    // A 401 with an active token = the session expired. Signal the app to
    // log out silently (no error banner) and return a promise that never
    // resolves so the caller's await hangs until the component unmounts.
    if (token) {
      setToken(null);
      window.dispatchEvent(new Event('auth:logout'));
      return new Promise(() => {});
    }
    throw new Error(body.error || 'Wrong username or password');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // Account/entity expiry: clear an active session so the user is cut off
    // immediately, and tag the error so the UI can show the expiry prompt.
    if (data.expired) {
      if (token) setToken(null);
      const e = new Error(data.error || 'Your account has expired.');
      e.expired = true;
      throw e;
    }
    throw new Error(data.error || 'Request failed');
  }
  return data;
}

// Subtle tap haptics on press-down (which is what feels native), cross-platform:
//  * Android / anything with the Vibration API -> navigator.vibrate.
//  * iOS Safari (no Vibration API) -> toggle a hidden <input type="checkbox"
//    switch> via a label click, which makes the system play its switch haptic.
//    Technique from web-haptics (https://haptics.lochie.me). No libraries/build.
// Throttled so a held button / slider drag can't turn into a continuous buzz,
// and a graceful no-op on desktop or where neither path works.
const HAPTICS_KEY = 'ha_haptics'; // set to '0' to disable
const _coarsePointer =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(pointer: coarse)').matches
    : false;
let _lastHaptic = 0;
// A persistent, rendered-but-invisible <input type=checkbox switch> in the body.
// Toggling it makes iOS Safari (17.4+) play its switch haptic. It must live in
// the body (not head) and stay in the DOM - removing it cancels the haptic.
let _iosSwitch = null;
function _iosHapticEl() {
  if (_iosSwitch || typeof document === 'undefined' || !document.body) return _iosSwitch;
  // Mirror the reference web-haptics setup exactly (that's what actually fires
  // on iOS): a body-mounted <label for=id> wrapping <input type=checkbox switch>
  // with all:initial then appearance:auto, both display:none. Clicking the label
  // toggles the switch, which makes iOS play its switch haptic.
  const id = 'ha-haptic-switch';
  const label = document.createElement('label');
  label.setAttribute('for', id);
  label.setAttribute('aria-hidden', 'true');
  label.textContent = 'Haptic feedback';
  label.style.display = 'none';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.id = id;
  input.setAttribute('switch', '');
  input.style.all = 'initial';
  input.style.appearance = 'auto';
  input.style.display = 'none';
  label.appendChild(input);
  document.body.appendChild(label);
  _iosSwitch = label;
  return _iosSwitch;
}
function haptic(ms = 10) {
  try {
    if (localStorage.getItem(HAPTICS_KEY) === '0') return;
    const now = Date.now();
    if (now - _lastHaptic < 25) return; // never a drone
    _lastHaptic = now;
    if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') {
      navigator.vibrate(ms); // Android et al.
      return;
    }
    if (!_coarsePointer) return; // skip desktop
    const el = _iosHapticEl(); // iOS: toggle the switch to fire the system haptic
    if (el) el.click();
  } catch {
    /* ignore */
  }
}
let _hapticsBound = false;
function bindHaptics() {
  if (_hapticsBound || typeof document === 'undefined') return;
  _hapticsBound = true;
  // Fire on `click`, not `pointerdown`: iOS only plays the switch haptic when
  // the toggle happens inside a real click/tap (a passive pointerdown doesn't
  // count). Android's vibrate is fine here too. Sliders haptic per step via
  // their own onChange (works on Android; iOS has no per-step click).
  //
  // Capture phase (the `true`): some buttons (climate modes, etc.) disable
  // themselves the instant they're clicked, and a bubble-phase listener runs
  // after React has flipped them to disabled - so it would skip the haptic. In
  // capture we run before that, while the button is still enabled.
  document.addEventListener(
    'click',
    (e) => {
      const t = e.target;
      if (!t || !t.closest) return;
      const el = t.closest('button');
      if (el && !el.disabled) haptic();
    },
    true
  );
}
bindHaptics();

const login = (username, password) =>
  request('/login', { method: 'POST', body: JSON.stringify({ username, password }) });
const getDevices = () => request('/devices');
const getMe = () => request('/me');
const changeMyPassword = (body) =>
  request('/me/password', { method: 'POST', body: JSON.stringify(body) });
const getServerVersion = () => request('/version'); // for auto-reload on new deploys
const getSession = () => request('/session');
// Manager: organize HA devices into areas.
const managerGetDevices = () => request('/manager/devices');
const managerUpdateDevice = (device_id, fields) =>
  request('/manager/device', { method: 'POST', body: JSON.stringify({ device_id, ...fields }) });
const managerGetAreas = () => request('/manager/areas');
const managerSaveArea = (fields) =>
  request('/manager/area', { method: 'POST', body: JSON.stringify(fields) });
const getMdiIcon = (name) => request(`/icon/mdi/${encodeURIComponent(name)}`);
const getDevice = (entity_id) => request(`/entity/${encodeURIComponent(entity_id)}`);
// Call a whitelisted service on an entity (backend enforces what's allowed).
const control = (entity_id, service, data = {}) =>
  request('/control', { method: 'POST', body: JSON.stringify({ entity_id, service, data }) });

// Admin (management screen, reached via the HA sidebar / Ingress).
const adminGetUsers = () => request('/admin/users');
const adminGetEntities = () => request('/admin/entities');
const adminGetAllEntities = () => request('/admin/entities?all=1'); // unfiltered, for the include picker
const adminSaveUser = (user) =>
  request('/admin/users', { method: 'POST', body: JSON.stringify(user) });
const adminDeleteUser = (username) =>
  request(`/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
const adminUploadIcon = async (file) => {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(new URL('api/admin/icon', document.baseURI), { method: 'POST', body: fd });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Upload failed');
};
const adminClearIcon = async () => {
  const r = await fetch(new URL('api/admin/icon', document.baseURI), { method: 'DELETE' });
  if (!r.ok) throw new Error('Failed to remove icon');
};
const adminGetDeviceTypes = () => request('/admin/device-types');
const adminSetDeviceTypes = (types) =>
  request('/admin/device-types', { method: 'POST', body: JSON.stringify({ types }) });
const adminGetSettings = () => request('/admin/settings');
const adminGetRemoteStatus = () => request('/admin/remote-status');
const adminSetSettings = (s) =>
  request('/admin/settings', { method: 'POST', body: JSON.stringify(s) });
const adminGetActivity = (limit = 200) => request(`/admin/activity?limit=${limit}`);
const adminClearActivity = () => request('/admin/activity', { method: 'DELETE' });

// Climate scheduling (user-facing)
const getMySchedules = () => request('/schedules');
const createSchedule = (fields) => request('/schedules', { method: 'POST', body: JSON.stringify(fields) });
const updateSchedule = (id, patch) => request(`/schedules/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(patch) });
const deleteSchedule = (id) => request(`/schedules/${encodeURIComponent(id)}`, { method: 'DELETE' });
const getScheduleEntities = () => request('/schedule-entities');
// Per-user device lists (tags / filters)
const getLists = () => request('/lists');
const createList = (fields) => request('/lists', { method: 'POST', body: JSON.stringify(fields) });
const updateList = (id, patch) => request(`/lists/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(patch) });
const deleteList = (id) => request(`/lists/${encodeURIComponent(id)}`, { method: 'DELETE' });
// Climate scheduling (admin)
const adminGetSchedulePerms = () => request('/admin/schedule-perms');
const adminSetSchedulePerms = (username, entity_ids) =>
  request('/admin/schedule-perms', { method: 'POST', body: JSON.stringify({ username, entity_ids }) });
const adminGetAllSchedules = () => request('/admin/schedules');
const adminGetClimateEntities = () => request('/admin/climate-entities');
const adminPatchSchedule = (id, patch) =>
  request(`/admin/schedules/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) });
const adminDeleteSchedule = (id) =>
  request(`/admin/schedules/${encodeURIComponent(id)}`, { method: 'DELETE' });

// Live pull of Home Assistant's own logbook for a range (never stored by us).
const adminHaLogbook = (startISO, endISO, entity) => {
  const p = new URLSearchParams({ start: startISO });
  if (endISO) p.set('end', endISO);
  if (entity) p.set('entity', entity);
  return request(`/admin/ha-logbook?${p.toString()}`);
};
// Live pull of one entity's state history for the chart.
const adminHaHistory = (entity, startISO, endISO) => {
  const p = new URLSearchParams({ entity, start: startISO });
  if (endISO) p.set('end', endISO);
  return request(`/admin/ha-history?${p.toString()}`);
};

// --- Components -----------------------------------------------------------

function GoogleLogo() {
  return (
    <svg className="oauth-logo" width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

function OAuthLogo({ oauth }) {
  const [imgFailed, setImgFailed] = useState(false);
  if (oauth.isGoogle) return <GoogleLogo />;
  if (oauth.logo && !imgFailed) {
    return (
      <img
        className="oauth-logo"
        width="18"
        height="18"
        src={oauth.logo}
        alt=""
        onError={() => setImgFailed(true)}
      />
    );
  }
  // Generic fallback: no logo configured, or the configured one failed to load.
  return (
    <svg className="oauth-logo" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 17a2 2 0 0 0 2-2 2 2 0 0 0-2-2 2 2 0 0 0-2 2 2 2 0 0 0 2 2m6-9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2h1V6a5 5 0 0 1 5-5 5 5 0 0 1 5 5v2h-1m-6 0h6V6a3 3 0 0 0-3-3 3 3 0 0 0-3 3v2z"
      />
    </svg>
  );
}

// Brand glyph shown beside the title (and on the install prompt). Precedence:
// an uploaded logo image wins (same image used for the tab/PWA icon), then a
// custom emoji set in Settings, then the default Control Center logo.
function BrandIcon({ icon, image, className = 'app-icon' }) {
  if (image) return <img className={className} src={image} alt="" />;
  return icon ? (
    <span className={className}>{icon}</span>
  ) : (
    <img className={className} src="./icons/icon.svg" alt="" />
  );
}

// Account-expiry badge for the user list: amber "Expires <date>" when a future
// expiry is set, red "Expired <date>" once it has passed, nothing otherwise.
// The date is 'YYYY-MM-DD', valid through that day (expired the day after).
function ExpiryBadge({ expires }) {
  if (!expires) return null;
  const t = new Date();
  const todayStr = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(
    t.getDate()
  ).padStart(2, '0')}`;
  const expired = expires < todayStr;
  const d = new Date(`${expires}T00:00:00`);
  const label = isNaN(d)
    ? expires
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  return expired ? (
    <span className="badge badge-expired">Expired {label}</span>
  ) : (
    <span className="badge badge-expires">Expires {label}</span>
  );
}

function Login({
  onLogin,
  title = 'Control Center',
  appIcon = '',
  appImage = null,
  providers = { local: true, oauth: false },
  oauth = { name: 'OAuth', isGoogle: false, logo: '' },
  notice = '',
  authError = '',
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  // `error` is a generic sign-in error shown above the form (works for OAuth-
  // only setups too); a password attempt below also surfaces here.
  const [error, setError] = useState(authError || '');
  // An expired-account message: carried over from a cut-off session or an OAuth
  // redirect (the `notice` prop), or raised by a fresh login attempt below.
  const [expired, setExpired] = useState(notice || '');
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError('');
    setExpired('');
    setBusy(true);
    try {
      const { token, displayName } = await login(username, password);
      onLogin(token, displayName);
    } catch (err) {
      if (err.expired) setExpired(err.message);
      else setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function oauthSignIn() {
    // Top-level navigation: the provider redirects back to /api/oauth/callback.
    window.location.href = new URL('api/oauth/login', document.baseURI).href;
  }

  return (
    <div className="centered">
      <div className="card login">
        <h1><BrandIcon icon={appIcon} image={appImage} /> {title}</h1>
        <p className="muted">Sign in to control your lights and AC</p>

        {expired && (
          <div className="expired-notice" role="alert">
            {expired === 'session' ? (
              <>
                <strong>Your session has ended.</strong>
                <span>Please log in again, or contact your system administrator if this keeps happening.</span>
              </>
            ) : (
              <>
                <strong>Your account has expired.</strong>
                <span>Please contact the system administrator for help.</span>
              </>
            )}
          </div>
        )}

        {error && !expired && <div className="error" role="alert">{error}</div>}

        {providers.local && (
          <form onSubmit={submit}>
            <label>
              Username
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <button type="submit" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        )}

        {providers.local && providers.oauth && <div className="or-sep">or</div>}

        {providers.oauth && (
          <button type="button" className="btn-oauth" onClick={oauthSignIn}>
            <OAuthLogo oauth={oauth} />
            <span>Sign in with {oauth.name}</span>
          </button>
        )}
      </div>
    </div>
  );
}

const DOMAIN_LABELS = {
  light: 'Lights',
  switch: 'Switches',
  input_boolean: 'Toggles',
  climate: 'Climate',
  fan: 'Fans',
  cover: 'Covers',
  lock: 'Locks',
  media_player: 'Media players',
  scene: 'Scenes',
  script: 'Scripts',
  automation: 'Automations',
  button: 'Buttons',
  input_button: 'Buttons',
  vacuum: 'Vacuums',
  sensor: 'Sensors',
  binary_sensor: 'Sensors',
};

const domainLabel = (d) =>
  DOMAIN_LABELS[d] || d.charAt(0).toUpperCase() + d.slice(1).replace(/_/g, ' ');

// A search input with a clear (✕) button that appears once there's text.
function SearchBox({ value, onChange, placeholder, className = '', ...rest }) {
  return (
    <div className={`search-wrap ${className}`}>
      <input
        type="search"
        className="search"
        value={value}
        placeholder={placeholder}
        onChange={onChange}
        {...rest}
      />
      {value && (
        <button
          type="button"
          className="search-clear"
          aria-label="Clear search"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onChange({ target: { value: '' } })}
        >
          ✕
        </button>
      )}
    </div>
  );
}

// Material Design Icons (the icon set Home Assistant uses), inlined as SVG
// path data so there's no font/CDN dependency.
const DOMAIN_MDI = {
  light: 'M12,2A7,7 0 0,0 5,9C5,11.38 6.19,13.47 8,14.74V17A1,1 0 0,0 9,18H15A1,1 0 0,0 16,17V14.74C17.81,13.47 19,11.38 19,9A7,7 0 0,0 12,2M9,21A1,1 0 0,0 10,22H14A1,1 0 0,0 15,21V20H9V21Z',
  switch: 'M8 6V18H16V6H8M14 10H10V8H14V10M19.4 1.6C19 1.2 18.5 1 18 1H6C5.5 1 5 1.2 4.6 1.6C4.2 2 4 2.5 4 3V21C4 21.5 4.2 22 4.6 22.4C5 22.8 5.5 23 6 23H18C18.5 23 19 22.8 19.4 22.4C19.8 22 20 21.5 20 21V3C20 2.5 19.8 2 19.4 1.6M18 21H6V3H18V21Z',
  input_boolean: 'M17 6H7C3.69 6 1 8.69 1 12S3.69 18 7 18H17C20.31 18 23 15.31 23 12S20.31 6 17 6M17 16H7C4.79 16 3 14.21 3 12S4.79 8 7 8H17C19.21 8 21 9.79 21 12S19.21 16 17 16M17 9C15.34 9 14 10.34 14 12S15.34 15 17 15 20 13.66 20 12 18.66 9 17 9Z',
  climate: 'M16.95,16.95L14.83,14.83C15.55,14.1 16,13.1 16,12C16,11.26 15.79,10.57 15.43,10L17.6,7.81C18.5,9 19,10.43 19,12C19,13.93 18.22,15.68 16.95,16.95M12,5C13.57,5 15,5.5 16.19,6.4L14,8.56C13.43,8.21 12.74,8 12,8A4,4 0 0,0 8,12C8,13.1 8.45,14.1 9.17,14.83L7.05,16.95C5.78,15.68 5,13.93 5,12A7,7 0 0,1 12,5M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12C22,6.47 17.5,2 12,2Z',
  fan: 'M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.88,14.34C15.87,18.03 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.96,15.85 2,15.07 2,11.5C2,7 5.56,6.89 6.74,9.26C7.24,10.25 8.29,10.68 9.22,10.87C9.41,10.39 9.73,9.97 10.14,9.65C8.15,5.96 8.94,2 12.5,2Z',
  cover: 'M3 4H21V8H19V20H17V8H7V20H5V8H3V4M8 9H16V11H8V9M8 12H16V14H8V12M8 15H16V17H8V15M8 18H16V20H8V18Z',
  lock: 'M12,17A2,2 0 0,0 14,15C14,13.89 13.1,13 12,13A2,2 0 0,0 10,15A2,2 0 0,0 12,17M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10C4,8.89 4.9,8 6,8H7V6A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18M12,3A3,3 0 0,0 9,6V8H15V6A3,3 0 0,0 12,3Z',
  media_player: 'M12,12A3,3 0 0,0 9,15A3,3 0 0,0 12,18A3,3 0 0,0 15,15A3,3 0 0,0 12,12M12,20A5,5 0 0,1 7,15A5,5 0 0,1 12,10A5,5 0 0,1 17,15A5,5 0 0,1 12,20M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8C10.89,8 10,7.1 10,6C10,4.89 10.89,4 12,4M17,2H7C5.89,2 5,2.89 5,4V20A2,2 0 0,0 7,22H17A2,2 0 0,0 19,20V4C19,2.89 18.1,2 17,2Z',
  scene: 'M20.84 2.18L16.91 2.96L19.65 6.5L21.62 6.1L20.84 2.18M13.97 3.54L12 3.93L14.75 7.46L16.71 7.07L13.97 3.54M9.07 4.5L7.1 4.91L9.85 8.44L11.81 8.05L9.07 4.5M4.16 5.5L3.18 5.69A2 2 0 0 0 1.61 8.04L2 10L6.9 9.03L4.16 5.5M2 10V20C2 21.11 2.9 22 4 22H20C21.11 22 22 21.11 22 20V10H2Z',
  script: 'M17.8,20C17.4,21.2 16.3,22 15,22H5C3.3,22 2,20.7 2,19V18H5L14.2,18C14.6,19.2 15.7,20 17,20H17.8M19,2C20.7,2 22,3.3 22,5V6H20V5C20,4.4 19.6,4 19,4C18.4,4 18,4.4 18,5V18H17C16.4,18 16,17.6 16,17V16H5V5C5,3.3 6.3,2 8,2H19M8,6V8H15V6H8M8,10V12H14V10H8Z',
  automation: 'M12,2A2,2 0 0,1 14,4C14,4.74 13.6,5.39 13,5.73V7H14A7,7 0 0,1 21,14H22A1,1 0 0,1 23,15V18A1,1 0 0,1 22,19H21V20A2,2 0 0,1 19,22H5A2,2 0 0,1 3,20V19H2A1,1 0 0,1 1,18V15A1,1 0 0,1 2,14H3A7,7 0 0,1 10,7H11V5.73C10.4,5.39 10,4.74 10,4A2,2 0 0,1 12,2M7.5,13A2.5,2.5 0 0,0 5,15.5A2.5,2.5 0 0,0 7.5,18A2.5,2.5 0 0,0 10,15.5A2.5,2.5 0 0,0 7.5,13M16.5,13A2.5,2.5 0 0,0 14,15.5A2.5,2.5 0 0,0 16.5,18A2.5,2.5 0 0,0 19,15.5A2.5,2.5 0 0,0 16.5,13Z',
  button: 'M13 5C15.21 5 17 6.79 17 9C17 10.5 16.2 11.77 15 12.46V11.24C15.61 10.69 16 9.89 16 9C16 7.34 14.66 6 13 6S10 7.34 10 9C10 9.89 10.39 10.69 11 11.24V12.46C9.8 11.77 9 10.5 9 9C9 6.79 10.79 5 13 5M20 20.5C19.97 21.32 19.32 21.97 18.5 22H13C12.62 22 12.26 21.85 12 21.57L8 17.37L8.74 16.6C8.93 16.39 9.2 16.28 9.5 16.28H9.7L12 18V9C12 8.45 12.45 8 13 8S14 8.45 14 9V13.47L15.21 13.6L19.15 15.79C19.68 16.03 20 16.56 20 17.14V20.5M20 2H4C2.9 2 2 2.9 2 4V12C2 13.11 2.9 14 4 14H8V12L4 12L4 4H20L20 12H18V14H20V13.96L20.04 14C21.13 14 22 13.09 22 12V4C22 2.9 21.11 2 20 2Z',
  vacuum: 'M12,2C14.65,2 17.19,3.06 19.07,4.93L17.65,6.35C16.15,4.85 14.12,4 12,4C9.88,4 7.84,4.84 6.35,6.35L4.93,4.93C6.81,3.06 9.35,2 12,2M3.66,6.5L5.11,7.94C4.39,9.17 4,10.57 4,12A8,8 0 0,0 12,20A8,8 0 0,0 20,12C20,10.57 19.61,9.17 18.88,7.94L20.34,6.5C21.42,8.12 22,10.04 22,12A10,10 0 0,1 12,22A10,10 0 0,1 2,12C2,10.04 2.58,8.12 3.66,6.5M12,6A6,6 0 0,1 18,12C18,13.59 17.37,15.12 16.24,16.24L14.83,14.83C14.08,15.58 13.06,16 12,16C10.94,16 9.92,15.58 9.17,14.83L7.76,16.24C6.63,15.12 6,13.59 6,12A6,6 0 0,1 12,6M12,8A1,1 0 0,0 11,9A1,1 0 0,0 12,10A1,1 0 0,0 13,9A1,1 0 0,0 12,8Z',
  sensor: 'M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,4A8,8 0 0,1 20,12C20,14.4 19,16.5 17.3,18C15.9,16.7 14,16 12,16C10,16 8.2,16.7 6.7,18C5,16.5 4,14.4 4,12A8,8 0 0,1 12,4M14,5.89C13.62,5.9 13.26,6.15 13.1,6.54L11.81,9.77L11.71,10C11,10.13 10.41,10.6 10.14,11.26C9.73,12.29 10.23,13.45 11.26,13.86C12.29,14.27 13.45,13.77 13.86,12.74C14.12,12.08 14,11.32 13.57,10.76L13.67,10.5L14.96,7.29L14.97,7.26C15.17,6.75 14.92,6.17 14.41,5.96C14.28,5.91 14.15,5.89 14,5.89M10,6A1,1 0 0,0 9,7A1,1 0 0,0 10,8A1,1 0 0,0 11,7A1,1 0 0,0 10,6M7,9A1,1 0 0,0 6,10A1,1 0 0,0 7,11A1,1 0 0,0 8,10A1,1 0 0,0 7,9M17,9A1,1 0 0,0 16,10A1,1 0 0,0 17,11A1,1 0 0,0 18,10A1,1 0 0,0 17,9Z',
  binary_sensor: 'M10,0.2C9,0.2 8.2,1 8.2,2C8.2,3 9,3.8 10,3.8C11,3.8 11.8,3 11.8,2C11.8,1 11,0.2 10,0.2M15.67,1A7.33,7.33 0 0,0 23,8.33V7A6,6 0 0,1 17,1H15.67M18.33,1C18.33,3.58 20.42,5.67 23,5.67V4.33C21.16,4.33 19.67,2.84 19.67,1H18.33M21,1A2,2 0 0,0 23,3V1H21M7.92,4.03C7.75,4.03 7.58,4.06 7.42,4.11L2,5.8V11H3.8V7.33L5.91,6.67L2,22H3.8L6.67,13.89L9,17V22H10.8V15.59L8.31,11.05L9.04,8.18L10.12,10H15V8.2H11.38L9.38,4.87C9.08,4.37 8.54,4.03 7.92,4.03Z',
  weather: 'M12.74,5.47C15.1,6.5 16.35,9.03 15.92,11.46C17.19,12.56 18,14.19 18,16V16.17C18.31,16.06 18.65,16 19,16A3,3 0 0,1 22,19A3,3 0 0,1 19,22H6A4,4 0 0,1 2,18A4,4 0 0,1 6,14H6.27C5,12.45 4.6,10.24 5.5,8.26C6.72,5.5 9.97,4.24 12.74,5.47M11.93,7.3C10.16,6.5 8.09,7.31 7.31,9.07C6.85,10.09 6.93,11.22 7.41,12.13C8.5,10.83 10.16,10 12,10C12.7,10 13.38,10.12 14,10.34C13.94,9.06 13.18,7.86 11.93,7.3M19,18H16V16A4,4 0 0,0 12,12A4,4 0 0,0 8,16H6A2,2 0 0,0 4,18A2,2 0 0,0 6,20H19A1,1 0 0,0 20,19A1,1 0 0,0 19,18Z',
  number: 'M4,17V9H2V7H6V17H4M22,15C22,16.11 21.1,17 20,17H16V15H20V13H18V11H20V9H16V7H20A2,2 0 0,1 22,9V10.5A1.5,1.5 0 0,1 20.5,12A1.5,1.5 0 0,1 22,13.5V15M14,15V17H8V13C8,11.89 8.9,11 10,11H12V9H8V7H12A2,2 0 0,1 14,9V11C14,12.11 13.1,13 12,13H10V15H14Z',
  select: 'M15 5H18L16.5 7L15 5M5 2H19C20.11 2 21 2.9 21 4V20C21 21.11 20.11 22 19 22H5C3.9 22 3 21.11 3 20V4C3 2.9 3.9 2 5 2M5 4V8H19V4H5M5 20H19V10H5V20M7 12H17V14H7V12M7 16H17V18H7V16Z',
  camera: 'M4,4H7L9,2H15L17,4H20A2,2 0 0,1 22,6V18A2,2 0 0,1 20,20H4A2,2 0 0,1 2,18V6A2,2 0 0,1 4,4M12,7A5,5 0 0,0 7,12A5,5 0 0,0 12,17A5,5 0 0,0 17,12A5,5 0 0,0 12,7M12,9A3,3 0 0,1 15,12A3,3 0 0,1 12,15A3,3 0 0,1 9,12A3,3 0 0,1 12,9Z',
  person: 'M12,4A4,4 0 0,1 16,8A4,4 0 0,1 12,12A4,4 0 0,1 8,8A4,4 0 0,1 12,4M12,14C16.42,14 20,15.79 20,18V20H4V18C4,15.79 7.58,14 12,14Z',
};
DOMAIN_MDI.input_button = DOMAIN_MDI.button;
const MDI_DEFAULT =
  'M3 6H21V4H3C1.9 4 1 4.9 1 6V18C1 19.1 1.9 20 3 20H7V18H3V6M13 12H9V13.78C8.39 14.33 8 15.11 8 16C8 16.89 8.39 17.67 9 18.22V20H13V18.22C13.61 17.67 14 16.88 14 16S13.61 14.33 13 13.78V12M11 17.5C10.17 17.5 9.5 16.83 9.5 16S10.17 14.5 11 14.5 12.5 15.17 12.5 16 11.83 17.5 11 17.5M22 8H16C15.5 8 15 8.5 15 9V19C15 19.5 15.5 20 16 20H22C22.5 20 23 19.5 23 19V9C23 8.5 22.5 8 22 8M21 18H17V10H21V18Z';

function DomainIcon({ domain }) {
  return (
    <svg className="mdi-icon" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
      <path d={DOMAIN_MDI[domain] || MDI_DEFAULT} fill="currentColor" />
    </svg>
  );
}

// Make raw HA values (hvac/fan modes) readable: "fan_only" -> "fan only".
const humanize = (s) => String(s == null ? '' : s).replace(/_/g, ' ');
// Split camelCase too, so "lowMedium" / "mediumHigh" read as "low medium" etc.
const prettyMode = (s) => humanize(String(s == null ? '' : s).replace(/([a-z])([A-Z])/g, '$1 $2'));

// Ordered ladder for climate fan_mode controls that are named speeds
// (low..high). Modes not on the ladder (e.g. "auto") are kept as separate
// buttons; "night" is hidden entirely. Used to render an ordered speed slider.
const FAN_SPEED_RANK = {
  silent: 0, quiet: 0, sleep: 0, min: 0, minimum: 0,
  low: 1,
  lowmedium: 2, lowmed: 2,
  medium: 3, med: 3, normal: 3,
  mediumhigh: 4, medhigh: 4,
  high: 5,
  max: 6, maximum: 6, strong: 6, powerful: 7, turbo: 7,
};
const FAN_HIDE = new Set(['night']);
const fanKey = (m) => String(m).toLowerCase().replace(/[\s_-]/g, '');
// Some units report fan speeds as plain numbers ("1".."5") instead of named
// steps - treat those as an ordered ladder too.
const isNumericMode = (m) => /^-?\d+(?:\.\d+)?$/.test(String(m).trim());

// States that should highlight a card as "active".
const ACTIVE_STATES = new Set([
  'on', 'open', 'playing', 'home', 'cleaning', 'unlocked',
  'heat', 'cool', 'heat_cool', 'auto', 'dry', 'fan_only',
]);

function fmtValue(v) {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function prettyState(device) {
  const unit = device.attributes && device.attributes.unit_of_measurement;
  const s = device.state ?? 'unknown';
  return unit ? `${s} ${unit}` : s;
}

function Toggle({ on, onClick, disabled }) {
  return (
    <button
      className={`switch ${on ? 'on' : ''}`}
      onClick={onClick}
      disabled={disabled}
      aria-label={on ? 'Turn off' : 'Turn on'}
    >
      <span className="knob" />
    </button>
  );
}

// One card for any entity, with controls tailored to its domain.
function DeviceCard({ device, onChange, onEdit, onError }) {
  const [busy, setBusy] = useState(false);
  const a = device.attributes || {};
  const [pct, setPct] = useState(
    a.brightness != null
      ? Math.round((a.brightness / 255) * 100)
      : a.percentage != null
      ? a.percentage
      : a.current_position != null
      ? a.current_position
      : 100
  );
  const [target, setTarget] = useState(a.temperature ?? 22);
  const targetRef = useRef(a.temperature ?? 22); // latest target across fast taps
  const [vol, setVol] = useState(a.volume_level != null ? Math.round(a.volume_level * 100) : 50);

  // Optimistic state: reflect the command instantly instead of waiting for the
  // 5s poll. `freezeUntil` ignores incoming poll updates briefly so a stale
  // read from HA (state not yet propagated) can't bounce the control back.
  const [state, setState] = useState(device.state);
  const freezeUntil = useRef(0);
  const commitTimer = useRef(null);
  const tempTimer = useRef(null);
  // Press-and-hold on the climate +/- buttons: repeat while held, send once on
  // release. holdDelay = the initial pause before repeating; holdTimer = the
  // repeat interval; pressing = whether a press is currently in progress.
  const holdDelay = useRef(null);
  const holdTimer = useRef(null);
  const pressing = useRef(false);
  useEffect(() => {
    if (Date.now() >= freezeUntil.current) setState(device.state);
  }, [device]);
  useEffect(
    () => () => {
      clearTimeout(commitTimer.current);
      clearTimeout(tempTimer.current);
      clearTimeout(holdDelay.current);
      clearInterval(holdTimer.current);
    },
    []
  );

  // Optimistic fan mode (an attribute, not the entity state) - same idea so
  // climate fan buttons respond instantly.
  const [fanMode, setFanMode] = useState(a.fan_mode);
  const fanFreeze = useRef(0);
  useEffect(() => {
    if (Date.now() >= fanFreeze.current) setFanMode((device.attributes || {}).fan_mode);
  }, [device]);

  function setFan(fm) {
    setFanMode(fm);
    fanFreeze.current = Date.now() + 1500;
    act('set_fan_mode', { fan_mode: fm });
  }

  // Optimistic swing mode (same pattern as fan mode).
  const [swingMode, setSwingMode] = useState(a.swing_mode);
  const swingFreeze = useRef(0);
  useEffect(() => {
    if (Date.now() >= swingFreeze.current) setSwingMode((device.attributes || {}).swing_mode);
  }, [device]);

  function setSwing(sm) {
    setSwingMode(sm);
    swingFreeze.current = Date.now() + 1500;
    act('set_swing_mode', { swing_mode: sm });
  }

  // Optimistic target temperature: like fan/swing, re-sync from the live stream
  // when a new state arrives (the scheduler, another client, or the remote HA
  // itself changed it), unless the user just adjusted it. Without this the
  // target froze at mount - or at the `?? 22` fallback for a late-loading remote
  // entity - while the current temp kept updating: the visible "out of sync".
  const targetFreeze = useRef(0);
  useEffect(() => {
    if (Date.now() >= targetFreeze.current) {
      const t = (device.attributes || {}).temperature;
      if (t != null) {
        setTarget(t);
        targetRef.current = t;
      }
    }
  }, [device]);

  const on = state === 'on';
  const isActive = ACTIVE_STATES.has(state);

  // `optimistic` is the state to show immediately (when the outcome is known).
  async function act(service, data, optimistic) {
    if (optimistic) {
      setState(optimistic);
      // Brief hold so a stale read can't bounce the control back; the live
      // stream confirms the real state within ~a fraction of a second.
      freezeUntil.current = Date.now() + 1500;
    }
    setBusy(true);
    try {
      await control(device.entity_id, service, data || {});
      onChange();
    } catch (err) {
      onError?.(err.message);
    } finally {
      setBusy(false);
    }
  }

  // Power toggle: flip the UI instantly, but send an *idempotent* turn_on /
  // turn_off (not a parity-based "toggle") and debounce it, so clicking fast
  // ends in exactly the state shown - the last click wins, no desync.
  function setPower(nextOn) {
    setState(nextOn ? 'on' : 'off');
    freezeUntil.current = Date.now() + 1500;
    clearTimeout(commitTimer.current);
    commitTimer.current = setTimeout(() => {
      control(device.entity_id, nextOn ? 'turn_on' : 'turn_off', {})
        .then(onChange)
        .catch((err) => onError?.(err.message));
    }, 250);
  }

  const slider = (label, value, setValue, service, toData, step = 1) => (
    <label className="slider">
      {label}: {Math.round(value)}%
      <input
        type="range"
        min="0"
        max="100"
        step={step}
        value={value}
        style={{ '--fill': `${value}%` }}
        disabled={busy}
        // Move freely while dragging (a light haptic tick per step), and only
        // send to HA on release - like the climate +/- hold.
        onChange={(e) => {
          setValue(Number(e.target.value));
          haptic(8);
        }}
        onMouseUp={(e) => act(service, toData(Number(e.target.value)))}
        onTouchEnd={(e) => act(service, toData(Number(e.target.value)))}
      />
    </label>
  );

  function controls() {
    switch (device.domain) {
      case 'light': {
        const dim =
          a.brightness != null ||
          (a.supported_color_modes || []).some((m) => m !== 'onoff');
        return (
          <>
            <div className="row-end">
              <Toggle on={on} disabled={busy} onClick={() => setPower(!on)} />
            </div>
            {dim && on &&
              slider('Brightness', pct, setPct, 'turn_on', (v) => ({
                brightness: Math.round((v / 100) * 255),
              }))}
          </>
        );
      }
      case 'switch':
      case 'input_boolean':
        return (
          <div className="row-end">
            <Toggle on={on} disabled={busy} onClick={() => setPower(!on)} />
          </div>
        );
      case 'fan':
        return (
          <>
            <div className="row-end">
              {on && <span className="fan-spin" aria-hidden="true">❉</span>}
              <Toggle on={on} disabled={busy} onClick={() => setPower(!on)} />
            </div>
            {a.percentage != null && on &&
              slider('Speed', pct, setPct, 'set_percentage', (v) => ({ percentage: v }),
                a.percentage_step || 1)}
          </>
        );
      case 'climate': {
        const isOff = state === 'off';
        const min = a.min_temp ?? 16;
        const max = a.max_temp ?? 30;
        const step = a.target_temp_step ?? 0.5;
        // Move the shown value instantly; only send the final temperature to HA
        // when the user lets go (a fallback timer covers a missed release) - so
        // tapping or holding 70 -> 64 is one call, not six.
        const commitTemp = () => {
          clearTimeout(tempTimer.current);
          // Hold the optimistic value past the send so the stream echo of the
          // new target doesn't briefly bounce back to the old one.
          targetFreeze.current = Date.now() + 3000;
          control(device.entity_id, 'set_temperature', { temperature: targetRef.current })
            .then(onChange)
            .catch((err) => onError?.(err.message));
        };
        const bump = (delta) => {
          const next = Math.min(max, Math.max(min, Number((targetRef.current + delta).toFixed(1))));
          if (next === targetRef.current) return; // already at the limit
          targetRef.current = next;
          setTarget(next);
          // Freeze stream re-syncs while the user is still adjusting (extends on
          // each tap/hold); commitTemp refreshes it again when the value is sent.
          targetFreeze.current = Date.now() + 3000;
          clearTimeout(tempTimer.current); // fallback commit if release is missed
          tempTimer.current = setTimeout(commitTemp, 2000);
        };
        // Press-and-hold: one bump immediately (so a tap works), then repeat
        // after a short delay, accelerating slightly, until release.
        const startHold = (delta) => {
          pressing.current = true;
          bump(delta);
          clearTimeout(holdDelay.current);
          holdDelay.current = setTimeout(() => {
            let n = 0;
            holdTimer.current = setInterval(() => {
              n += 1;
              bump(delta * (n > 12 ? 2 : 1)); // speed up after ~1.5s of holding
            }, 110);
          }, 400);
        };
        const endHold = () => {
          if (!pressing.current) return;
          pressing.current = false;
          clearTimeout(holdDelay.current);
          clearInterval(holdTimer.current);
          holdDelay.current = null;
          holdTimer.current = null;
          commitTemp(); // push the final target once, on release
        };
        const tempBtn = (delta, label, aria) => (
          <button
            type="button"
            className="temp-btn"
            disabled={isOff}
            aria-label={aria}
            onPointerDown={(e) => {
              if (isOff) return;
              e.preventDefault();
              startHold(delta);
            }}
            onPointerUp={endHold}
            onPointerLeave={endHold}
            onPointerCancel={endHold}
            // Keyboard activation fires click with detail 0 (no pointer events).
            onClick={(e) => {
              if (e.detail === 0 && !isOff) {
                bump(delta);
                commitTemp();
              }
            }}
          >
            {label}
          </button>
        );
        // Fan modes: hide "night"; if the rest form an ordered speed ladder
        // (low..high) render a slider that reports the equivalent named mode to
        // HA, and keep any non-speed modes (e.g. "auto") as buttons.
        const fanModesVisible = (a.fan_modes || []).filter((m) => !FAN_HIDE.has(fanKey(m)));
        const numericFan = fanModesVisible.filter(isNumericMode);
        let fanSpeeds;
        let fanSpecials;
        if (numericFan.length >= 3) {
          // Numeric speed list ("1".."5"): order by value; keep any non-numeric
          // modes (e.g. "auto") as buttons.
          fanSpeeds = [...numericFan].sort((x, y) => parseFloat(x) - parseFloat(y));
          fanSpecials = fanModesVisible.filter((m) => !isNumericMode(m));
        } else {
          // Named ladder (low..high); non-ladder modes (auto, etc.) as buttons.
          fanSpeeds = fanModesVisible
            .filter((m) => fanKey(m) in FAN_SPEED_RANK)
            .sort((x, y) => FAN_SPEED_RANK[fanKey(x)] - FAN_SPEED_RANK[fanKey(y)]);
          fanSpecials = fanModesVisible.filter((m) => !(fanKey(m) in FAN_SPEED_RANK));
        }
        const fanAsSlider = fanSpeeds.length >= 3;
        const fanIdx = Math.max(0, fanSpeeds.indexOf(fanMode));
        const commitFan = (fm) => {
          setFanMode(fm);
          fanFreeze.current = Date.now() + 1500;
          control(device.entity_id, 'set_fan_mode', { fan_mode: fm }).then(onChange).catch((err) => onError?.(err.message));
        };
        // Build the readout as one string so the separator/spacing don't depend
        // on CSS (a stale cached stylesheet was rendering the two spans joined).
        const readout = [
          a.current_temperature != null ? `Now ${a.current_temperature}°` : null,
          a.current_humidity != null ? `${a.current_humidity}% humidity` : null,
        ]
          .filter(Boolean)
          .join(' · ');
        return (
          <>
            {readout && (
              <div className="climate-readout">
                <span className="muted">{readout}</span>
              </div>
            )}
            <div className="temp-control">
              {tempBtn(-step, '−', 'Decrease temperature')}
              <span className="temp-value">{isOff ? '-' : `${target}°`}</span>
              {tempBtn(step, '+', 'Increase temperature')}
            </div>
            <div className="mode-row">
              {(a.hvac_modes || []).map((mode) => (
                <button
                  key={mode}
                  className={`mode ${state === mode ? 'selected' : ''} ${mode === 'off' ? 'mode-off' : ''}`}
                  onClick={() => act('set_hvac_mode', { hvac_mode: mode }, mode)}
                  disabled={busy}
                >
                  {humanize(mode)}
                </button>
              ))}
            </div>
            {fanModesVisible.length > 0 && !isOff && (
              <div className="fan-modes">
                {fanAsSlider ? (
                  <>
                    <label className="slider fan-slider">
                      Fan: {prettyMode(fanMode)}
                      <input
                        type="range"
                        min="0"
                        max={fanSpeeds.length - 1}
                        step="1"
                        value={fanIdx}
                        style={{ '--fill': `${(fanIdx / Math.max(1, fanSpeeds.length - 1)) * 100}%` }}
                        disabled={busy}
                        aria-label="Fan speed"
                        onChange={(e) => {
                          setFanMode(fanSpeeds[Number(e.target.value)]);
                          fanFreeze.current = Date.now() + 1500; // don't let a push bounce the drag
                          haptic(8);
                        }}
                        onMouseUp={(e) => commitFan(fanSpeeds[Number(e.target.value)])}
                        onTouchEnd={(e) => commitFan(fanSpeeds[Number(e.target.value)])}
                      />
                    </label>
                    {fanSpecials.length > 0 && (
                      <div className="mode-row">
                        {fanSpecials.map((fm) => (
                          <button
                            key={fm}
                            className={`mode ${fanMode === fm ? 'selected' : ''}`}
                            onClick={() => setFan(fm)}
                            disabled={busy}
                          >
                            {prettyMode(fm)}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <span className="muted">Fan</span>
                    <div className="mode-row">
                      {fanModesVisible.map((fm) => (
                        <button
                          key={fm}
                          className={`mode ${fanMode === fm ? 'selected' : ''}`}
                          onClick={() => setFan(fm)}
                          disabled={busy}
                        >
                          {prettyMode(fm)}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
            {(a.swing_modes || []).length > 0 && !isOff && (() => {
              const modes = a.swing_modes;
              // Classify each swing mode into the axes it drives, tolerating
              // naming variants: off/stop/none, horizontal|h, vertical|v,
              // both|all|h+v, etc. If the four combinations (off / H / V /
              // both) are all available, show two independent toggles instead
              // of four exclusive buttons and derive the swing_mode from them.
              const axesOf = (mode) => {
                const s = String(mode).toLowerCase().trim();
                if (/(^|[^a-z])(off|stop|none|disabled)([^a-z]|$)/.test(s) || s === '0' || s === 'false')
                  return { off: true };
                if (s.includes('both') || s === 'all' || s === '3d') return { h: true, v: true };
                const h = s.includes('horizontal') || /(^|[^a-z])h([^a-z]|$)/.test(s);
                const v = s.includes('vertical') || /(^|[^a-z])v([^a-z]|$)/.test(s);
                return { h, v };
              };
              const cls = modes.map((m) => ({ m, ax: axesOf(m) }));
              const offMode = (cls.find((c) => c.ax.off) || {}).m;
              const pick = (h, v) =>
                (cls.find((c) => !c.ax.off && !!c.ax.h === h && !!c.ax.v === v) || {}).m;
              const hOnly = pick(true, false);
              const vOnly = pick(false, true);
              const both = pick(true, true);
              const twoAxis =
                offMode != null && hOnly != null && vOnly != null && both != null;
              const curAx = axesOf(swingMode || 'off');
              const hOn = !!curAx.h;
              const vOn = !!curAx.v;
              const modeFor = (h, v) => (h && v ? both : h ? hOnly : v ? vOnly : offMode);
              const setHV = (h, v) => {
                const t = modeFor(h, v);
                if (t != null) setSwing(t);
              };
              return (
                <div className="fan-modes">
                  <span className="muted">Swing</span>
                  <div className="mode-row">
                    {twoAxis ? (
                      <>
                        <button
                          className={`mode ${hOn ? 'selected' : ''}`}
                          onClick={() => setHV(!hOn, vOn)}
                          disabled={busy}
                        >
                          Horizontal
                        </button>
                        <button
                          className={`mode ${vOn ? 'selected' : ''}`}
                          onClick={() => setHV(hOn, !vOn)}
                          disabled={busy}
                        >
                          Vertical
                        </button>
                      </>
                    ) : (
                      modes.map((m) => (
                        <button
                          key={m}
                          className={`mode ${swingMode === m ? 'selected' : ''}`}
                          onClick={() => setSwing(m)}
                          disabled={busy}
                        >
                          {humanize(m)}
                        </button>
                      ))
                    )}
                  </div>
                </div>
              );
            })()}
          </>
        );
      }
      case 'cover':
        return (
          <>
            <div className="btn-row">
              <button onClick={() => act('open_cover', {}, 'open')} disabled={busy}>Open</button>
              <button onClick={() => act('stop_cover')} disabled={busy}>Stop</button>
              <button onClick={() => act('close_cover', {}, 'closed')} disabled={busy}>Close</button>
            </div>
            {a.current_position != null &&
              slider('Position', pct, setPct, 'set_cover_position', (v) => ({ position: v }))}
          </>
        );
      case 'lock': {
        const locked = state === 'locked';
        return (
          <div className="btn-row">
            <button
              className={`mode ${locked ? 'selected' : ''}`}
              onClick={() => act('lock', {}, 'locked')}
              disabled={busy || locked}
            >
              Lock
            </button>
            <button
              className={`mode ${!locked ? 'selected' : ''}`}
              onClick={() => act('unlock', {}, 'unlocked')}
              disabled={busy || !locked}
            >
              Unlock
            </button>
          </div>
        );
      }
      case 'media_player':
        return (
          <>
            <div className="btn-row">
              <button onClick={() => act('media_previous_track')} disabled={busy}>⏮</button>
              <button onClick={() => act('media_play_pause')} disabled={busy}>⏯</button>
              <button onClick={() => act('media_next_track')} disabled={busy}>⏭</button>
            </div>
            {a.volume_level != null &&
              slider('Volume', vol, setVol, 'volume_set', (v) => ({ volume_level: v / 100 }))}
          </>
        );
      case 'scene':
        return (
          <div className="btn-row">
            <button onClick={() => act('turn_on')} disabled={busy}>Activate</button>
          </div>
        );
      case 'script':
        return (
          <div className="btn-row">
            <button onClick={() => act('turn_on')} disabled={busy}>Run</button>
          </div>
        );
      case 'automation':
        return (
          <div className="row-between">
            <button className="mode" onClick={() => act('trigger')} disabled={busy}>Trigger</button>
            <Toggle on={on} disabled={busy} onClick={() => setPower(!on)} />
          </div>
        );
      case 'button':
      case 'input_button':
        return (
          <div className="btn-row">
            <button onClick={() => act('press')} disabled={busy}>Press</button>
          </div>
        );
      case 'vacuum':
        return (
          <div className="btn-row">
            <button onClick={() => act('start')} disabled={busy}>Start</button>
            <button onClick={() => act('pause')} disabled={busy}>Pause</button>
            <button onClick={() => act('return_to_base')} disabled={busy}>Dock</button>
          </div>
        );
      default:
        return <div className="device-sub muted">Read-only</div>;
    }
  }

  // For RGB lights: extract the current color as a CSS rgb() string, or null.
  function lightRgb() {
    if (device.domain !== 'light' || !on) return null;
    if (a.rgb_color) {
      const [r, g, b] = a.rgb_color;
      // Very dark colors (nearly black) look bad as a glow - fall back to warm.
      if (r + g + b < 30) return null;
      return `rgb(${r},${g},${b})`;
    }
    if (a.hs_color) {
      // Convert HS (hue 0-360, sat 0-100) to RGB for the glow color.
      const h = a.hs_color[0] / 360;
      const s = a.hs_color[1] / 100;
      if (s < 0.08) return null; // nearly white - warm fallback looks better
      const i = Math.floor(h * 6);
      const f = h * 6 - i;
      const q = 1 - f, t = f;
      const [r, g, b] = [
        [1,t,0],[q,1,0],[0,1,t],[0,q,1],[t,0,1],[1,0,q],
      ][i % 6];
      return `rgb(${Math.round(r*255)},${Math.round(g*255)},${Math.round(b*255)})`;
    }
    return null;
  }

  // Pick a state-reactive accent: warm light, blue cool, red heat, etc.
  // Every active accent pulses for a consistent "live" feel.
  function accent() {
    const d = device.domain;
    if (d === 'light' && on) return lightRgb() ? 'glow pulse' : 'accent-warm glow pulse';
    if (d === 'climate') {
      // heat_cool can heat OR cool. Color by what it's actually doing now if HA
      // reports it (hvac_action): red heating, blue cooling. Many setups don't
      // report the action, so fall back to a heat+cool blend rather than guessing.
      if (state === 'heat_cool') {
        if (a.hvac_action === 'heating') return 'accent-heat glow pulse';
        if (a.hvac_action === 'cooling') return 'accent-cool glow pulse';
        return 'accent-mix glow pulse';
      }
      if (state === 'heat') return 'accent-heat glow pulse';
      if (state === 'cool' || state === 'auto') return 'accent-cool glow pulse';
      if (state === 'dry') return 'accent-dry glow pulse';
      if (state === 'fan_only') return 'accent-on glow pulse';
      return '';
    }
    if (d === 'cover' && state === 'open') return 'accent-sky glow pulse';
    if (d === 'lock') return state === 'locked' ? 'accent-on glow pulse' : 'accent-amber glow pulse';
    if (d === 'media_player' && state === 'playing') return 'accent-media glow pulse';
    if (['switch', 'input_boolean', 'fan', 'automation'].includes(d) && on)
      return 'accent-on glow pulse';
    return isActive ? 'accent-on glow pulse' : '';
  }

  const rgb = lightRgb();
  return (
    <div className={`card device ${accent()}`} style={rgb ? { '--g': rgb } : undefined}>
      <div className="device-head">
        <span className="device-name">{device.name}</span>
        {onEdit && device.device_id && (
          <button
            type="button"
            className="ghost icon-only device-edit"
            title="Edit device (name &amp; area)"
            onClick={onEdit}
          >
            <MdiIcon icon="pencil-outline" size={18} />
          </button>
        )}
      </div>
      <div className="device-state">{prettyState({ state, attributes: a })}</div>
      <div className="controls">{controls()}</div>
    </div>
  );
}

// Quick edit dialog for a device: rename + reassign area (writes to HA).
function DeviceEditDialog({ device, areas, onClose, onSave }) {
  const [name, setName] = useState(device.name || '');
  const [areaId, setAreaId] = useState(device.area_id || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function save() {
    setBusy(true);
    setErr('');
    try {
      await onSave(device.id, { name: name.trim(), area_id: areaId || null });
      onClose();
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="card modal modal-form" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Edit device</h3>
          <button className="ghost icon-only" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <label>
          Name
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        <label>
          Area
          <select value={areaId} onChange={(e) => setAreaId(e.target.value)}>
            <option value="">Unassigned</option>
            {areas.map((a) => (
              <option key={a.area_id} value={a.area_id}>
                {a.floor ? `${a.floor} - ${a.name}` : a.name}
              </option>
            ))}
          </select>
        </label>
        {device.entities && device.entities.length > 0 && (
          <p className="meta">{device.entities.length} entit{device.entities.length === 1 ? 'y' : 'ies'}: {device.entities.slice(0, 4).join(', ')}{device.entities.length > 4 ? '…' : ''}</p>
        )}
        {err && <div className="error">{err}</div>}
        <div className="editor-actions">
          <button className="btn-primary" onClick={save} disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </button>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// Human-readable list of the admin's password requirements.
function passwordRuleList(r) {
  if (!r) return [];
  const out = [];
  if (r.min) out.push(`at least ${r.min} characters`);
  if (r.max) out.push(`at most ${r.max} characters`);
  if (r.upper) out.push('an uppercase letter');
  if (r.lower) out.push('a lowercase letter');
  if (r.number) out.push('a number');
  if (r.special) out.push('a special character');
  return out;
}

// Join a list as natural English: "a", "a and b", "a, b, and c".
function joinNatural(items) {
  if (items.length <= 1) return items.join('');
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`;
}

// Self-service "Change password" dialog (local accounts only).
function ChangePasswordDialog({ rules, onClose }) {
  const [cur, setCur] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [done, setDone] = useState(false);
  const reqs = passwordRuleList(rules);

  async function submit(e) {
    e.preventDefault();
    setErr('');
    if (next !== confirm) {
      setErr('The new passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      await changeMyPassword({ current: cur, new: next });
      setDone(true);
    } catch (e2) {
      setErr(e2.message);
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="card modal modal-form" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Change password</h3>
          <button className="ghost icon-only" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {done ? (
          <>
            <p className="meta">Your password has been changed.</p>
            <div className="editor-actions">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </>
        ) : (
          <form onSubmit={submit}>
            <label>
              Current password
              <input type="password" value={cur} autoFocus autoComplete="current-password"
                     onChange={(e) => setCur(e.target.value)} />
            </label>
            <label>
              New password
              <input type="password" value={next} autoComplete="new-password"
                     onChange={(e) => setNext(e.target.value)} />
            </label>
            <label>
              Confirm new password
              <input type="password" value={confirm} autoComplete="new-password"
                     onChange={(e) => setConfirm(e.target.value)} />
            </label>
            {reqs.length > 0 && <p className="pw-reqs">Must include {joinNatural(reqs)}.</p>}
            {err && <div className="error">{err}</div>}
            <div className="editor-actions">
              <button type="submit" className="btn-primary" disabled={busy}>
                {busy ? 'Saving…' : 'Change password'}
              </button>
              <button type="button" className="ghost" onClick={onClose} disabled={busy}>
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

// Manager-only: move Home Assistant devices between areas / rename (writes to HA).
// Friendly display name for a Home Assistant integration (platform) domain.
const INTEGRATION_NAMES = {
  mqtt: 'MQTT', tplink: 'TP-Link', kasa: 'Kasa', esphome: 'ESPHome',
  hue: 'Philips Hue', zha: 'Zigbee', zwave_js: 'Z-Wave', deconz: 'deCONZ',
  tasmota: 'Tasmota', shelly: 'Shelly', wiz: 'WiZ', lifx: 'LIFX',
  homekit_controller: 'HomeKit', smartthings: 'SmartThings', sonos: 'Sonos',
  homeassistant: 'Home Assistant',
};
function prettyIntegration(dom) {
  if (INTEGRATION_NAMES[dom]) return INTEGRATION_NAMES[dom];
  return dom.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

// Brand badge: the integration's logo (from HA's brands CDN) + name. Falls back
// to just the name if the brand has no logo. Shown on manager device rows.
function IntegrationBadge({ domain, name }) {
  // Show the integration's brand logo via our backend proxy, which serves Home
  // Assistant's brands API (so a custom integration's OWN logo shows too) with a
  // CDN fallback, and 404s when there's no real logo. On error, show a neutral
  // puzzle-piece glyph - never Home Assistant's "icon not available" image.
  const [logoOk, setLogoOk] = useState(true);
  if (!domain && !name) return null;
  const label = name || prettyIntegration(domain || '');
  const tok = getToken();
  const src = `api/icon/brand/${domain}${tok ? `?token=${encodeURIComponent(tok)}` : ''}`;
  return (
    <span className="int-badge" title={label}>
      {domain && logoOk ? (
        <img
          className="int-logo"
          src={src}
          alt=""
          loading="lazy"
          onError={() => setLogoOk(false)}
        />
      ) : (
        <svg className="int-logo int-generic" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M20.5,11H19V7C19,5.89 18.1,5 17,5H13V3.5A2.5,2.5 0 0,0 10.5,1A2.5,2.5 0 0,0 8,3.5V5H4A2,2 0 0,0 2,7V10.8H3.5C5,10.8 6.2,12 6.2,13.5C6.2,15 5,16.2 3.5,16.2H2V20A2,2 0 0,0 4,22H7.8V20.5C7.8,19 9,17.8 10.5,17.8C12,17.8 13.2,19 13.2,20.5V22H17A2,2 0 0,0 19,20V16H20.5A2.5,2.5 0 0,0 23,13.5A2.5,2.5 0 0,0 20.5,11Z" />
        </svg>
      )}
      <span>{label}</span>
    </span>
  );
}

function Organizer() {
  const [data, setData] = useState(null); // { devices, areas }
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState(null); // device being edited

  const load = useCallback(() => {
    setError('');
    managerGetDevices()
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function applyUpdate(device_id, fields) {
    await managerUpdateDevice(device_id, fields);
    setData((d) => ({
      ...d,
      devices: d.devices.map((dev) =>
        dev.id === device_id
          ? {
              ...dev,
              ...('area_id' in fields
                ? {
                    area_id: fields.area_id || null,
                    area: (d.areas.find((a) => a.area_id === fields.area_id) || {}).name || null,
                  }
                : {}),
              ...(fields.name ? { name: fields.name } : {}),
            }
          : dev
      ),
    }));
  }

  if (!data) {
    return error ? <div className="error banner">{error}</div> : <p className="muted">Loading devices…</p>;
  }

  const term = query.trim().toLowerCase();
  const visible = term
    ? data.devices.filter(
        (d) =>
          d.name.toLowerCase().includes(term) ||
          (d.area || '').toLowerCase().includes(term) ||
          d.entities.some((e) => e.toLowerCase().includes(term))
      )
    : data.devices;

  // Group devices by current area; "Unassigned" first so new devices stand out.
  const groups = {};
  for (const dev of visible) {
    const k = dev.area || 'Unassigned';
    (groups[k] = groups[k] || []).push(dev);
  }
  const order = Object.keys(groups).sort((a, b) =>
    a === 'Unassigned' ? -1 : b === 'Unassigned' ? 1 : a.localeCompare(b)
  );

  return (
    <div className="organizer">
      <p className="muted org-intro">
        Assign each device to a Home Assistant area, or rename it. Changes are saved to Home
        Assistant. <span className="tap-hint">Tap any device below to edit it.</span>
      </p>
      <SearchBox
        className="dashboard-search"
        placeholder="Search devices…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {error && <div className="error banner">{error}</div>}
      {data.devices.length === 0 ? (
        <p className="muted">No devices found in Home Assistant.</p>
      ) : visible.length === 0 ? (
        <p className="muted">No devices match “{query}”.</p>
      ) : (
        order.map((areaName) => (
          <section key={areaName}>
            <h2>{areaName}</h2>
            {groups[areaName].map((dev) => (
              <button
                key={dev.id}
                type="button"
                className="card org-row"
                title="Edit this device"
                onClick={() => setEditing(dev)}
              >
                <div className="org-info">
                  <span className="device-name">
                    {dev.name}
                    {dev.instance_name && <span className="pick-instance-badge">{dev.instance_name}</span>}
                  </span>
                  <span className="meta">
                    {dev.area || 'Unassigned'}
                    {dev.entities.length > 0
                      ? ` · ${dev.entities.slice(0, 2).join(', ')}${dev.entities.length > 2 ? '…' : ''}`
                      : ''}
                  </span>
                </div>
                <IntegrationBadge domain={dev.integration} name={dev.integration_name} />
              </button>
            ))}
          </section>
        ))
      )}
      {editing && (
        <DeviceEditDialog
          device={editing}
          areas={data.areas.filter((a) => a.instance === editing.instance)}
          onClose={() => setEditing(null)}
          onSave={applyUpdate}
        />
      )}
    </div>
  );
}

// Render an arbitrary Material Design Icon by name ("mdi:sofa" or "sofa"),
// fetched once from the backend and cached in-memory. Falls back to a neutral
// room glyph while loading, on failure, or when no icon is set.
const _mdiCache = new Map(); // name -> {body,width,height} | null (failed)
function MdiIcon({ icon, size = 22, className = '' }) {
  const name = icon ? String(icon).replace(/^mdi:/, '') : '';
  const [data, setData] = useState(() => (name ? _mdiCache.get(name) : undefined));
  useEffect(() => {
    if (!name) return;
    if (_mdiCache.has(name)) {
      setData(_mdiCache.get(name));
      return;
    }
    let alive = true;
    getMdiIcon(name)
      .then((d) => {
        _mdiCache.set(name, d);
        if (alive) setData(d);
      })
      .catch(() => {
        _mdiCache.set(name, null);
        if (alive) setData(null);
      });
    return () => {
      alive = false;
    };
  }, [name]);

  if (name && data) {
    return (
      <svg
        className={`mdi-icon ${className}`}
        viewBox={`0 0 ${data.width || 24} ${data.height || 24}`}
        width={size}
        height={size}
        fill="currentColor"
        aria-hidden="true"
        dangerouslySetInnerHTML={{ __html: data.body }}
      />
    );
  }
  return (
    <svg
      className={`mdi-icon ${className}`}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <rect x="4" y="4" width="16" height="16" rx="3" />
    </svg>
  );
}

// Create a new area or rename an existing one (and pick a floor when creating).
function AreaEditDialog({ area, floors, onClose, onSave }) {
  const isNew = !area.area_id;
  const [name, setName] = useState(area.name || '');
  const [floorId, setFloorId] = useState(area.floor_id || '');
  // For new areas with no floor, we need to know which instance to create on.
  const [instanceId, setInstanceId] = useState(area.instance !== undefined ? area.instance : null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  // Unique instances present in the floors list (for new-area instance selector).
  const instances = isNew
    ? [...new Map(floors.map((f) => [f.instance, { id: f.instance, name: f.instance_name || 'Main' }])).values()]
    : [];
  const hasMultipleInstances = instances.length > 1;

  // When a floor is selected, update the tracked instance to match.
  function handleFloorChange(fid) {
    setFloorId(fid);
    if (fid) {
      const f = floors.find((fl) => fl.floor_id === fid);
      if (f) setInstanceId(f.instance);
    }
  }

  async function save() {
    if (!name.trim()) {
      setErr('An area name is required');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const fields = isNew
        ? { name: name.trim(), floor_id: floorId || null, instance: floorId ? undefined : instanceId }
        : { area_id: area.area_id, name: name.trim() };
      await onSave(fields);
      onClose();
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  // Group floors by instance for the optgroup layout (new areas only).
  const floorsByInstance = isNew ? instances.map((inst) => ({
    inst,
    floors: floors.filter((f) => f.instance === inst.id),
  })) : [];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="card modal modal-form" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{isNew ? 'New area' : 'Rename area'}</h3>
          <button className="ghost icon-only" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <label>
          Name
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        {isNew && hasMultipleInstances && !floorId && (
          <label>
            Instance
            <select value={instanceId || ''} onChange={(e) => setInstanceId(e.target.value || null)}>
              {instances.map((inst) => (
                <option key={inst.id || '__main'} value={inst.id || ''}>{inst.name}</option>
              ))}
            </select>
          </label>
        )}
        {isNew && (
          <label>
            Floor
            <select value={floorId} onChange={(e) => handleFloorChange(e.target.value)}>
              <option value="">No floor</option>
              {hasMultipleInstances
                ? floorsByInstance.map(({ inst, floors: iFloors }) =>
                    iFloors.length > 0 && (
                      <optgroup key={inst.id || '__main'} label={inst.name}>
                        {iFloors.map((f) => (
                          <option key={f.floor_id} value={f.floor_id}>{f.name}</option>
                        ))}
                      </optgroup>
                    )
                  )
                : floors.map((f) => (
                    <option key={f.floor_id} value={f.floor_id}>{f.name}</option>
                  ))
              }
            </select>
          </label>
        )}
        {err && <div className="error">{err}</div>}
        <div className="editor-actions">
          <button className="btn-primary" onClick={save} disabled={busy}>
            {busy ? 'Saving…' : isNew ? 'Create' : 'Save'}
          </button>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// Manager-only: group HA areas into floors and create new areas (writes to HA).
// Floors themselves are managed in Home Assistant - here they're only assigned.
// Laid out like HA's overview: a section per floor, with the areas inside it.
function AreaOrganizer() {
  const [data, setData] = useState(null); // { floors, areas }
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null); // area being renamed, or {} for new

  const load = useCallback(() => {
    setError('');
    managerGetAreas()
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function move(area_id, floor_id) {
    try {
      await managerSaveArea({ area_id, floor_id: floor_id || null });
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  async function saveArea(fields) {
    await managerSaveArea(fields);
    load();
  }

  if (!data) {
    return error ? <div className="error banner">{error}</div> : <p className="muted">Loading areas…</p>;
  }

  // Bucket areas under their floor; areas with no floor go in a trailing group.
  const byFloor = new Map(data.floors.map((f) => [f.floor_id, []]));
  const noFloor = [];
  for (const a of data.areas) {
    if (a.floor_id && byFloor.has(a.floor_id)) byFloor.get(a.floor_id).push(a);
    else noFloor.push(a);
  }
  const sections = [
    ...data.floors.map((f) => ({ key: f.floor_id, name: f.name, areas: byFloor.get(f.floor_id) })),
    { key: '__none', name: 'No floor', areas: noFloor },
  ];

  return (
    <div className="area-org">
      <div className="area-org-head">
        <p className="muted org-intro">
          Group your Home Assistant areas into floors, and create new areas. Floors are managed in
          Home Assistant. Changes are saved to Home Assistant.
        </p>
        <button className="btn-primary area-add" onClick={() => setEditing({})}>
          ＋ New area
        </button>
      </div>
      {error && <div className="error banner">{error}</div>}
      {data.floors.length === 0 && (
        <p className="muted">
          No floors defined in Home Assistant yet. Create floors there, then assign areas to them
          here.
        </p>
      )}
      {sections.map(
        (sec) =>
          (sec.areas.length > 0 || sec.key !== '__none') && (
            <section key={sec.key}>
              <h2>{sec.name}</h2>
              {sec.areas.length === 0 ? (
                <p className="muted area-empty">No areas on this floor.</p>
              ) : (
                sec.areas.map((a) => (
                  <div key={a.area_id} className="card org-row">
                    <span className="area-icon" aria-hidden="true">
                      <MdiIcon icon={a.icon} />
                    </span>
                    <div className="org-info">
                      <span className="device-name">
                        {a.name}
                        {a.instance && (
                          <span className="section-instance-badge">{a.instance_name || a.instance}</span>
                        )}
                      </span>
                    </div>
                    {data.floors.filter((f) => f.instance === a.instance).length > 0 && (
                      <select
                        className="user-filter area-floor-select"
                        value={a.floor_id || ''}
                        onChange={(e) => move(a.area_id, e.target.value)}
                        aria-label={`Floor for ${a.name}`}
                      >
                        <option value="">No floor</option>
                        {data.floors.filter((f) => f.instance === a.instance).map((f) => (
                          <option key={f.floor_id} value={f.floor_id}>{f.name}</option>
                        ))}
                      </select>
                    )}
                    <button
                      className="ghost icon-only org-edit"
                      title="Rename area"
                      onClick={() => setEditing(a)}
                    >
                      <MdiIcon icon="pencil-outline" size={18} />
                    </button>
                  </div>
                ))
              )}
            </section>
          )
      )}
      {editing && (
        <AreaEditDialog
          area={editing}
          floors={editing.area_id
            ? data.floors.filter((f) => f.instance === editing.instance)
            : data.floors}
          onClose={() => setEditing(null)}
          onSave={saveArea}
        />
      )}
    </div>
  );
}

// Manager-only Organize view: tabs to organize devices into areas, or areas
// into floors.
function Organize() {
  const [tab, setTab] = useState('devices');
  return (
    <div className="organize">
      <div className="lb-scope organize-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'devices'}
          className={tab === 'devices' ? 'active' : ''}
          onClick={() => setTab('devices')}
        >
          Devices
        </button>
        <button
          role="tab"
          aria-selected={tab === 'areas'}
          className={tab === 'areas' ? 'active' : ''}
          onClick={() => setTab('areas')}
        >
          Areas &amp; floors
        </button>
      </div>
      {tab === 'devices' ? <Organizer /> : <AreaOrganizer />}
    </div>
  );
}

// Initials (up to 2 letters) from a display name.
function initialsOf(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
// Stable, distinct color per user (hash the name -> hue).
function avatarColor(key) {
  let h = 0;
  const s = String(key || '');
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360}deg 52% 45%)`;
}
// User avatar: the OAuth picture if available, else initials on a per-user
// color, else a generic account glyph.
function Avatar({ name, picture, size = 32 }) {
  const [imgOk, setImgOk] = useState(true);
  const initials = initialsOf(name);
  const dim = { width: size, height: size };
  if (picture && imgOk) {
    return (
      <img className="avatar" style={dim} src={picture} alt="" referrerPolicy="no-referrer"
           onError={() => setImgOk(false)} />
    );
  }
  if (initials) {
    return (
      <span className="avatar avatar-initials" style={{ ...dim, background: avatarColor(name) }}>
        {initials}
      </span>
    );
  }
  return (
    <span className="avatar avatar-initials" style={{ ...dim, background: 'var(--muted)' }}>
      <MdiIcon icon="account" size={Math.round(size * 0.62)} />
    </span>
  );
}

// Account dropdown in the dashboard header: the avatar opens a menu with the
// manager organizer and log out. (Change password is added in a later step.)
function AccountMenu({ name, picture, isManager, canChangePassword, onChangePassword, onOrganize, onSchedules, onLists, onLogout }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);
  return (
    <div className="account" ref={ref}>
      <button
        className="account-btn"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={name || 'Account'}
      >
        <Avatar name={name} picture={picture} />
      </button>
      {open && (
        <div className="account-menu" role="menu">
          {name && <div className="account-head">{name}</div>}
          {canChangePassword && (
            <button role="menuitem" onClick={() => { setOpen(false); onChangePassword(); }}>
              Change password
            </button>
          )}
          {isManager && (
            <button role="menuitem" onClick={() => { setOpen(false); onOrganize(); }}>
              Organize
            </button>
          )}
          {onSchedules && (
            <button role="menuitem" onClick={() => { setOpen(false); onSchedules(); }}>
              Schedules
            </button>
          )}
          {onLists && (
            <button role="menuitem" onClick={() => { setOpen(false); onLists(); }}>
              Lists
            </button>
          )}
          <button role="menuitem" className="danger" onClick={() => { setOpen(false); onLogout(); }}>
            Log out
          </button>
        </div>
      )}
    </div>
  );
}

// --- Climate Scheduler -------------------------------------------------------

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MODE_LABELS = { off: 'Off', cool: 'Cool', heat: 'Heat', auto: 'Auto', dry: 'Dry', fan: 'Fan only' };
const FAN_OPTIONS = ['auto', 'low', 'medium', 'high', 'diffuse', 'focus'];
const TEMP_MIN = 16;
const TEMP_MAX = 30;

/* Derive temp range/unit from the list of schedule entities (same as climate cards). */
function schedTempInfo(entities) {
  const a = (entities && entities[0] && entities[0].attributes) || {};
  const tMin = a.min_temp ?? TEMP_MIN;
  const tMax = a.max_temp ?? TEMP_MAX;
  const tStep = a.target_temp_step ?? 0.5;
  // HA provides temperature_unit on climate entities; fall back to inferring from range.
  const tUnit = a.temperature_unit || (tMin > 40 ? '°F' : '°C');
  return { tMin, tMax, tStep, tUnit };
}

function segColor(entry, tMin, tMax) {
  if (!entry) return 'var(--seg-empty)';
  const { mode, temp } = entry;
  if (mode === 'off') return '#4a4a4a';
  if (mode === 'fan') return '#43a047';
  if (mode === 'dry') return '#ffa726';
  const lo = tMin ?? TEMP_MIN;
  const hi = tMax ?? TEMP_MAX;
  const t = Math.max(lo, Math.min(hi, temp != null ? temp : (lo + hi) / 2));
  const f = (t - lo) / (hi - lo); // 0 = coldest, 1 = hottest
  if (mode === 'cool') {
    return `hsl(210,85%,${Math.round(42 + f * 22)}%)`;
  }
  if (mode === 'heat') {
    return `hsl(${Math.round(28 - f * 18)},88%,${Math.round(60 - f * 15)}%)`;
  }
  // auto: blue-to-orange gradient by temperature
  return `hsl(${Math.round(210 - f * 175)},85%,55%)`;
}

function segLabel(entry) {
  if (!entry || entry.mode === 'off') return '';
  if (entry.mode === 'fan' || entry.mode === 'dry') return MODE_LABELS[entry.mode] || entry.mode;
  return entry.temp != null ? `${entry.temp}°` : (MODE_LABELS[entry.mode] || entry.mode);
}

function daySegments(entries, dayIdx, tMin, tMax) {
  const evs = [];
  for (const e of entries) {
    if (!e.time) continue;
    const [h, m] = e.time.split(':').map(Number);
    const mins = h * 60 + m;
    for (const d of (e.days || [])) {
      evs.push({ absMin: d * 1440 + mins, entry: e });
    }
  }
  evs.sort((a, b) => a.absMin - b.absMin);
  if (!evs.length) return [{ pctStart: 0, pctEnd: 100, color: 'var(--seg-empty)', label: '' }];

  const dayStart = dayIdx * 1440;
  const before = evs.filter((e) => e.absMin < dayStart);
  const carry = before.length ? before[before.length - 1].entry : evs[evs.length - 1].entry;

  const dayEvs = evs
    .filter((e) => Math.floor(e.absMin / 1440) === dayIdx)
    .sort((a, b) => a.absMin - b.absMin);

  const segs = [];
  let cur = carry;
  let prevMin = 0;
  for (const { absMin, entry } of dayEvs) {
    const relMin = absMin - dayStart;
    if (relMin > prevMin) {
      segs.push({ pctStart: prevMin / 14.4, pctEnd: relMin / 14.4, color: segColor(cur, tMin, tMax), label: segLabel(cur) });
    }
    cur = entry;
    prevMin = relMin;
  }
  segs.push({ pctStart: prevMin / 14.4, pctEnd: 100, color: segColor(cur, tMin, tMax), label: '' });
  return segs;
}

const DAY_SHORT = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
const MODE_ABBR = { off: '–', cool: '❄', heat: '▲', auto: '⇅', dry: '∼', fan: '≈' };

/* SchedSwitch: button[role=switch] with sliding knob */
function SchedSwitch({ checked, onChange, disabled }) {
  return (
    <button
      role="switch"
      aria-checked={!!checked}
      type="button"
      className={`sched-switch${checked ? ' on' : ''}`}
      onClick={() => !disabled && onChange(!checked)}
      disabled={disabled}
    >
      <span className="sched-switch-knob" />
    </button>
  );
}

/* SchedSearchableMenu: searchable dropdown, single or multi select */
function SchedSearchableMenu({
  trigger, items, multi,
  selectedIds, selectedId,
  onSelect, onToggle,
  placeholder, emptyText, footer,
}) {
  const [open, setOpen] = React.useState(false);
  const [q, setQ] = React.useState('');
  const [hi, setHi] = React.useState(-1);
  const wrapRef = React.useRef(null);
  const inputRef = React.useRef(null);

  React.useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setOpen(false);
        setQ('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  React.useEffect(() => {
    if (open && inputRef.current) { inputRef.current.focus(); setHi(-1); }
  }, [open]);

  const filtered = (items || []).filter(
    (it) => !q.trim() || it.label.toLowerCase().includes(q.trim().toLowerCase())
  );

  function handleKey(e) {
    if (e.key === 'Escape') { setOpen(false); setQ(''); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
    else if (e.key === 'Enter' && hi >= 0 && hi < filtered.length) { e.preventDefault(); pick(filtered[hi]); }
  }

  function pick(item) {
    if (multi) {
      onToggle && onToggle(item.id);
    } else {
      onSelect && onSelect(item.id);
      setOpen(false);
      setQ('');
    }
  }

  return (
    <div className="sched-smenu-wrap" ref={wrapRef}>
      {React.cloneElement(trigger, { onClick: () => setOpen((o) => !o), 'aria-expanded': open })}
      {open && (
        <div className="sched-smenu">
          <div className="sched-smenu-search">
            <span className="sched-smenu-search-icon">&#9906;</span>
            <input
              ref={inputRef}
              className="sched-smenu-input"
              placeholder={placeholder || 'Search…'}
              value={q}
              onChange={(e) => { setQ(e.target.value); setHi(-1); }}
              onKeyDown={handleKey}
            />
          </div>
          <div className="sched-smenu-list">
            {filtered.length === 0 && (
              <div className="sched-smenu-empty">{emptyText || 'Nothing found'}</div>
            )}
            {filtered.map((item, idx) => {
              const sel = multi
                ? (selectedIds && selectedIds.has(item.id))
                : item.id === selectedId;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`sched-smenu-item${sel ? ' sel' : ''}${hi === idx ? ' hi' : ''}`}
                  onMouseEnter={() => setHi(idx)}
                  onClick={() => pick(item)}
                >
                  <span className="sched-smenu-item-label">{item.label}</span>
                  {item.sub && <span className="sched-smenu-sub">{item.sub}</span>}
                  {sel && <span className="sched-smenu-check">&#10003;</span>}
                </button>
              );
            })}
          </div>
          {footer && <div className="sched-smenu-footer">{footer}</div>}
        </div>
      )}
    </div>
  );
}

/* SchedWeekStrip: 7-day color bar with hour-axis ticks at 0, 6, 12, 18, 24 */
function SchedWeekStrip({ entries, tMin, tMax }) {
  const HOUR_TICKS = [0, 6, 12, 18, 24];
  return (
    <div className="sched-strip-wrap">
      <div className="sched-strip-axis">
        {HOUR_TICKS.map((h) => (
          <span key={h} className="sched-strip-tick" style={{ left: `${(h / 24) * 100}%` }}>
            {h === 0 || h === 24 ? '' : `${h}h`}
          </span>
        ))}
      </div>
      <div className="sched-strip">
        {DAY_LABELS.map((label, di) => {
          const segs = daySegments(entries, di, tMin, tMax);
          return (
            <div key={di} className="sched-strip-row">
              <span className="sched-strip-day">{label}</span>
              <div className="sched-strip-bar">
                {segs.map((seg, si) => (
                  <div
                    key={si}
                    className="sched-strip-seg"
                    style={{ left: `${seg.pctStart}%`, width: `${seg.pctEnd - seg.pctStart}%`, background: seg.color }}
                    title={seg.label}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* SchedEntryRow: 26x26 colored dot + mode abbr + opacity-based day letters */
function SchedEntryRow({ entry, onEdit, source, warn, tMin, tMax, tUnit }) {
  const unit = tUnit ?? '°';
  const tempStr = entry.temp != null ? ` · ${entry.temp}${unit}` : '';
  return (
    <div
      className="sched-entry-row"
      onClick={onEdit || undefined}
      role={onEdit ? 'button' : undefined}
      tabIndex={onEdit ? 0 : undefined}
      onKeyDown={(e) => e.key === 'Enter' && onEdit && onEdit()}
    >
      <div className="sched-entry-dot" style={{ background: segColor(entry, tMin, tMax) }}>
        <span className="sched-entry-dot-icon">{MODE_ABBR[entry.mode] || '?'}</span>
      </div>
      <div className="sched-entry-info">
        <div className="sched-entry-time-line">
          <span className="sched-entry-time">{entry.time}</span>
          <span className="sched-entry-mode-label">{MODE_LABELS[entry.mode] || entry.mode}{tempStr}</span>
        </div>
        <div className="sched-entry-days">
          {DAY_SHORT.map((d, i) => (
            <span key={i} style={{ opacity: entry.days.includes(i) ? 1 : 0.2 }}>{d}</span>
          ))}
        </div>
      </div>
      {source && <span className="sched-entry-source">{source}</span>}
      {warn && <span className="sched-entry-warn" title="Conflict">!</span>}
      {onEdit && <span className="sched-entry-action">&#8250;</span>}
    </div>
  );
}

/* SchedEntryEditor: bottom-sheet event editor, temp LEFT of slider */
function SchedEntryEditor({ entry, onSave, onCancel, onDelete, schedName, affects, tMin, tMax, tStep, tUnit }) {
  const lo = tMin ?? TEMP_MIN;
  const hi = tMax ?? TEMP_MAX;
  const step = tStep ?? 0.5;
  const unit = tUnit ?? '°C';
  const defaultTemp = entry?.temp != null ? entry.temp : Math.round((lo + hi) / 2 * 2) / 2;
  const [time, setTime] = React.useState(entry?.time || '07:00');
  const [days, setDays] = React.useState(new Set(entry?.days || [0, 1, 2, 3, 4]));
  const [mode, setMode] = React.useState(entry?.mode || 'heat');
  const [temp, setTemp] = React.useState(defaultTemp);
  const [fan, setFan] = React.useState(entry?.fan || '');

  const toggleDay = (d) => setDays((prev) => {
    const next = new Set(prev);
    next.has(d) ? next.delete(d) : next.add(d);
    return next;
  });

  const daysArr = [...days].sort((a, b) => a - b);
  const showTemp = mode !== 'off' && mode !== 'fan' && mode !== 'dry';

  const MODE_COLORS = {
    off: '#555', cool: '#2b9af9', heat: '#ff8100', auto: '#5B8DB8', dry: '#ffa726', fan: '#43a047',
  };

  function handleSave() {
    if (!daysArr.length) return;
    onSave({ id: entry?.id, time, days: daysArr, mode, temp: showTemp ? temp : null, fan: fan || null });
  }

  return (
    <div className="sched-sheet">
      <div className="sched-sheet-topbar">
        <button className="ghost" onClick={onCancel} type="button">Cancel</button>
        <h3 className="sched-sheet-title">{entry?.id ? 'Edit event' : 'Add event'}</h3>
        <button className="btn-primary" onClick={handleSave} disabled={!daysArr.length} type="button">Save</button>
      </div>

      {schedName && (
        <div className="sched-edit-ctx">
          In <strong>{schedName}</strong>
          {affects > 0 && <span> &middot; also affects {affects} thermostat{affects !== 1 ? 's' : ''}</span>}
        </div>
      )}

      <div className="sched-field-group">
        <span className="sched-field-label">Time</span>
        <input type="time" className="sched-time-input" value={time} onChange={(e) => setTime(e.target.value)} />
      </div>

      <div className="sched-field-group">
        <span className="sched-field-label">Days</span>
        <div className="sched-day-btn-row">
          {DAY_SHORT.map((d, i) => (
            <button key={i} type="button" className={`sched-day-toggle${days.has(i) ? ' on' : ''}`}
              onClick={() => toggleDay(i)}>{d}</button>
          ))}
        </div>
        <div className="sched-presets">
          <button type="button" className="ghost sched-preset-btn" onClick={() => setDays(new Set([0,1,2,3,4]))}>Weekdays</button>
          <button type="button" className="ghost sched-preset-btn" onClick={() => setDays(new Set([5,6]))}>Weekend</button>
          <button type="button" className="ghost sched-preset-btn" onClick={() => setDays(new Set([0,1,2,3,4,5,6]))}>Every day</button>
        </div>
      </div>

      <div className="sched-field-group">
        <span className="sched-field-label">Mode</span>
        <div className="sched-mode-grid">
          {Object.entries(MODE_LABELS).map(([k, v]) => (
            <button key={k} type="button"
              className={`sched-mode-btn${mode === k ? ' on' : ''}`}
              style={mode === k ? { borderColor: MODE_COLORS[k], background: MODE_COLORS[k] + '22' } : {}}
              onClick={() => setMode(k)}>
              <span className="sched-mode-dot" style={{ background: MODE_COLORS[k] }} />{v}
            </button>
          ))}
        </div>
      </div>

      {showTemp && (
        <div className="sched-field-group">
          <span className="sched-field-label">Temperature</span>
          <div className="sched-temp-row">
            <span className="sched-temp-big" style={{ color: segColor({ mode, temp }, lo, hi) }}>{temp}{unit}</span>
            <input type="range" min={lo} max={hi} step={step} value={temp}
              style={{ accentColor: segColor({ mode, temp }, lo, hi) }}
              onChange={(e) => { haptic(); setTemp(Number(e.target.value)); }} />
          </div>
          <div className="sched-range-ends"><span>{lo}{unit}</span><span>{hi}{unit}</span></div>
        </div>
      )}

      <div className="sched-field-group">
        <span className="sched-field-label">Fan speed</span>
        <div className="sched-fan-row">
          {['', ...FAN_OPTIONS.slice(0, 4)].map((f) => (
            <button key={f || '_auto'} type="button" className={`sched-fan-btn${fan === f ? ' on' : ''}`}
              onClick={() => setFan(f)}>{f || 'Auto'}</button>
          ))}
        </div>
      </div>

      {entry?.id && onDelete && (
        <button className="sched-danger-link" type="button"
          onClick={() => { onCancel(); onDelete(); }}>Delete event</button>
      )}
    </div>
  );
}

/* SchedMyView: my schedules list + editor (schedule state lifted to SchedulerPanel) */
function SchedMyView({ entities, schedules, setSchedules }) {
  const [selId, setSelId] = React.useState(null);
  const [editEntry, setEditEntry] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const nameInputRef = React.useRef(null);

  React.useEffect(() => {
    if (schedules && schedules.length && !selId) setSelId(schedules[0].id);
  }, [schedules]);

  const sched = schedules ? schedules.find((s) => s.id === selId) || null : null;
  const entityName = (id) => (entities || []).find((e) => e.entity_id === id)?.name || id;
  const tInfo = schedTempInfo(entities);

  async function createNew() {
    setBusy(true);
    setError('');
    try {
      const existing = (schedules || []).map((s) => s.name);
      let n = (schedules || []).length + 1;
      while (existing.includes(`Schedule ${n}`)) n++;
      const s = await createSchedule({ name: `Schedule ${n}`, targets: [], entries: [] });
      setSchedules((prev) => [...(prev || []), s]);
      setSelId(s.id);
      setTimeout(() => {
        if (nameInputRef.current) { nameInputRef.current.focus(); nameInputRef.current.select(); }
      }, 60);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function patchSched(id, patch) {
    setBusy(true); setError('');
    try {
      const updated = await updateSchedule(id, patch);
      setSchedules((prev) => prev.map((s) => (s.id === id ? updated : s)));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function removeSched(id) {
    if (!window.confirm('Delete this schedule?')) return;
    setBusy(true); setError('');
    try {
      await deleteSchedule(id);
      setSchedules((prev) => {
        const next = prev.filter((s) => s.id !== id);
        setSelId(next.length ? next[0].id : null);
        return next;
      });
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function saveEntry(entryData) {
    if (!sched) return;
    const entries = entryData.id
      ? sched.entries.map((e) => (e.id === entryData.id ? entryData : e))
      : [...sched.entries, entryData];
    await patchSched(sched.id, { entries });
    setEditEntry(null);
  }

  async function deleteEntry(entryId) {
    if (!sched) return;
    await patchSched(sched.id, { entries: sched.entries.filter((e) => e.id !== entryId) });
  }

  function removeTarget(eid) {
    if (!sched) return;
    patchSched(sched.id, { targets: sched.targets.filter((t) => t !== eid) });
  }

  function addTarget(eid) {
    if (!sched || sched.targets.includes(eid)) return;
    patchSched(sched.id, { targets: [...sched.targets, eid] });
  }

  const schedItems = (schedules || []).map((s) => ({
    id: s.id,
    label: s.name,
    sub: `${s.entries.length} event${s.entries.length !== 1 ? 's' : ''}`,
  }));

  const addableEntities = (entities || []).filter(
    (en) => !sched || !sched.targets.includes(en.entity_id)
  );

  const sortedEntries = sched ? [...sched.entries].sort((a, b) => a.time.localeCompare(b.time)) : [];

  if (!schedules) return <p className="muted">Loading…</p>;

  return (
    <div className="sched-view">
      {error && <div className="error banner">{error}</div>}

      <SchedSearchableMenu
        trigger={
          <button className="sched-switcher" type="button">
            <span className="sched-status-dot" style={{
              background: sched ? (sched.enabled ? 'var(--accent)' : 'var(--muted)') : 'var(--muted)'
            }} />
            <span className="sched-switcher-label">{sched ? sched.name : 'No schedules yet'}</span>
            {sched && <span className="sched-switcher-meta">{sched.entries.length} event{sched.entries.length !== 1 ? 's' : ''}</span>}
            <span className="sched-caret">&#9660;</span>
          </button>
        }
        items={schedItems}
        selectedId={selId}
        onSelect={(id) => setSelId(id)}
        placeholder="Search schedules…"
        emptyText="No schedules found"
        footer={
          <button className="sched-menu-create" type="button" onClick={createNew} disabled={busy}>
            + Create schedule
          </button>
        }
      />

      {!sched && (schedules || []).length === 0 && (
        <div className="sched-card">
          <p className="muted" style={{ margin: 0 }}>No schedules yet. Use the menu above to create one.</p>
        </div>
      )}

      {sched && (
        <div className="sched-card">
          <div className="sched-card-header">
            <input
              ref={nameInputRef}
              className="sched-name-input"
              value={sched.name}
              onChange={(e) => setSchedules((prev) => prev.map((s) =>
                s.id === sched.id ? { ...s, name: e.target.value } : s))}
              onBlur={(e) => { if (e.target.value.trim()) patchSched(sched.id, { name: e.target.value.trim() }); }}
              onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
            />
            <SchedSwitch checked={sched.enabled} disabled={busy}
              onChange={(v) => patchSched(sched.id, { enabled: v })} />
            <button className="ghost" type="button" onClick={() => removeSched(sched.id)}
              disabled={busy} title="Delete schedule">&#x1F5D1;</button>
          </div>

          <div className="sched-sub">
            <span className="sched-unit-title">Thermostats</span>
            {entities.length === 0 ? (
              <p className="muted">No climate devices granted. Ask an admin.</p>
            ) : (
              <div className="sched-chip-row">
                {(sched.targets || []).map((eid) => (
                  <span key={eid} className="sched-chip">
                    {entityName(eid)}
                    <button className="sched-chip-x" type="button"
                      onClick={() => removeTarget(eid)} aria-label={`Remove ${entityName(eid)}`}>&#215;</button>
                  </span>
                ))}
                {addableEntities.length > 0 && (
                  <SchedSearchableMenu
                    trigger={<button className="sched-chip sched-chip-add" type="button">+ AC</button>}
                    items={addableEntities.map((en) => ({ id: en.entity_id, label: en.name }))}
                    onSelect={(id) => addTarget(id)}
                    placeholder="Search…"
                    emptyText="All added"
                  />
                )}
              </div>
            )}
          </div>

          {sortedEntries.length > 0 && (
            <div className="sched-sub">
              <span className="sched-unit-title">Week preview</span>
              <SchedWeekStrip entries={sortedEntries} tMin={tInfo.tMin} tMax={tInfo.tMax} />
            </div>
          )}

          <div className="sched-sub">
            <span className="sched-unit-title">Events</span>
            {sortedEntries.length === 0 ? (
              <p className="muted">No events yet.</p>
            ) : (
              sortedEntries.map((e) => (
                <SchedEntryRow key={e.id} entry={e} onEdit={() => setEditEntry(e)} tMin={tInfo.tMin} tMax={tInfo.tMax} tUnit={tInfo.tUnit} />
              ))
            )}
            <button className="sched-add-btn" type="button" onClick={() => setEditEntry({})}>
              + Add event
            </button>
          </div>
        </div>
      )}

      {editEntry !== null && (
        <div className="sched-overlay" onClick={(e) => e.target === e.currentTarget && setEditEntry(null)}>
          <SchedEntryEditor
            entry={editEntry.id ? editEntry : null}
            schedName={sched?.name}
            affects={Math.max(0, (sched?.targets || []).length - 1)}
            onSave={saveEntry}
            onCancel={() => setEditEntry(null)}
            onDelete={editEntry.id ? () => { deleteEntry(editEntry.id); setEditEntry(null); } : null}
            tMin={tInfo.tMin} tMax={tInfo.tMax} tStep={tInfo.tStep} tUnit={tInfo.tUnit}
          />
        </div>
      )}
    </div>
  );
}

/* SchedByThermostat: view merged events per thermostat, with searchable picker */
function SchedByThermostat({ entities, schedules }) {
  const [selEntityId, setSelEntityId] = React.useState((entities[0] || {}).entity_id || '');

  if (!entities.length) {
    return (
      <div className="sched-view">
        <p className="muted">No permitted climate entities.</p>
      </div>
    );
  }

  const selEntity = entities.find((e) => e.entity_id === selEntityId) || entities[0];
  const tInfo = schedTempInfo([selEntity]);
  const targetSchedules = (schedules || []).filter(
    (s) => s.enabled && s.targets.includes(selEntity.entity_id)
  );

  const merged = [];
  const byKey = {};
  for (const s of targetSchedules) {
    for (const e of s.entries) {
      for (const d of e.days) {
        const key = `${d}:${e.time}`;
        if (!byKey[key]) byKey[key] = [];
        byKey[key].push(s.name);
      }
      merged.push(e);
    }
  }
  const conflictCount = Object.values(byKey).filter((v) => v.length > 1).length;

  const entItems = entities.map((e) => ({ id: e.entity_id, label: e.name }));

  return (
    <div className="sched-view">
      <SchedSearchableMenu
        trigger={
          <button className="sched-switcher" type="button">
            <span className="sched-switcher-label">{selEntity.name}</span>
            <span className="sched-caret">&#9660;</span>
          </button>
        }
        items={entItems}
        selectedId={selEntity.entity_id}
        onSelect={(id) => setSelEntityId(id)}
        placeholder="Search thermostats…"
        emptyText="None found"
      />

      <div className="sched-card">
        {targetSchedules.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>No active schedules target this thermostat.</p>
        ) : (
          <>
            <SchedWeekStrip entries={merged} tMin={tInfo.tMin} tMax={tInfo.tMax} />
            {conflictCount > 0 && (
              <div className="sched-clash-note">
                Conflict: {conflictCount} time slot{conflictCount > 1 ? 's' : ''} overlap.
              </div>
            )}
            {targetSchedules.map((s) => {
              return (
                <div key={s.id} className="sched-sub">
                  <span className="sched-unit-title">{s.name}</span>
                  <span className="muted" style={{ fontSize: '0.82em' }}>
                    by {s.owner} &middot; {s.entries.length} event{s.entries.length !== 1 ? 's' : ''}
                  </span>
                  {s.entries.map((e) => {
                    const hasConflict = e.days.some((d) => (byKey[`${d}:${e.time}`] || []).length > 1);
                    return <SchedEntryRow key={e.id} entry={e} source={s.name} warn={hasConflict} tMin={tInfo.tMin} tMax={tInfo.tMax} tUnit={tInfo.tUnit} />;
                  })}
                </div>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}

/* SchedulerPanel: main panel, lifts schedule+entity state */
function SchedulerPanel() {
  const [view, setView] = React.useState('mine');
  const [entities, setEntities] = React.useState([]);
  const [schedules, setSchedules] = React.useState(null);
  const [error, setError] = React.useState('');

  React.useEffect(() => {
    Promise.all([getScheduleEntities(), getMySchedules()])
      .then(([entData, scheds]) => {
        setEntities(entData.entities || []);
        setSchedules(scheds);
      })
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div className="sched-panel">
      <div className="sched-topbar">
        <h2 style={{ margin: 0 }}>Schedules</h2>
      </div>
      <div className="tabs" style={{ marginBottom: 16 }}>
        <button className={`seg${view === 'mine' ? ' on' : ''}`} onClick={() => setView('mine')} type="button">My schedules</button>
        <button className={`seg${view === 'thermostat' ? ' on' : ''}`} onClick={() => setView('thermostat')} type="button">By thermostat</button>
      </div>
      {error && <div className="error banner">{error}</div>}
      {view === 'mine' ? (
        <SchedMyView entities={entities} schedules={schedules} setSchedules={setSchedules} />
      ) : (
        <SchedByThermostat entities={entities} schedules={schedules || []} />
      )}
    </div>
  );
}

/* SchedAdminSchedRow: collapsible schedule row for the admin "All schedules" tab */
function SchedAdminSchedRow({ sched, entities, onToggle, onDelete }) {
  const [open, setOpen] = React.useState(false);
  const entityName = (id) => (entities || []).find((e) => e.entity_id === id)?.name || id;

  return (
    <div className="sched-admin-sched">
      <div className="sched-admin-sched-head">
        <button className="sched-admin-chevron" type="button" onClick={() => setOpen((o) => !o)}>
          {open ? '▾' : '▸'}
        </button>
        <div className="sched-admin-sched-main">
          <span className="sched-admin-sched-name">{sched.name}</span>
          <span className="sched-admin-meta">
            {sched.entries.length} event{sched.entries.length !== 1 ? 's' : ''}
            {sched.targets.length > 0 && ` · ${sched.targets.length} thermostat${sched.targets.length !== 1 ? 's' : ''}`}
          </span>
        </div>
        <SchedSwitch checked={sched.enabled} onChange={() => onToggle(sched)} />
        <button className="sched-admin-trash" type="button" onClick={() => onDelete(sched)}>&#x1F5D1;</button>
      </div>
      {open && (
        <div className="sched-admin-body">
          {sched.targets.length > 0 && (
            <div className="sched-admin-targets">
              {sched.targets.map((t) => (
                <span key={t} className="sched-chip">{entityName(t)}</span>
              ))}
            </div>
          )}
          {sched.entries.length === 0 ? (
            <p className="muted" style={{ margin: '6px 0' }}>No events.</p>
          ) : (
            [...sched.entries].sort((a, b) => a.time.localeCompare(b.time)).map((e) => (
              <SchedEntryRow key={e.id} entry={e} />
            ))
          )}
        </div>
      )}
    </div>
  );
}

/* AdminSchedulesView: admin sub-panel with Access (2-col grid) and All schedules (grouped+collapsible) */
function AdminSchedulesView() {
  const [subTab, setSubTab] = React.useState('access');
  const [schedules, setSchedules] = React.useState(null);
  const [users, setUsers] = React.useState(null);
  const [climateEntities, setClimateEntities] = React.useState([]);
  const [perms, setPerms] = React.useState({});
  const [error, setError] = React.useState('');
  const [permBusy, setPermBusy] = React.useState(false);

  React.useEffect(() => {
    Promise.all([adminGetAllSchedules(), adminGetUsers(), adminGetClimateEntities(), adminGetSchedulePerms()])
      .then(([scheds, usersData, entData, permsData]) => {
        setSchedules(scheds);
        setUsers(usersData.users);
        setClimateEntities(entData.entities || []);
        setPerms(permsData);
      })
      .catch((e) => setError(e.message));
  }, []);

  async function toggleEnabled(s) {
    try {
      const updated = await adminPatchSchedule(s.id, { enabled: !s.enabled });
      setSchedules((prev) => prev.map((x) => (x.id === s.id ? updated : x)));
    } catch (e) { setError(e.message); }
  }

  async function removeSched(s) {
    if (!window.confirm(`Delete schedule "${s.name}" by ${s.owner}?`)) return;
    try {
      await adminDeleteSchedule(s.id);
      setSchedules((prev) => prev.filter((x) => x.id !== s.id));
    } catch (e) { setError(e.message); }
  }

  async function togglePerm(username, entityId, nowOn) {
    setPermBusy(true);
    try {
      const current = new Set(perms[username] || []);
      if (nowOn) current.add(entityId); else current.delete(entityId);
      await adminSetSchedulePerms(username, [...current]);
      setPerms((prev) => ({ ...prev, [username]: [...current] }));
    } catch (e) { setError(e.message); }
    finally { setPermBusy(false); }
  }

  function userHasDevice(user, entityId) {
    return user.all || user.manager || (user.entities || []).includes(entityId);
  }

  const loading = !schedules || !users;

  const userMap = {};
  (users || []).forEach((u) => { userMap[u.username] = u; });

  const grouped = schedules
    ? [...new Set(schedules.map((s) => s.owner))].map((owner) => ({
        owner,
        scheds: schedules.filter((s) => s.owner === owner),
      }))
    : [];

  return (
    <div>
      <div className="tabs" style={{ marginBottom: 16 }}>
        <button className={`seg${subTab === 'access' ? ' on' : ''}`} onClick={() => setSubTab('access')} type="button">Access</button>
        <button className={`seg${subTab === 'schedules' ? ' on' : ''}`} onClick={() => setSubTab('schedules')} type="button">All schedules</button>
      </div>

      {error && <div className="error banner">{error}</div>}
      {loading && <p className="muted">Loading…</p>}

      {!loading && subTab === 'access' && (
        <div>
          {climateEntities.length === 0 ? (
            <p className="muted">No climate entities found.</p>
          ) : (
            (users || []).map((u) => {
              const userPerms = new Set(perms[u.username] || []);
              return (
                <div key={u.username} className="sched-admin-user-section">
                  <div className="sched-admin-user-label">
                    {u.displayName || u.username}
                    {u.admin && <span className="sched-admin-badge">admin</span>}
                  </div>
                  <div className="sched-perm-grid">
                    {climateEntities.map((e) => {
                      const eligible = userHasDevice(u, e.entity_id);
                      const on = userPerms.has(e.entity_id);
                      return (
                        <button
                          key={e.entity_id}
                          type="button"
                          className={`sched-perm-cell${on ? ' on' : ''}${!eligible ? ' locked' : ''}`}
                          onClick={() => eligible && !permBusy && togglePerm(u.username, e.entity_id, !on)}
                          disabled={!eligible}
                        >
                          <span className="sched-perm-cell-name">{e.name}</span>
                          <span className="sched-perm-check">
                            {!eligible ? '🔒' : on ? '✓' : ''}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })
          )}
          <p className="muted" style={{ fontSize: '0.78em', marginTop: 12 }}>
            Locked devices require device-control access first (Users tab).
          </p>
        </div>
      )}

      {!loading && subTab === 'schedules' && (
        <div>
          {grouped.length === 0 ? (
            <p className="muted">No schedules created yet.</p>
          ) : (
            grouped.map(({ owner, scheds }) => (
              <div key={owner} className="sched-admin-user-section">
                <div className="sched-admin-user-label">
                  {userMap[owner]?.displayName || owner}
                  <span className="sched-admin-count">{scheds.length} schedule{scheds.length !== 1 ? 's' : ''}</span>
                </div>
                {scheds.map((s) => (
                  <SchedAdminSchedRow
                    key={s.id}
                    sched={s}
                    entities={climateEntities}
                    onToggle={toggleEnabled}
                    onDelete={removeSched}
                  />
                ))}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

/* ListsManager: create/rename/delete per-user device lists and assign devices
   to them (reuses EntityChips). Rendered as a full panel like SchedulerPanel;
   the shared topbar "Done" leaves the view. onChange() lets the dashboard
   refresh its filter chips after edits. */
function ListsManager({ onChange }) {
  const [lists, setLists] = React.useState(null);
  const [devices, setDevices] = React.useState([]);
  const [error, setError] = React.useState('');
  const [newName, setNewName] = React.useState('');
  const [editingId, setEditingId] = React.useState(null);

  const reload = React.useCallback(() => {
    Promise.all([getLists(), getDevices()])
      .then(([ls, d]) => { setLists(ls); setDevices(d.devices || []); })
      .catch((e) => setError(e.message));
  }, []);
  React.useEffect(() => { reload(); }, [reload]);

  const notify = () => { if (onChange) onChange(); };

  async function addList() {
    const name = newName.trim();
    if (!name) return;
    try {
      const created = await createList({ name });
      setNewName('');
      setLists((prev) => [...(prev || []), created]);
      setEditingId(created.id);
      notify();
    } catch (e) { setError(e.message); }
  }

  async function rename(id, name) {
    const clean = name.trim();
    if (!clean) { reload(); return; }
    try {
      const updated = await updateList(id, { name: clean });
      setLists((prev) => prev.map((l) => (l.id === id ? updated : l)));
      notify();
    } catch (e) { setError(e.message); }
  }

  async function remove(id) {
    if (!window.confirm('Delete this list? Devices are not affected.')) return;
    try {
      await deleteList(id);
      setLists((prev) => prev.filter((l) => l.id !== id));
      if (editingId === id) setEditingId(null);
      notify();
    } catch (e) { setError(e.message); }
  }

  async function toggleDevice(id, entityId) {
    const lst = (lists || []).find((l) => l.id === id);
    if (!lst) return;
    const set = new Set(lst.entities || []);
    set.has(entityId) ? set.delete(entityId) : set.add(entityId);
    const entities = [...set];
    setLists((prev) => prev.map((l) => (l.id === id ? { ...l, entities } : l))); // optimistic
    try {
      await updateList(id, { entities });
      notify();
    } catch (e) { setError(e.message); reload(); }
  }

  return (
    <div className="sched-panel">
      <div className="sched-topbar">
        <h2 style={{ margin: 0 }}>Lists</h2>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Group your devices your own way. Each list shows as a filter chip on the dashboard.
      </p>
      {error && <div className="error banner">{error}</div>}

      <div className="list-create">
        <input
          className="list-name-input"
          value={newName}
          placeholder="New list name…"
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') addList(); }}
        />
        <button className="btn-primary" type="button" onClick={addList} disabled={!newName.trim()}>
          Create list
        </button>
      </div>

      {lists === null ? (
        <p className="muted">Loading…</p>
      ) : lists.length === 0 ? (
        <p className="muted">No lists yet. Create one above.</p>
      ) : (
        lists.map((l) => {
          const count = (l.entities || []).length;
          const editing = editingId === l.id;
          return (
            <div key={l.id} className="card list-row">
              <div className="list-head">
                <input
                  className="list-name-input list-name-edit"
                  value={l.name}
                  onChange={(e) => setLists((prev) => prev.map((x) => (x.id === l.id ? { ...x, name: e.target.value } : x)))}
                  onBlur={(e) => rename(l.id, e.target.value)}
                  aria-label="List name"
                />
                <span className="muted list-count">{count} device{count !== 1 ? 's' : ''}</span>
                <button className="ghost" type="button" onClick={() => setEditingId(editing ? null : l.id)}>
                  {editing ? 'Close' : 'Edit devices'}
                </button>
                <button className="ghost danger" type="button" onClick={() => remove(l.id)}>Delete</button>
              </div>
              {editing && (
                <div className="list-assign">
                  <EntityChips
                    entities={devices}
                    selected={new Set(l.entities || [])}
                    onToggle={(eid) => toggleDevice(l.id, eid)}
                    placeholder="Add a device to this list…"
                  />
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

function Dashboard({
  displayName,
  onLogout,
  live = true,
  title = 'Control Center',
  appIcon = '',
  appImage = null,
  isManager = false,
  picture = '',
  canChangePassword = false,
  passwordRules = null,
}) {
  // The one top-level view. Single source of truth so views are mutually
  // exclusive; adding a new view later is just another value here, no extra flags.
  const [view, setView] = useState('none'); // 'none' (devices) | 'organize' | 'schedules' | 'lists'
  const [showPw, setShowPw] = useState(false);
  const [hasSchedPerms, setHasSchedPerms] = useState(false);
  const [devices, setDevices] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  // Manager-only: device/area data for the per-card edit popup.
  const [mgrData, setMgrData] = useState(null); // { devices, areas }
  const [editDevice, setEditDevice] = useState(null);

  useEffect(() => {
    if (!isManager) return;
    managerGetDevices()
      .then(setMgrData)
      .catch(() => {});
  }, [isManager]);

  useEffect(() => {
    getScheduleEntities()
      .then((d) => setHasSchedPerms((d.entities || []).length > 0))
      .catch(() => {});
  }, []);

  function openDeviceEdit(entity) {
    if (!mgrData || !entity.device_id) return;
    const dev = mgrData.devices.find((d) => d.id === entity.device_id);
    setEditDevice(dev || { id: entity.device_id, name: entity.name, area_id: null, entities: [] });
  }

  async function saveDeviceEdit(device_id, fields) {
    await managerUpdateDevice(device_id, fields);
    setEditDevice(null);
    refresh(); // device cards (names/areas) update
    managerGetDevices().then(setMgrData).catch(() => {});
  }
  const [compact, setCompact] = useState(localStorage.getItem(COMPACT_KEY) === '1');
  const [groupBy, setGroupBy] = useState(localStorage.getItem(GROUPBY_KEY) || 'type');
  // Per-user lists (tags). The active selection lives in `groupBy`, which holds
  // 'type' / 'area' / 'floor' OR a list id - a list is an alternative to those.
  const [lists, setLists] = useState([]);
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '[]'));
    } catch {
      return new Set();
    }
  });
  // Areas under a floor start collapsed; this tracks the ones the user opened.
  const [openAreas, setOpenAreas] = useState(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(OPEN_AREAS_KEY) || '[]'));
    } catch {
      return new Set();
    }
  });

  function toggleCompact() {
    setCompact((c) => {
      const next = !c;
      localStorage.setItem(COMPACT_KEY, next ? '1' : '0');
      return next;
    });
  }

  function chooseGroupBy(v) {
    setGroupBy(v);
    localStorage.setItem(GROUPBY_KEY, v);
  }

  const refreshLists = useCallback(() => {
    getLists().then((ls) => setLists(Array.isArray(ls) ? ls : [])).catch(() => {});
  }, []);
  // Load lists on first paint and whenever returning to the device view, so a
  // list created/renamed/deleted in the manager updates the filter chips.
  useEffect(() => {
    if (view === 'none') refreshLists();
  }, [view, refreshLists]);

  function toggleCollapse(key) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next]));
      return next;
    });
  }

  function toggleArea(key) {
    setOpenAreas((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      localStorage.setItem(OPEN_AREAS_KEY, JSON.stringify([...next]));
      return next;
    });
  }

  // Live-connection state for the "Connection lost" toast.
  const [connected, setConnected] = useState(true);
  const [lost, setLost] = useState(false);

  // Transient toast for a failed control command, so a rejected action (e.g. an
  // unreachable remote instance) shows a reason instead of silently snapping back.
  const [cmdError, setCmdError] = useState('');
  const cmdErrorTimer = useRef(null);
  const flashCmdError = useCallback((msg) => {
    setCmdError(msg || 'Command failed');
    clearTimeout(cmdErrorTimer.current);
    cmdErrorTimer.current = setTimeout(() => setCmdError(''), 4000);
  }, []);

  // Fallback fetch (used for first paint and if the live stream drops).
  const refresh = useCallback(async () => {
    try {
      const data = await getDevices();
      setDevices(data.devices || []);
      setError('');
      setConnected(true);
    } catch (err) {
      setError(err.message);
      setConnected(false);
    } finally {
      setLoading(false);
    }
  }, []);

  // Only show "connection lost" after a short grace, so a quick blip (or the
  // add-on restarting during an update) doesn't flash the toast.
  useEffect(() => {
    if (connected) {
      setLost(false);
      return;
    }
    const t = setTimeout(() => setLost(true), 2500);
    return () => clearTimeout(t);
  }, [connected]);

  // Apply one pushed change to the device list.
  function applyUpdate(prev, u) {
    if (!u.state) return prev.filter((d) => d.entity_id !== u.entity_id);
    const i = prev.findIndex((d) => d.entity_id === u.entity_id);
    if (i === -1) return [...prev, u.state];
    const next = prev.slice();
    // Pushed updates don't carry room/floor - keep them from the prior entry.
    next[i] = { ...prev[i], ...u.state };
    return next;
  }

  useEffect(() => {
    refresh(); // first paint via REST (works even if the live stream is down)

    // Polling mode (e.g. local preview): no persistent connection, so the page
    // reaches network-idle and stays interactive/screenshottable.
    if (!live) {
      const id = setInterval(refresh, 3000);
      return () => clearInterval(id);
    }

    // Live updates over a WebSocket (proxy/Cloudflare-friendly). WebSockets
    // don't auto-reconnect, so we reconnect ourselves and re-sync over REST on
    // each (re)open. The token rides as a query param.
    // Single-flight reconnect: only ever one socket exists. A superseded socket
    // has its handlers detached before it is closed, so a stale close can't
    // schedule a duplicate reconnect, flip `connected`, or leak a server-side
    // connection. Leaks matter: each live socket holds one of gunicorn's worker
    // threads, so churning/leaking sockets could starve the server and hang both
    // the REST refresh and the next reconnect - which is what broke "Retry now"
    // and auto-refresh once the connection dropped.
    let ws = null;
    let retry = null;
    let stopped = false;
    let opened = false;

    function teardown(sock) {
      if (!sock) return;
      sock.onopen = sock.onmessage = sock.onclose = sock.onerror = null;
      try {
        sock.close();
      } catch {}
    }

    function scheduleReconnect() {
      if (stopped || retry) return; // never stack reconnect timers
      retry = setTimeout(() => {
        retry = null;
        connect();
      }, 3000);
    }

    function connect() {
      if (stopped) return;
      clearTimeout(retry);
      retry = null;
      teardown(ws); // supersede any prior/in-flight socket without re-triggering
      const u = new URL('api/ws', document.baseURI);
      u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
      u.searchParams.set('token', getToken() || '');
      const sock = new WebSocket(u.href);
      ws = sock;
      sock.onopen = () => {
        if (ws !== sock) return;
        setConnected(true);
        if (opened) refresh(); // reconnect: catch up on anything missed
        opened = true;
      };
      sock.onmessage = (e) => {
        if (ws !== sock) return;
        let m;
        try {
          m = JSON.parse(e.data);
        } catch {
          return;
        }
        if (m && m.entity_id) setDevices((prev) => applyUpdate(prev, m));
      };
      sock.onclose = () => {
        if (ws !== sock) return; // ignore a socket we already superseded
        setConnected(false);
        scheduleReconnect();
      };
      sock.onerror = () => {
        try {
          sock.close();
        } catch {}
      };
    }
    connect();

    // Safety net so the dashboard can't go stale if the socket is unavailable.
    const pollId = setInterval(refresh, 30000);

    return () => {
      stopped = true;
      clearTimeout(retry);
      clearInterval(pollId);
      teardown(ws);
    };
  }, [refresh, live]);

  const q = query.trim().toLowerCase();
  const searched = q
    ? devices.filter(
        (d) => d.name.toLowerCase().includes(q) || d.entity_id.toLowerCase().includes(q)
      )
    : devices;
  // A list is an alternative to Type/Area/Floor: when `groupBy` is a list id,
  // show only that list's devices (grouped by Type).
  const activeList = lists.find((l) => l.id === groupBy) || null;
  const activeSet = activeList ? new Set(activeList.entities || []) : null;
  const visible = activeSet ? searched.filter((d) => activeSet.has(d.entity_id)) : searched;

  const hasRooms = devices.some((d) => d.area);
  const hasFloors = devices.some((d) => d.floor);
  const dense = devices.length >= GROUPING_THRESHOLD; // collapsible + group-by
  const OTHER = 'Other';
  // Resolve the grouping ('room' is the legacy name for 'area'). A list groups
  // by Type; fall back to Type for anything without data (or a deleted list).
  let mode = activeList ? 'type' : groupBy === 'room' ? 'area' : groupBy;
  if (mode === 'area' && !hasRooms) mode = 'type';
  if (mode === 'floor' && !hasFloors) mode = 'type';
  if (mode !== 'area' && mode !== 'floor') mode = 'type';
  const byLocation = mode === 'area' || mode === 'floor';

  const groupKeyOf = (d) =>
    mode === 'area' ? d.area || OTHER : mode === 'floor' ? d.floor || OTHER : d.domain;
  const groupLabelOf = (key) => (mode === 'type' ? domainLabel(key) : key);

  const groups = {};
  for (const d of visible) {
    const k = groupKeyOf(d);
    if (!groups[k]) groups[k] = [];
    groups[k].push(d);
  }
  for (const list of Object.values(groups)) {
    // Within an area/floor, order by type then name; within a type, by name.
    list.sort((a, b) =>
      byLocation
        ? a.domain.localeCompare(b.domain) || a.name.localeCompare(b.name)
        : a.name.localeCompare(b.name)
    );
  }
  // Order groups: areas/floors alphabetically ("Other" last); types by label.
  const keys = Object.keys(groups).sort((x, y) => {
    if (byLocation) {
      if (x === OTHER) return 1;
      if (y === OTHER) return -1;
      return x.localeCompare(y);
    }
    return groupLabelOf(x).localeCompare(groupLabelOf(y));
  });

  // Area name -> its HA mdi icon (for the group headers). Areas without an
  // icon set simply get no glyph.
  const areaIconOf = {};
  for (const d of devices) {
    if (d.area && d.area_icon && !(d.area in areaIconOf)) areaIconOf[d.area] = d.area_icon;
  }

  // Floor mode nests areas under each floor: returns [[areaName, devices], …].
  const subgroupByArea = (list) => {
    const m = {};
    for (const d of list) {
      const a = d.area || OTHER;
      (m[a] = m[a] || []).push(d);
    }
    return Object.keys(m)
      .sort((x, y) => (x === OTHER ? 1 : y === OTHER ? -1 : x.localeCompare(y)))
      .map((name) => [name, m[name]]);
  };

  return (
    <div className={`dashboard${compact ? ' compact' : ''}`}>
      <header className="topbar">
        <div>
          <h1><BrandIcon icon={appIcon} image={appImage} /> {title}</h1>
          {displayName && <span className="muted">Hi, {displayName}</span>}
        </div>
        <div className="topbar-actions">
          {view !== 'none' && (
            <button className="ghost" onClick={() => setView('none')}>
              Done
            </button>
          )}
          {view === 'none' && devices.length > 4 && (
            <button
              className="ghost icon-only"
              onClick={toggleCompact}
              title={compact ? 'Comfortable view' : 'Compact view'}
              aria-pressed={compact}
            >
              {compact ? '▦' : '▤'}
            </button>
          )}
          <ThemeToggle />
          <InstallButton />
          <AccountMenu
            name={displayName}
            picture={picture}
            isManager={isManager}
            canChangePassword={canChangePassword}
            onChangePassword={() => setShowPw(true)}
            onOrganize={() => setView('organize')}
            onSchedules={hasSchedPerms ? () => setView('schedules') : undefined}
            onLists={() => setView('lists')}
            onLogout={onLogout}
          />
        </div>
      </header>

      {showPw && (
        <ChangePasswordDialog rules={passwordRules} onClose={() => setShowPw(false)} />
      )}

      {view === 'schedules' ? (
        <SchedulerPanel />
      ) : view === 'organize' ? (
        <Organize />
      ) : view === 'lists' ? (
        <ListsManager onChange={refreshLists} />
      ) : (
      <>{/* normal dashboard */}

      {error && <div className="error banner">{error}</div>}
      {!loading && devices.length > 2 && (
        <SearchBox
          className="dashboard-search"
          placeholder="Search devices…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      {!loading && (lists.length > 0 || (dense && (hasRooms || hasFloors))) && (
        <div className="group-by dashboard-groupby">
          <button
            type="button"
            className={`seg ${!activeList && mode === 'type' ? 'on' : ''}`}
            onClick={() => chooseGroupBy('type')}
          >
            Type
          </button>
          {hasRooms && (
            <button
              type="button"
              className={`seg ${!activeList && mode === 'area' ? 'on' : ''}`}
              onClick={() => chooseGroupBy('area')}
            >
              Area
            </button>
          )}
          {hasFloors && (
            <button
              type="button"
              className={`seg ${!activeList && mode === 'floor' ? 'on' : ''}`}
              onClick={() => chooseGroupBy('floor')}
            >
              Floor
            </button>
          )}
          {lists.map((l) => (
            <button
              key={l.id}
              type="button"
              className={`seg ${groupBy === l.id ? 'on' : ''}`}
              onClick={() => chooseGroupBy(l.id)}
            >
              {l.name}
            </button>
          ))}
        </div>
      )}
      {loading ? (
        <p className="muted">Loading your devices…</p>
      ) : devices.length === 0 ? (
        <div className="onboarding">
          <div className="onboarding-icon">👋</div>
          <h2>You're signed in</h2>
          <p className="muted">
            No devices have been assigned to you yet. Please reach out to your system
            administrator to get set up.
          </p>
        </div>
      ) : keys.length === 0 ? (
        query ? (
          <p className="muted">No devices match “{query}”.</p>
        ) : activeList ? (
          <p className="muted">“{activeList.name}” has no devices yet. Add some from Lists in the account menu.</p>
        ) : (
          <p className="muted">No devices to show.</p>
        )
      ) : (
        keys.map((key) => {
          const ckey = `${mode}:${key}`;
          const open = !dense || !collapsed.has(ckey);
          return (
            <section key={ckey}>
              {dense ? (
                <button
                  type="button"
                  className="section-head"
                  onClick={() => toggleCollapse(ckey)}
                  aria-expanded={open}
                >
                  <span className={`acc-caret ${open ? 'open' : ''}`}>▸</span>
                  {mode === 'area' && areaIconOf[key] && (
                    <MdiIcon icon={areaIconOf[key]} size={20} className="section-icon" />
                  )}
                  <span className="section-title">{groupLabelOf(key)}</span>
                  <span className="section-count muted">{groups[key].length}</span>
                </button>
              ) : (
                <h2 className="section-h2">
                  {mode === 'area' && areaIconOf[key] && (
                    <MdiIcon icon={areaIconOf[key]} size={20} className="section-icon" />
                  )}
                  {groupLabelOf(key)}
                </h2>
              )}
              {open &&
                (mode === 'floor' ? (
                  subgroupByArea(groups[key]).map(([area, list]) => {
                    const akey = `${key}::${area}`;
                    const aopen = openAreas.has(akey);
                    return (
                      <div key={area} className="area-subgroup">
                        <button
                          type="button"
                          className="area-subhead"
                          onClick={() => toggleArea(akey)}
                          aria-expanded={aopen}
                        >
                          <span className={`acc-caret ${aopen ? 'open' : ''}`}>▸</span>
                          {areaIconOf[area] && (
                            <MdiIcon icon={areaIconOf[area]} size={18} className="section-icon" />
                          )}
                          <span>{area}</span>
                          <span className="section-count muted">{list.length}</span>
                        </button>
                        {aopen && (
                          <div className="grid">
                            {list.map((d) => (
                              <DeviceCard
                                key={d.entity_id}
                                device={d}
                                onChange={refresh}
                                onError={flashCmdError}
                                onEdit={isManager && mgrData ? () => openDeviceEdit(d) : undefined}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })
                ) : (
                  <div className="grid">
                    {groups[key].map((d) => (
                      <DeviceCard
                        key={d.entity_id}
                        device={d}
                        onChange={refresh}
                        onError={flashCmdError}
                        onEdit={isManager && mgrData ? () => openDeviceEdit(d) : undefined}
                      />
                    ))}
                  </div>
                ))}
            </section>
          );
        })
      )}
      </>
      )}
      {editDevice && (
        <DeviceEditDialog
          device={editDevice}
          areas={mgrData.areas}
          onClose={() => setEditDevice(null)}
          onSave={saveDeviceEdit}
        />
      )}
      {cmdError && (
        <div className="conn-toast err" role="alert" aria-live="assertive">
          <span>{cmdError}</span>
        </div>
      )}
      {lost && (
        <div className="conn-toast" role="status" aria-live="polite">
          <span className="conn-spinner" aria-hidden="true" />
          <span>Connection lost. Reconnecting…</span>
          <button
            type="button"
            className="conn-retry"
            onClick={() => window.location.reload()}
          >
            Retry now
          </button>
        </div>
      )}
    </div>
  );
}

function UserEditor({ user, entities, schedPerms = [], onSave, onCancel }) {
  const isNew = !user;
  const [username, setUsername] = useState(user?.username || '');
  const [displayName, setDisplayName] = useState(user?.displayName || '');
  const [password, setPassword] = useState('');
  const [picked, setPicked] = useState(new Set(user?.entities || []));
  // Managers get full access via the role itself, so a manager's stored "all"
  // flag is redundant - treat it as off here so un-checking Manager reveals the
  // real (non-manager) "All devices" choice instead of staying stuck on.
  const [all, setAll] = useState(!!user?.all && !user?.manager);
  const [manager, setManager] = useState(!!user?.manager);
  // Optional account expiry, and optional per-entity expiry { entity_id: date }.
  const [expires, setExpires] = useState(user?.expires || '');
  const [entityExpires, setEntityExpires] = useState(() => ({ ...(user?.entityExpires || {}) }));
  const [search, setSearch] = useState('');
  const [groupBy, setGroupBy] = useState(null); // null = auto
  const [expanded, setExpanded] = useState(() => new Set());
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // Per-user climate scheduling permissions
  const [schedPicked, setSchedPicked] = useState(new Set(schedPerms));
  const [allEntitiesForSched, setAllEntitiesForSched] = useState([]);
  // Every entity (any type), for the per-user "add a specific device" search -
  // lets the admin grant one user a disabled-type entity without globalising it.
  const [allEntities, setAllEntities] = useState([]);
  useEffect(() => {
    adminGetAllEntities()
      .then((e) => setAllEntities(e.entities))
      .catch(() => {});
    // Climate entities come from STATE_CACHE via a dedicated endpoint so this
    // works whether HA is reachable or not (e.g. mock/dev mode).
    adminGetClimateEntities()
      .then((e) => setAllEntitiesForSched(e.entities))
      .catch(() => {});
  }, []);

  const toggleGroup = (key) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  function toggleEntity(id) {
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function save() {
    setError('');
    setBusy(true);
    try {
      await onSave({
        username: username.trim(),
        original: user?.username || '',
        displayName: displayName.trim(),
        password,
        all, // manager already grants full access on the backend; don't force it
        manager,
        entities: [...picked],
        expires,
        // Only keep per-entity expiry for entities still assigned to this user.
        entityExpires: Object.fromEntries(
          Object.entries(entityExpires).filter(([id]) => picked.has(id))
        ),
        scheduleEntityIds: [...schedPicked].filter((id) =>
          (all || manager) || allEntitiesForSched.filter((en) => picked.has(en.entity_id)).some((en) => en.entity_id === id)
        ),
      });
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  const q = search.trim().toLowerCase();
  const match = (e) =>
    !q || e.name.toLowerCase().includes(q) || e.entity_id.toLowerCase().includes(q);
  const filtered = entities.filter(match);

  const byId = {};
  for (const e of allEntities) byId[e.entity_id] = e; // names for any specific (incl. disabled types)
  for (const e of entities) byId[e.entity_id] = e;
  const hasAreas = entities.some((e) => e.area);
  const hasFloors = entities.some((e) => e.floor);

  // How to group the picker. Defaults to Floor (or Room) when HA knows areas.
  const mode = groupBy || (hasFloors ? 'floor' : hasAreas ? 'room' : 'type');
  const groupKey = (e) =>
    mode === 'floor'
      ? e.floor || 'No floor'
      : mode === 'room'
      ? e.area || 'No room'
      : domainLabel(e.domain);

  const groups = {};
  for (const e of filtered) {
    const k = groupKey(e);
    if (!groups[k]) groups[k] = [];
    groups[k].push(e);
  }
  const groupNames = Object.keys(groups).sort((a, b) => a.localeCompare(b));
  for (const list of Object.values(groups)) list.sort((a, b) => a.name.localeCompare(b.name));

  const selected = [...picked].map((id) => byId[id] || { entity_id: id, name: id });

  const groupOptions = [
    hasFloors && ['floor', 'Floor'],
    hasAreas && ['room', 'Room'],
    ['type', 'Type'],
  ].filter(Boolean);
  const searching = q.length > 0;

  const setEntityExpiry = (id, val) =>
    setEntityExpires((prev) => {
      const next = { ...prev };
      if (val) next[id] = val;
      else delete next[id];
      return next;
    });

  // The selected devices, rendered as rows so each carries its own controls:
  // remove (✕), the name, and an optional "expires" date right on the device.
  const renderSelected = (word) =>
    selected.length > 0 && (
      <div className="selected-box">
        <div className="selected-head">
          {selected.length} {word} - set an optional date to hide a device after
        </div>
        <div className="sel-rows">
          {selected.map((e) => (
            <div key={e.entity_id} className="sel-row">
              <button
                type="button"
                className="sel-remove"
                title="Remove this device"
                aria-label={`Remove ${e.name}`}
                onClick={() => toggleEntity(e.entity_id)}
              >
                <span aria-hidden="true">✕</span>
              </button>
              <span className="sel-name">{e.name}</span>
              <label className="sel-expiry" title="Optional: hide this device for this user after this date">
                <span>expires</span>
                <input
                  type="date"
                  value={entityExpires[e.entity_id] || ''}
                  onChange={(ev) => setEntityExpiry(e.entity_id, ev.target.value)}
                />
              </label>
            </div>
          ))}
        </div>
      </div>
    );

  // Devices that actually have an expiry set (the "Expiring devices" recap).
  const expiringList = selected.filter((e) => entityExpires[e.entity_id]);

  const checkRow = (e) => (
    <label key={e.entity_id} className={`pick-row ${picked.has(e.entity_id) ? 'picked' : ''}`}>
      <input
        type="checkbox"
        checked={picked.has(e.entity_id)}
        onChange={() => toggleEntity(e.entity_id)}
      />
      <span className="pick-icon" title={domainLabel(e.domain)}><DomainIcon domain={e.domain} /></span>
      <span className="pick-text">
        <span className="pick-name">
          {e.name}
          {e.instance_name && <span className="pick-instance-badge">{e.instance_name}</span>}
        </span>
        <span className="pick-id">{e.entity_id}</span>
      </span>
    </label>
  );

  // In Floor mode, show the rooms (areas) within a floor as collapsible
  // sub-sections (collapsed by default, like the floors).
  const renderBody = (list, floorName) => {
    if (mode !== 'floor') return list.map(checkRow);
    const sub = {};
    for (const e of list) {
      const room = e.area || 'No room';
      if (!sub[room]) sub[room] = [];
      sub[room].push(e);
    }
    return Object.keys(sub)
      .sort((a, b) => a.localeCompare(b))
      .map((room) => {
        const key = `room:${floorName}/${room}`;
        const ropen = searching || expanded.has(key);
        const rsel = sub[room].filter((e) => picked.has(e.entity_id)).length;
        return (
          <div key={room} className="acc-subgroup">
            <button type="button" className="acc-subhead" onClick={() => toggleGroup(key)}>
              <span className={`acc-caret ${ropen ? 'open' : ''}`}>▸</span>
              <span className="acc-subtitle">{room}</span>
              <span className="acc-count muted">
                {rsel ? `${rsel}/` : ''}
                {sub[room].length}
              </span>
            </button>
            {ropen && sub[room].sort((a, b) => a.name.localeCompare(b.name)).map(checkRow)}
          </div>
        );
      });
  };

  return (
    <div className="card editor">
      <h3>{isNew ? 'Add user' : `Edit ${user.username}`}</h3>
      <label>
        Username
        <input
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </label>
      <label>
        Display name
        <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          placeholder={isNew ? 'Set a password' : 'Leave blank to keep current'}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <label>
        Account expires (optional)
        <input type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
      </label>
      <p className="muted field-note">
        After this date the user can't sign in (and any open session is signed out), and they're
        shown a message to contact the administrator. Leave blank for no expiry.
      </p>

      <label className="checkbox-row all-toggle">
        <input type="checkbox" checked={manager} onChange={(e) => setManager(e.target.checked)} />
        <span>
          <strong>Manager</strong> - can organize devices and areas in Home Assistant (and gets
          access to all devices, like the option below)
        </span>
      </label>

      <h4 className="devices-heading">Devices this user can control</h4>

      <label className="checkbox-row all-toggle">
        <input
          type="checkbox"
          checked={all || manager}
          disabled={manager}
          onChange={(e) => setAll(e.target.checked)}
        />
        <span>
          <strong>All devices</strong> - control everything, including devices added later
        </span>
      </label>

      {all || manager ? (
        <>
          <p className="muted all-note">
            {manager ? 'Managers control every device. ' : 'This user can control every device. '}
            You can still add specific extras below (e.g. a device whose type is turned off)
            {manager ? '.' : ', or turn this off to pick devices individually.'}
          </p>
          {renderSelected('added')}
          <div className="add-specific">
            <span className="muted add-specific-label">
              Add a specific device (any type, this user only)
            </span>
            <EntityChips
              entities={allEntities}
              selected={picked}
              onToggle={toggleEntity}
              showChips={false}
              placeholder="Search all devices…"
            />
          </div>
        </>
      ) : (
        <>
          {renderSelected('selected')}

          <div className="add-specific">
            <span className="muted add-specific-label">
              Add a specific device (any type, this user only)
            </span>
            <EntityChips
              entities={allEntities}
              selected={picked}
              onToggle={toggleEntity}
              showChips={false}
              placeholder="Search all devices…"
            />
          </div>

          {entities.length === 0 ? (
            <p className="muted">No entities found in Home Assistant.</p>
          ) : (
            <div className="browse-devices">
              <span className="muted add-specific-label">Or browse the allowed devices</span>
              <SearchBox
                placeholder="Filter the list below…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {groupOptions.length > 1 && (
                <div className="group-by">
                  <span className="muted">Group by</span>
                  {groupOptions.map(([val, label]) => (
                    <button
                      key={val}
                      type="button"
                      className={`seg ${mode === val ? 'on' : ''}`}
                      onClick={() => {
                        setGroupBy(val);
                        setExpanded(new Set());
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {groupNames.length === 0 ? (
                <p className="muted">No matches.</p>
              ) : (
                groupNames.map((name) => {
                  const list = groups[name];
                  const open = searching || expanded.has(name);
                  const sel = list.filter((e) => picked.has(e.entity_id)).length;
                  return (
                    <div key={name} className="acc-group">
                      <button
                        type="button"
                        className="acc-head"
                        onClick={() => toggleGroup(name)}
                      >
                        <span className={`acc-caret ${open ? 'open' : ''}`}>▸</span>
                        <span className="acc-title">{name}</span>
                        <span className="acc-count muted">
                          {sel ? `${sel}/` : ''}
                          {list.length}
                        </span>
                      </button>
                      {open && <div className="acc-body">{renderBody(list, name)}</div>}
                    </div>
                  );
                })
              )}
            </div>
          )}
        </>
      )}

      {expiringList.length > 0 && (
        <div className="entity-expiry">
          <h4 className="devices-heading">Expiring devices</h4>
          <p className="muted field-note">
            These devices disappear from this user after the date shown (they work through that
            whole day). Change the date on the device above, or clear it here to keep the device.
          </p>
          <ul className="expiring-list">
            {expiringList.map((e) => (
              <li key={e.entity_id}>
                <span className="sel-name">{e.name}</span>
                <span className="expiring-date">expires {entityExpires[e.entity_id]}</span>
                <button
                  type="button"
                  className="sel-remove"
                  title="Clear this expiry"
                  aria-label={`Clear expiry for ${e.name}`}
                  onClick={() => setEntityExpiry(e.entity_id, '')}
                >
                  <span aria-hidden="true">✕</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Scheduling permissions */}
      <h4 className="devices-heading">Scheduling permissions</h4>
      <p className="muted field-note">
        Grant access to schedule specific devices. Only devices this user can already control are shown.
      </p>
      {(() => {
        const schedEligible = (all || manager)
          ? allEntitiesForSched
          : allEntitiesForSched.filter((en) => picked.has(en.entity_id));
        if (schedEligible.length === 0) {
          return (
            <p className="muted">
              {allEntitiesForSched.length === 0
                ? 'No schedulable entities found.'
                : 'Assign climate entities to this user first.'}
            </p>
          );
        }
        return (
          <div className="sched-perm-grid" style={{ marginTop: 8 }}>
            {schedEligible.map((en) => {
              const on = schedPicked.has(en.entity_id);
              return (
                <button
                  key={en.entity_id}
                  type="button"
                  className={`sched-perm-cell${on ? ' on' : ''}`}
                  onClick={() => setSchedPicked((prev) => {
                    const next = new Set(prev);
                    next.has(en.entity_id) ? next.delete(en.entity_id) : next.add(en.entity_id);
                    return next;
                  })}
                >
                  <span className="sched-perm-cell-name">{en.name || en.entity_id}</span>
                  <span className="sched-perm-check">{on ? '✓' : ''}</span>
                </button>
              );
            })}
          </div>
        );
      })()}

      {error && <div className="error">{error}</div>}
      <div className="editor-actions">
        <button className="btn-primary" onClick={save} disabled={busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button className="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// Upload / reset the custom app icon (PWA / home-screen / favicon).
function IconSettings() {
  const [v, setV] = useState(0); // cache-buster for the preview
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function onFile(e) {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      await adminUploadIcon(file);
      setV(Date.now());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError('');
    try {
      await adminClearIcon();
      setV(Date.now());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card icon-settings">
      <img className="icon-preview" src={`app-icon?v=${v}`} alt="App icon" />
      <div className="icon-settings-text">
        <span className="device-name">App icon</span>
        <span className="meta">Installed-app / home-screen icon and browser tab.</span>
        {error && <span className="error">{error}</span>}
      </div>
      <div className="row-actions">
        <label className="ghost upload-btn">
          {busy ? 'Working…' : 'Upload'}
          <input type="file" accept="image/*" onChange={onFile} disabled={busy} hidden />
        </label>
        <button className="ghost" onClick={reset} disabled={busy}>
          Reset
        </button>
      </div>
    </div>
  );
}

// Which sign-in methods the user dashboard offers. OAuth credentials live in
// the add-on config; this just toggles Local / OAuth / Both.
// Gmail-recipients-style field: selected entities as removable chips, with a
// type-to-search box that suggests matches to add.
function EntityChips({ entities, selected, onToggle, placeholder = 'Add a device…', showChips = true }) {
  const [q, setQ] = useState('');
  const sel = selected instanceof Set ? selected : new Set(selected);
  const byId = {};
  for (const e of entities) byId[e.entity_id] = e;
  const term = q.trim().toLowerCase();
  const matches = term
    ? entities
        .filter(
          (e) =>
            !sel.has(e.entity_id) &&
            (e.name.toLowerCase().includes(term) || e.entity_id.toLowerCase().includes(term))
        )
        .slice(0, 8)
    : [];
  return (
    <div className="chips-field">
      <div className="chips-box">
        {showChips &&
          [...sel].map((id) => {
            const e = byId[id] || { entity_id: id, name: id };
            return (
              <button type="button" key={id} className="chip" onClick={() => onToggle(id)}>
                {e.name} <span aria-hidden="true">✕</span>
              </button>
            );
          })}
        <input
          className="chips-input"
          value={q}
          placeholder={showChips && sel.size ? '' : placeholder}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      {matches.length > 0 && (
        <div className="chips-menu">
          {matches.map((e) => (
            <button
              type="button"
              key={e.entity_id}
              className="chips-menu-row"
              onClick={() => {
                onToggle(e.entity_id);
                setQ('');
              }}
            >
              <DomainIcon domain={e.domain} />
              <span className="chips-menu-name">{e.name}</span>
              <span className="chips-menu-id muted">{e.entity_id}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Global "always-included" entities - shown in every picker and granted to
// "All devices" users even when their domain's type is turned off.
function IncludedEntitiesSettings() {
  const [all, setAll] = useState(null);
  const [sel, setSel] = useState(new Set());
  const [status, setStatus] = useState('');

  useEffect(() => {
    Promise.all([adminGetAllEntities(), adminGetSettings()])
      .then(([e, s]) => {
        setAll(e.entities);
        setSel(new Set(s.includedEntities || []));
      })
      .catch((err) => setStatus(err.message));
  }, []);

  async function toggle(id) {
    const next = new Set(sel);
    next.has(id) ? next.delete(id) : next.add(id);
    setSel(next);
    setStatus('Saving…');
    try {
      await adminSetSettings({ includedEntities: [...next] });
      setStatus('Saved.');
    } catch (e) {
      setStatus(e.message);
    }
  }

  if (all === null) return null;
  return (
    <div className="card settings-card">
      <span className="device-name">Included entities</span>
      <span className="meta">
        Always show these specific entities in the picker and give them to “All devices” users -
        even if their type is turned off above. Useful for hiding a noisy domain (e.g. switches)
        while keeping the few you care about.
      </span>
      <EntityChips entities={all} selected={sel} onToggle={toggle} placeholder="Add an entity…" />
      {status && <span className="muted">{status}</span>}
    </div>
  );
}

function AuthProviderSettings() {
  const [val, setVal] = useState(null);
  const [cfg, setCfg] = useState({ configured: false, name: 'OAuth' });
  const [rules, setRules] = useState(null);
  const [status, setStatus] = useState('');

  useEffect(() => {
    adminGetSettings()
      .then((d) => {
        setVal(d.authProviders || 'local');
        setCfg({ configured: !!d.oauthConfigured, name: d.oauthName || 'OAuth', openWarning: !!d.oauthOpenWarning });
        setRules(d.passwordRules || { min: 0, max: 0, upper: false, lower: false, number: false, special: false });
      })
      .catch((e) => setStatus(e.message));
  }, []);

  async function choose(v) {
    setVal(v);
    setStatus('Saving…');
    try {
      await adminSetSettings({ authProviders: v });
      setStatus('Saved.');
    } catch (e) {
      setStatus(e.message);
    }
  }

  async function saveRules(next) {
    setRules(next);
    setStatus('Saving…');
    try {
      await adminSetSettings({ passwordRules: next });
      setStatus('Saved.');
    } catch (e) {
      setStatus(e.message);
    }
  }

  if (val === null) return null;
  const opts = [
    ['local', 'Local'],
    ['oauth', cfg.name],
    ['both', 'Both'],
  ];
  return (
    <div className="card settings-card">
      <span className="device-name">Sign-in methods</span>
      <span className="meta">How household members sign in to the user dashboard.</span>
      <div className="seg-group">
        {opts.map(([v, label]) => (
          <button
            key={v}
            className={`seg ${val === v ? 'on' : ''}`}
            disabled={v !== 'local' && !cfg.configured}
            onClick={() => choose(v)}
          >
            {label}
          </button>
        ))}
      </div>
      {!cfg.configured && (
        <span className="meta">
          Add OAuth credentials in the add-on configuration to enable {cfg.name} sign-in.
        </span>
      )}
      {cfg.openWarning && (val === 'oauth' || val === 'both') && (
        <span className="meta" style={{ color: '#e0a23b' }}>
          ⚠ {cfg.name} is enabled but no allowed emails or domains are set, so sign-in is
          refused. Set <code>oauth_allowed_emails</code> / <code>oauth_allowed_domains</code>
          {' '}(or <code>oauth_allow_any</code>) in the add-on configuration.
        </span>
      )}
      {rules && val !== 'oauth' && (
        <div className="pw-rules">
          <span className="meta">Password rules (for users changing their own password):</span>
          <div className="pw-rules-len">
            <label>
              Min length
              <input type="number" min="0" max="128" value={rules.min}
                     onChange={(e) => saveRules({ ...rules, min: Math.max(0, parseInt(e.target.value, 10) || 0) })} />
            </label>
            <label>
              Max length
              <input type="number" min="0" max="256" value={rules.max}
                     onChange={(e) => saveRules({ ...rules, max: Math.max(0, parseInt(e.target.value, 10) || 0) })} />
            </label>
          </div>
          {[['upper', 'an uppercase letter'], ['lower', 'a lowercase letter'], ['number', 'a number'], ['special', 'a special character']].map(([k, label]) => (
            <label key={k} className="checkbox-row">
              <input type="checkbox" checked={!!rules[k]}
                     onChange={(e) => saveRules({ ...rules, [k]: e.target.checked })} />
              Require {label}
            </label>
          ))}
        </div>
      )}
      {status && <span className="muted">{status}</span>}
    </div>
  );
}

// Session-signing secret: show whether it's pinned by config or auto-managed,
// and let an admin rotate the managed one (signs everyone out).
function RemoteInstancesSettings() {
  const [instances, setInstances] = useState(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    try {
      const d = await adminGetRemoteStatus();
      setInstances(d.instances || []);
    } catch (e) {
      setInstances([]);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { check(); }, []);

  if (instances !== null && instances.length === 0) return null;

  return (
    <div className="card settings-card">
      <span className="device-name">Remote instances</span>
      {instances === null ? (
        <span className="muted">Checking…</span>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.25rem' }}>
          {instances.map((inst) => (
            <div key={inst.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
              <span
                style={{
                  display: 'inline-block', width: '0.55rem', height: '0.55rem',
                  borderRadius: '50%', flexShrink: 0,
                  background: inst.reachable ? 'var(--green, #22c55e)' : 'var(--red, #ef4444)',
                }}
              />
              <span style={{ fontWeight: 600 }}>{inst.name}</span>
              <span className="muted" style={{ fontSize: '0.8rem' }}>{inst.url}</span>
              {inst.reachable
                ? <span className="muted" style={{ fontSize: '0.8rem' }}>{inst.cached_entities} entities cached</span>
                : <span style={{ color: 'var(--red, #ef4444)', fontSize: '0.8rem' }}>{inst.error || 'Unreachable'}</span>
              }
            </div>
          ))}
        </div>
      )}
      <div className="tab-actions" style={{ marginTop: '0.5rem' }}>
        <button className="ghost" disabled={busy} onClick={check}>
          {busy ? 'Checking…' : 'Recheck'}
        </button>
      </div>
    </div>
  );
}

function SecretSettings() {
  const [source, setSource] = useState(null);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    adminGetSettings().then((d) => setSource(d.secretSource || 'managed')).catch(() => {});
  }, []);
  async function regenerate() {
    if (!window.confirm('Regenerate the session secret? Everyone will be signed out and must log in again.')) return;
    setBusy(true);
    setStatus('');
    try {
      await request('/admin/regenerate-secret', { method: 'POST' });
      setStatus('Done. All sessions have been invalidated.');
    } catch (e) {
      setStatus(e.message);
    } finally {
      setBusy(false);
    }
  }
  // Only show this card when there's an action to take. When the secret is
  // pinned via config (the add-on's jwt_secret), there's nothing to do here -
  // you'd change it in the add-on configuration - so the card is hidden.
  if (source !== 'managed') return null;
  return (
    <div className="card settings-card">
      <span className="device-name">Session security</span>
      <span className="meta">
        A random session secret is managed automatically for this install. Regenerate it to
        sign everyone out (e.g. if you suspect a session token leaked).
      </span>
      <div className="tab-actions">
        <button className="ghost" disabled={busy} onClick={regenerate}>
          {busy ? 'Working…' : 'Regenerate session secret'}
        </button>
      </div>
      {status && <span className="muted">{status}</span>}
    </div>
  );
}

// Full backup / restore of everything in /data (users, passwords, device
// assignments, settings, activity, app icon) so an uninstall + reinstall keeps
// all data intact.
function BackupSettings() {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [includeActivity, setIncludeActivity] = useState(true);

  async function exportData() {
    setStatus('');
    try {
      const path = 'api/admin/export' + (includeActivity ? '' : '?activity=0');
      const res = await fetch(new URL(path, document.baseURI));
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'control-center-backup.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatus('Exported.');
    } catch (err) {
      setStatus(err.message || 'Export failed');
    }
  }

  async function onFile(e) {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (
      !window.confirm(
        'Restoring will REPLACE all current users, device assignments and settings with the contents of this backup. Continue?',
      )
    )
      return;
    setBusy(true);
    setStatus('Restoring…');
    try {
      const data = JSON.parse(await file.text());
      const r = await request('/admin/import', { method: 'POST', body: JSON.stringify(data) });
      setStatus(`Restored ${r.users} user${r.users === 1 ? '' : 's'}. Reloading…`);
      setTimeout(() => location.reload(), 600);
    } catch (err) {
      setStatus(err.message || 'Import failed');
      setBusy(false);
    }
  }

  return (
    <div className="card settings-card">
      <span className="device-name">Backup &amp; restore</span>
      <span className="meta">
        Export every setting, user and device assignment to a file, then restore it after
        reinstalling to bring everything back.
      </span>
      <div className="warn-box">
        ⚠ The backup contains user accounts and <strong>password hashes</strong>. Treat it as
        sensitive - store it securely and don’t share it.
      </div>
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={includeActivity}
          onChange={(e) => setIncludeActivity(e.target.checked)}
        />
        <span>Include the activity log in the export</span>
      </label>
      <div className="editor-actions">
        <button className="btn-primary" onClick={exportData} disabled={busy}>
          Export
        </button>
        <label className="ghost upload-btn">
          {busy ? 'Working…' : 'Restore from file'}
          <input
            type="file"
            accept="application/json,.json"
            onChange={onFile}
            disabled={busy}
            hidden
          />
        </label>
        {status && <span className="muted">{status}</span>}
      </div>
    </div>
  );
}

// Names + home icon (emoji). Saves to /data and reloads so the change applies
// everywhere.
function NameSettings() {
  const [s, setS] = useState(null);
  const [status, setStatus] = useState('');

  useEffect(() => {
    adminGetSettings()
      .then((d) => setS({ name: d.name, title: d.title, icon: d.icon }))
      .catch((e) => setStatus(e.message));
  }, []);

  async function save() {
    setStatus('Saving…');
    try {
      await adminSetSettings(s);
      location.reload(); // re-fetch session so the new name/icon apply everywhere
    } catch (e) {
      setStatus(e.message);
    }
  }

  if (!s) return null;
  const set = (k) => (e) => setS({ ...s, [k]: e.target.value });
  return (
    <div className="card editor settings-card">
      <label>
        Title (login page &amp; dashboard heading)
        <input type="text" value={s.title} onChange={set('title')} placeholder="Defaults to the app name" />
      </label>
      <label>
        App name (browser tab &amp; installed app)
        <input type="text" value={s.name} onChange={set('name')} placeholder="Control Center" />
      </label>
      <label>
        Header icon - emoji (optional; the logo is shown by default)
        <input type="text" value={s.icon} onChange={set('icon')} placeholder="🎛️" />
      </label>
      <div className="editor-actions">
        <button className="btn-primary" onClick={save}>Save</button>
        {status && <span className="muted">{status}</span>}
      </div>
    </div>
  );
}

// Choose which entity domains appear in the device picker. Shows every type
// present in Home Assistant so removed ones can be added back.
function DeviceTypesSettings({ onChange }) {
  const [available, setAvailable] = useState(null);
  const [enabled, setEnabled] = useState(new Set());
  const [error, setError] = useState('');

  useEffect(() => {
    adminGetDeviceTypes()
      .then((d) => {
        setAvailable(d.available);
        setEnabled(new Set(d.enabled));
      })
      .catch((e) => setError(e.message));
  }, []);

  async function toggle(dom) {
    const next = new Set(enabled);
    next.has(dom) ? next.delete(dom) : next.add(dom);
    setEnabled(next);
    try {
      await adminSetDeviceTypes([...next]);
      onChange && onChange();
    } catch (e) {
      setError(e.message);
    }
  }

  if (!available || available.length === 0) return null;
  return (
    <div className="card device-types">
      <span className="device-name">Device types to show</span>
      <span className="meta">Tap a type to include or exclude it from the picker.</span>
      <div className="chips type-chips">
        {available.map((dom) => (
          <button
            key={dom}
            type="button"
            className={`type-chip ${enabled.has(dom) ? 'on' : ''}`}
            onClick={() => toggle(dom)}
          >
            <DomainIcon domain={dom} /> {domainLabel(dom)}
          </button>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function relativeTime(ts) {
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hr${hrs === 1 ? '' : 's'} ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function dayLabel(ts) {
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

function timeLabel(ts) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
}

// Date <-> <input type="datetime-local"> value ("YYYY-MM-DDTHH:MM", local time).
function toLocalInput(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(
    d.getMinutes(),
  )}`;
}
function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}
function endOfToday() {
  const d = new Date();
  d.setHours(23, 59, 59, 999);
  return d;
}
const DAY_MS = 24 * 60 * 60 * 1000;
// Cap how many rows we actually render - a busy "All" day can hold tens of
// thousands of logbook entries, which would freeze the page if all rendered.
const MAX_RENDER = 500;

// An interactive, zoomable history graph for one entity - pulled live from
// Home Assistant's /api/history (never stored). Numeric entities (sensors,
// temperature) render as a line/area chart; on/off-style entities render as a
// stepped chart over their discrete states. Drag to zoom, double-click to
// reset. Built on the vendored uPlot (global `uPlot`).
function HistoryChart({ entity, label, start, end }) {
  const elRef = useRef(null);
  const plotRef = useRef(null);
  const [state, setState] = useState({ loading: true, error: '', data: null });

  // Fetch the history. The backend returns one or more aligned numeric series
  // (e.g. a climate's current + target temperature), or a single discrete
  // series with `levels` for on/off-style entities.
  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: '', data: null });
    adminHaHistory(entity, start.toISOString(), end.toISOString())
      .then((d) => {
        if (!alive) return;
        const times = d.times || [];
        const series = d.series || [];
        if (!times.length || !series.length) {
          setState({ loading: false, error: '', data: null });
          return;
        }
        setState({
          loading: false,
          error: '',
          data: { numeric: !!d.numeric, unit: d.unit, levels: d.levels || null, times, series },
        });
      })
      .catch((e) => { if (alive) setState({ loading: false, error: e.message, data: null }); });
    return () => { alive = false; };
  }, [entity, start, end]);

  // (Re)build the uPlot instance whenever the data changes.
  useEffect(() => {
    const el = elRef.current;
    const data = state.data;
    if (!el || !data || !window.uPlot) return;
    const { numeric, levels, unit, times, series } = data;
    const css = getComputedStyle(el);
    const axisColor = css.color || '#888';
    const grid = 'rgba(127,127,127,0.18)';
    const root = getComputedStyle(document.documentElement);
    const accent = root.getPropertyValue('--accent').trim() || '#03a9f4';
    const accent2 = root.getPropertyValue('--accent-2').trim() || '#0f9d58';
    const palette = [accent, accent2, '#ff9800', '#ab47bc', '#e91e63'];
    const width = el.clientWidth || 600;

    const single = series.length === 1;
    const uSeries = [
      { value: (u, ts) => (ts == null ? '' : new Date(ts * 1000).toLocaleString()) },
      ...series.map((s, i) => {
        const su = s.unit || unit;
        return {
          label: s.label,
          stroke: palette[i % palette.length],
          width: 2,
          spanGaps: true,
          fill: numeric && single ? 'rgba(3,169,244,0.10)' : undefined,
          points: { show: !numeric },
          paths: numeric ? undefined : uPlot.paths.stepped({ align: 1 }),
          value: (u, v) =>
            v == null ? '' : numeric ? `${v}${su ? ` ${su}` : ''}` : (levels[v] ?? v),
        };
      }),
    ];

    const opts = {
      width,
      height: 260,
      padding: [12, 12, 0, 0],
      scales: { x: { time: true }, y: numeric ? {} : { range: [-0.4, levels.length - 0.6] } },
      legend: { show: true },
      cursor: { drag: { x: true, y: false } },
      series: uSeries,
      axes: [
        { stroke: axisColor, grid: { stroke: grid }, ticks: { stroke: grid } },
        {
          stroke: axisColor,
          grid: { stroke: grid },
          ticks: { stroke: grid },
          size: numeric ? 52 : 84,
          splits: numeric ? undefined : (u) => levels.map((_, i) => i),
          values: numeric
            ? (u, vals) => vals.map((v) => `${v}${unit ? ` ${unit}` : ''}`)
            : (u, vals) => vals.map((v) => levels[v] ?? ''),
        },
      ],
    };
    const u = new uPlot(opts, [times, ...series.map((s) => s.values)], el);
    plotRef.current = u;
    const ro = new ResizeObserver(() => u.setSize({ width: el.clientWidth || width, height: 260 }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      u.destroy();
      plotRef.current = null;
    };
  }, [state.data, entity, label]);

  return (
    <div className="lb-chart">
      <div className="lb-chart-head">
        <span className="lb-chart-title">History · {label || entity}</span>
        <span className="lb-chart-hint">drag to zoom · double-click to reset</span>
      </div>
      {state.loading ? (
        <p className="muted">Loading history…</p>
      ) : state.error ? (
        <div className="error">{state.error}</div>
      ) : !state.data ? (
        <p className="muted">No history for this device in this range.</p>
      ) : (
        <div className="lb-chart-canvas" ref={elRef} />
      )}
    </div>
  );
}

// Styled after Home Assistant's logbook: entries grouped by day, each with a
// round device-type icon, the entity in accent colorr, the action, and a
// time / relative-time / user line. Records this app's own control actions,
// with controls to pick a time range, page by day, and filter by user / item.
function ActivityLog() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  const [who, setWho] = useState(''); // '' = all users
  const [selItems, setSelItems] = useState(() => new Set()); // entity_ids; empty = all
  const [start, setStart] = useState(startOfToday);
  const [end, setEnd] = useState(endOfToday);
  // 'these' = the app's own activity log; 'all' = also pull Home Assistant's
  // live logbook (never stored) for every device.
  const [scope, setScope] = useState('these');
  const [haItems, setHaItems] = useState([]);
  const [haLoading, setHaLoading] = useState(false);

  const load = useCallback(() => {
    adminGetActivity(1000)
      .then((d) => setItems(d.activity))
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // In "All" mode, pull HA's logbook for the visible range. It's fetched live
  // and never persisted; our own changes (which HA records as "by system") are
  // dropped server-side so we don't show them twice.
  useEffect(() => {
    if (scope !== 'all') {
      setHaItems([]);
      return;
    }
    let alive = true;
    setHaLoading(true);
    adminHaLogbook(start.toISOString(), end.toISOString())
      .then((d) => { if (alive) setHaItems(d.entries || []); })
      .catch((e) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setHaLoading(false); });
    return () => { alive = false; };
  }, [scope, start, end]);

  function shiftDays(n) {
    setStart(new Date(start.getTime() + n * DAY_MS));
    setEnd(new Date(end.getTime() + n * DAY_MS));
  }

  function toggleItem(id) {
    setSelItems((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  // The pool feeding the list: the app's own log, plus HA's logbook in "All".
  // In "All" we also drop any HA row that lines up with one of our own app
  // actions (same device, within ~90s) so the named app-log entry stands in
  // for it instead of a bare "by system" duplicate. (The backend does this too;
  // this is a client-side safety net against clock skew / a tight window.)
  const appItems = items || [];
  let pool;
  if (scope === 'all') {
    const appByEnt = new Map();
    for (const a of appItems) {
      if (!a.entity_id) continue;
      if (!appByEnt.has(a.entity_id)) appByEnt.set(a.entity_id, []);
      appByEnt.get(a.entity_id).push(a.ts);
    }
    const ha = haItems.filter((e) => {
      const ts = appByEnt.get(e.entity_id);
      return !(ts && ts.some((t) => Math.abs(t - e.ts) <= 90));
    });
    pool = [...appItems, ...ha];
  } else {
    pool = appItems;
  }

  // Distinct users / items present, for the filters. Users come only from the
  // app's own log (HA entries aren't attributable to an app user); items span
  // everything in the pool so HA-only devices can also be filtered.
  const users = [];
  const itemOpts = [];
  {
    const us = new Set();
    const is = new Set();
    for (const e of pool) {
      const uk = e.username || '';
      if (uk && !us.has(uk)) {
        us.add(uk);
        users.push({ value: uk, label: e.name || e.username || 'Unknown' });
      }
      if (e.entity_id && !is.has(e.entity_id)) {
        is.add(e.entity_id);
        itemOpts.push({ id: e.entity_id, label: e.entity || e.entity_id, domain: e.domain });
      }
    }
    itemOpts.sort((a, b) => a.label.localeCompare(b.label));
  }

  const startMs = start.getTime();
  const endMs = end.getTime();
  const matched = pool
    .filter((e) => {
      const t = e.ts * 1000;
      if (t < startMs || t > endMs) return false;
      if (who && (e.username || '') !== who) return false;
      if (selItems.size && !selItems.has(e.entity_id)) return false;
      return true;
    })
    .sort((a, b) => b.ts - a.ts);
  // Render only the newest MAX_RENDER so a huge logbook can't freeze the page.
  const overflow = Math.max(0, matched.length - MAX_RENDER);
  const shown = overflow ? matched.slice(0, MAX_RENDER) : matched;

  // When exactly one device is in focus, show its interactive history graph.
  const focusEntity = selItems.size === 1 ? [...selItems][0] : null;
  const focusLabel = focusEntity
    ? ((itemOpts.find((o) => o.id === focusEntity) || {}).label || focusEntity)
    : null;

  // Group the (newest-first) entries into calendar days.
  const groups = [];
  let cur = null;
  for (const e of shown) {
    const label = dayLabel(e.ts);
    if (!cur || cur.label !== label) {
      cur = { label, rows: [] };
      groups.push(cur);
    }
    cur.rows.push(e);
  }

  return (
    <div className="card activity">
      <div className="activity-head">
        <span className="device-name">Activity</span>
        <div className="lb-scope" role="tablist" aria-label="Which logs to show">
          <button
            role="tab"
            aria-selected={scope === 'these'}
            className={scope === 'these' ? 'active' : ''}
            onClick={() => setScope('these')}
          >
            These logs
          </button>
          <button
            role="tab"
            aria-selected={scope === 'all'}
            className={scope === 'all' ? 'active' : ''}
            onClick={() => setScope('all')}
          >
            All
          </button>
        </div>
        <button className="ghost" onClick={load}>Refresh</button>
      </div>
      {scope === 'all' && (
        <p className="lb-scope-note muted">
          Showing this app's activity plus Home Assistant's live logbook for
          every device. Pulled from HA on demand - nothing extra is stored.
        </p>
      )}

      <div className="lb-controls">
        <div className="lb-range">
          <input
            type="datetime-local"
            value={toLocalInput(start)}
            max={toLocalInput(end)}
            onChange={(ev) => ev.target.value && setStart(new Date(ev.target.value))}
            aria-label="Start date and time"
          />
          <span className="lb-dash">-</span>
          <input
            type="datetime-local"
            value={toLocalInput(end)}
            min={toLocalInput(start)}
            onChange={(ev) => ev.target.value && setEnd(new Date(ev.target.value))}
            aria-label="End date and time"
          />
        </div>
        <div className="lb-nav">
          <button className="ghost icon-only" onClick={() => shiftDays(-1)} aria-label="Previous day">
            ‹
          </button>
          <button className="ghost icon-only" onClick={() => shiftDays(1)} aria-label="Next day">
            ›
          </button>
        </div>
        {items && users.length > 1 && (
          <select
            className="user-filter"
            value={who}
            onChange={(ev) => setWho(ev.target.value)}
            aria-label="Filter by user"
          >
            <option value="">All users</option>
            {users.map((u) => (
              <option key={u.value} value={u.value}>
                {u.label}
              </option>
            ))}
          </select>
        )}
        {items && itemOpts.length > 0 && (
          <details className="lb-multi">
            <summary>{selItems.size ? `${selItems.size} item${selItems.size === 1 ? '' : 's'}` : 'All items'}</summary>
            <div className="lb-menu">
              {selItems.size > 0 && (
                <button className="lb-menu-clear" onClick={() => setSelItems(new Set())}>
                  Clear selection
                </button>
              )}
              {itemOpts.map((it) => (
                <label key={it.id} className="lb-menu-row">
                  <input
                    type="checkbox"
                    checked={selItems.has(it.id)}
                    onChange={() => toggleItem(it.id)}
                  />
                  <DomainIcon domain={it.domain} />
                  <span>{it.label}</span>
                </label>
              ))}
            </div>
          </details>
        )}
      </div>

      {selItems.size > 0 && (
        <div className="lb-filter-note">
          <span>
            Showing only{' '}
            <strong>
              {[...selItems]
                .map((id) => (itemOpts.find((o) => o.id === id) || {}).label || id)
                .join(', ')}
            </strong>
          </span>
          <button type="button" className="lb-clear" onClick={() => setSelItems(new Set())}>
            Show all
          </button>
        </div>
      )}
      {focusEntity && (
        <HistoryChart
          entity={focusEntity}
          label={focusLabel}
          start={start}
          end={end}
        />
      )}
      {error && <div className="error">{error}</div>}
      {overflow > 0 && (
        <div className="lb-overflow">
          Showing the {MAX_RENDER.toLocaleString()} most recent of{' '}
          {matched.length.toLocaleString()} entries. Narrow the date range or
          filter to a device to see the rest.
        </div>
      )}
      {items === null || (scope === 'all' && haLoading && shown.length === 0) ? (
        <p className="muted">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="muted">No activity in this range.</p>
      ) : (
        groups.map((g) => (
          <div key={g.label} className="lb-group">
            <div className="lb-date">{g.label}</div>
            <ul className="lb-list">
              {g.rows.map((e, i) => (
                <li key={i} className="lb-row">
                  <span className="lb-icon" title={domainLabel(e.domain)}>
                    <DomainIcon domain={e.domain} />
                  </span>
                  <div className="lb-body">
                    <div className="lb-line">
                      <button
                        type="button"
                        className="lb-entity"
                        title={`Show only ${e.entity}`}
                        onClick={() => e.entity_id && setSelItems(new Set([e.entity_id]))}
                      >
                        {e.entity}
                      </button>{' '}
                      {e.verb}
                      {e.source === 'schedule' && (
                        <span className="lb-sched-badge" title={e.schedule ? `Schedule: ${e.schedule}` : 'Ran from a schedule'}>Schedule</span>
                      )}
                    </div>
                    <div className="lb-meta">
                      {timeLabel(e.ts)} · {relativeTime(e.ts)}
                      {e.source === 'schedule'
                        ? (e.name ? ` · ${e.name}'s schedule${e.schedule ? ` “${e.schedule}”` : ''}` : '')
                        : (e.name ? ` · ${e.name}` : '')}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  );
}

function Admin({ onBack, standalone, title = 'Control Center' }) {
  const [users, setUsers] = useState(null);
  const [entities, setEntities] = useState([]);
  const [editing, setEditing] = useState(null); // {user} or {} for new
  const [tab, setTab] = useState('users'); // 'users' | 'activity' | 'settings' | 'schedules'
  const [error, setError] = useState('');
  const [schedPermsAll, setSchedPermsAll] = useState({});

  const reload = useCallback(async () => {
    setError('');
    // Load users and entities independently: a failed entity fetch (e.g. HA
    // briefly unreachable) shouldn't block managing users.
    try {
      const u = await adminGetUsers();
      setUsers(u.users);
    } catch (err) {
      setError(err.message);
    }
    try {
      const e = await adminGetEntities();
      setEntities(e.entities);
    } catch (err) {
      setError((prev) => prev || `Couldn't load device list: ${err.message}`);
    }
    try {
      const p = await adminGetSchedulePerms();
      setSchedPermsAll(p);
    } catch {
      // non-fatal: schedule perms simply won't be pre-populated
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  // Opening an editor refreshes the entity list, so entities just added to the
  // global "Included entities" list show up in the picker without a page reload.
  useEffect(() => {
    if (editing === null) return;
    adminGetEntities()
      .then((e) => setEntities(e.entities))
      .catch(() => {});
  }, [editing]);

  async function handleSave(data) {
    const { scheduleEntityIds, ...userFields } = data;
    await adminSaveUser(userFields);
    if (scheduleEntityIds !== undefined) {
      await adminSetSchedulePerms(userFields.username || userFields.original, scheduleEntityIds);
    }
    setEditing(null);
    reload();
  }

  async function handleDelete(username) {
    if (!window.confirm(`Delete user "${username}"?`)) return;
    try {
      await adminDeleteUser(username);
      reload();
    } catch (err) {
      setError(err.message);
    }
  }

  if (editing !== null) {
    return (
      <div className="admin">
        <UserEditor
          user={editing.user}
          entities={entities}
          schedPerms={schedPermsAll[editing.user?.username] || []}
          onSave={handleSave}
          onCancel={() => setEditing(null)}
        />
      </div>
    );
  }

  return (
    <div className={`admin${tab === 'activity' ? ' admin-wide' : ''}`}>
      <header className="topbar">
        <h1>{title}</h1>
        <div className="topbar-actions">
          <ThemeToggle />
          {!standalone && (
            <button className="ghost" onClick={onBack}>
              Back
            </button>
          )}
        </div>
      </header>

      <div className="tabs">
        <button className={`seg ${tab === 'users' ? 'on' : ''}`} onClick={() => setTab('users')}>
          Users
        </button>
        <button className={`seg ${tab === 'activity' ? 'on' : ''}`} onClick={() => setTab('activity')}>
          Activity
        </button>
        <button className={`seg ${tab === 'schedules' ? 'on' : ''}`} onClick={() => setTab('schedules')}>
          Schedules
        </button>
        <button className={`seg ${tab === 'settings' ? 'on' : ''}`} onClick={() => setTab('settings')}>
          Settings
        </button>
      </div>

      {error && <div className="error banner">{error}</div>}

      {tab === 'settings' ? (
        <>
          <NameSettings />
          <IconSettings />
          <DeviceTypesSettings onChange={reload} />
          <IncludedEntitiesSettings />
          <AuthProviderSettings />
          <RemoteInstancesSettings />
          <SecretSettings />
          <BackupSettings />
        </>
      ) : tab === 'activity' ? (
        <ActivityLog />
      ) : tab === 'schedules' ? (
        <AdminSchedulesView />
      ) : (
        <>
          <div className="tab-actions">
            <button className="btn-primary" onClick={() => setEditing({ user: null })}>
              Add user
            </button>
          </div>
          {users === null ? (
            <p className="muted">Loading…</p>
          ) : (
            users.map((u) => (
              <div key={u.username} className="card user-row">
                <div>
                  <span className="device-name">
                    {u.displayName || u.username}
                    {u.manager && <span className="badge badge-manager">Manager</span>}
                    <ExpiryBadge expires={u.expires} />
                  </span>
                  <div className="meta">
                    {u.username} ·{' '}
                    {u.all
                      ? 'All devices'
                      : `${u.entities.length} device${u.entities.length === 1 ? '' : 's'}`}
                  </div>
                </div>
                <div className="row-actions">
                  <button className="ghost" onClick={() => setEditing({ user: u })}>
                    Edit
                  </button>
                  <button className="btn-danger" onClick={() => handleDelete(u.username)}>
                    Delete
                  </button>
                </div>
              </div>
            ))
          )}
        </>
      )}
    </div>
  );
}

// The household-facing experience (served on the published user port): app
// login -> personal device dashboard. No access to user management.
// First-visit "Install" banner. Registers the service worker, captures the
// browser's install prompt (Android/desktop Chrome), and shows tailored iOS
// instructions (Safari has no install event). Dismissal is remembered.
const PWA_DISMISS_KEY = 'ha_pwa_dismissed';

// --- Theme: System (default) / Light / Dark ---
// v2 key so stale values from the old light/dark-only toggle are ignored and
// everyone gets the System default.
const THEME_KEY = 'ha_theme_v2';
const systemPrefersLight = () =>
  !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches);

function themePref() {
  const v = localStorage.getItem(THEME_KEY);
  return v === 'light' || v === 'dark' || v === 'system' ? v : 'system';
}
function resolveTheme(pref) {
  if (pref === 'system') return systemPrefersLight() ? 'light' : 'dark';
  return pref;
}
function applyTheme(pref) {
  document.documentElement.setAttribute('data-theme', resolveTheme(pref));
}
if (typeof document !== 'undefined') {
  applyTheme(themePref());
}

function ThemeToggle() {
  const [pref, setPref] = useState(themePref());
  const [, setTick] = useState(0); // re-render the icon when the OS theme flips
  useEffect(() => {
    localStorage.setItem(THEME_KEY, pref);
    applyTheme(pref);
    // When following the system, re-apply (and re-render the icon) live as the
    // OS theme changes.
    if (pref === 'system' && window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: light)');
      const onChange = () => {
        applyTheme('system');
        setTick((n) => n + 1);
      };
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
  }, [pref]);

  // Two states only: Auto (follow the device) or its inverse. From Auto, flip to
  // the OPPOSITE of the device's theme; from the manual override, back to Auto.
  const next = pref === 'system' ? (systemPrefersLight() ? 'dark' : 'light') : 'system';
  const shown = resolveTheme(pref); // 'light' or 'dark' - what's actually on screen
  // Auto shows the half/half glyph; the manual override shows sun/moon for what's on.
  const icon = pref === 'system' ? '🌗' : shown === 'light' ? '☀️' : '🌙';
  const title =
    pref === 'system' ? `Theme: Auto (${shown})` : `Theme: ${shown === 'light' ? 'Light' : 'Dark'}`;
  return (
    <button
      className="ghost icon-only"
      onClick={() => setPref(next)}
      title={title}
      aria-label={title}
    >
      {icon}
    </button>
  );
}

// Capture the browser's install prompt once, app-wide, so both the first-visit
// banner and the always-available header "Install" button can use it.
let deferredInstall = null;
if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstall = e;
    window.dispatchEvent(new Event('pwa-installable'));
  });
  window.addEventListener('appinstalled', () => {
    deferredInstall = null;
    window.dispatchEvent(new Event('pwa-installed'));
  });
}

// A persistent header button - visible whenever the app is installable, even
// after the first-visit banner is dismissed.
function InstallButton() {
  const [ready, setReady] = useState(!!deferredInstall);
  useEffect(() => {
    const on = () => setReady(!!deferredInstall);
    window.addEventListener('pwa-installable', on);
    window.addEventListener('pwa-installed', on);
    return () => {
      window.removeEventListener('pwa-installable', on);
      window.removeEventListener('pwa-installed', on);
    };
  }, []);
  if (!ready) return null;
  async function install() {
    if (!deferredInstall) return;
    deferredInstall.prompt();
    await deferredInstall.userChoice;
    deferredInstall = null;
    setReady(false);
  }
  return (
    <button className="btn-success" onClick={install}>
      Install
    </button>
  );
}

// Floating install banner. On the lockscreen (`persistent`) it stays put until
// the app is installed; on the dashboard it's dismissible.
function InstallPrompt({ persistent = false, appName = 'Control Center', appIcon = '', appImage = null }) {
  const standalone =
    window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;
  const [deferred, setDeferred] = useState(deferredInstall);
  const [ios, setIos] = useState(false);
  const [dismissed, setDismissed] = useState(localStorage.getItem(PWA_DISMISS_KEY) === '1');

  useEffect(() => {
    if (standalone) return;
    if ('serviceWorker' in navigator && window.isSecureContext) {
      navigator.serviceWorker.register(new URL('sw.js', document.baseURI)).catch(() => {});
    }
    const onInstallable = () => setDeferred(deferredInstall);
    window.addEventListener('pwa-installable', onInstallable);
    window.addEventListener('pwa-installed', () => setDeferred(null));
    const ua = navigator.userAgent;
    if (/iphone|ipad|ipod/i.test(ua) && /safari/i.test(ua) && !/crios|fxios|android/i.test(ua)) {
      setIos(true);
    }
    return () => window.removeEventListener('pwa-installable', onInstallable);
  }, []);

  async function install() {
    if (!deferredInstall) return;
    deferredInstall.prompt();
    await deferredInstall.userChoice;
    deferredInstall = null;
    setDeferred(null);
  }

  function dismiss() {
    setDismissed(true);
    localStorage.setItem(PWA_DISMISS_KEY, '1');
  }

  if (standalone) return null;
  const installable = deferred || ios;
  if (!installable || (!persistent && dismissed)) return null;

  return (
    <div className="install-banner">
      <BrandIcon icon={appIcon} image={appImage} className="install-icon" />
      <div className="install-text">
        <strong>Install {appName}</strong>
        <span className="muted">
          {ios
            ? 'Tap the Share button, then “Add to Home Screen”.'
            : 'Add it to your home screen for a full-screen, native feel.'}
        </span>
      </div>
      {!ios && deferred && (
        <button className="btn-success" onClick={install}>
          Install
        </button>
      )}
      {!persistent && (
        <button className="install-close" onClick={dismiss} aria-label="Dismiss">
          ✕
        </button>
      )}
    </div>
  );
}

function UserApp({ live, title, appName, appIcon, appImage, providers, oauth, authNotice }) {
  const [token, setTok] = useState(getToken());
  const [displayName, setDisplayName] = useState(localStorage.getItem(NAME_KEY) || '');
  const [isManager, setIsManager] = useState(false);
  const [picture, setPicture] = useState('');
  const [canChangePassword, setCanChangePassword] = useState(false);
  const [passwordRules, setPasswordRules] = useState(null);
  // Seed the login prompts from an OAuth sign-in failure handed back by the
  // server (expired account, or a generic message), so it shows on the login
  // page exactly like a password sign-in failure.
  const [expiredNotice, setExpiredNotice] = useState(authNotice?.expired ? 'expired' : '');
  const [authError, setAuthError] = useState(authNotice?.message || '');

  // Auth expiry from any API call: silently drop back to login.
  useEffect(() => {
    function onAuthLogout() {
      localStorage.removeItem(NAME_KEY);
      setTok(null);
      setDisplayName('');
      setIsManager(false);
      setPicture('');
      setCanChangePassword(false);
      setPasswordRules(null);
      // A silent auth:logout is a session timeout, NOT an account expiry - the
      // user just needs to sign in again, so use the 'session' notice (not the
      // 'contact your administrator' account-expired one).
      setExpiredNotice('session');
    }
    window.addEventListener('auth:logout', onAuthLogout);
    return () => window.removeEventListener('auth:logout', onAuthLogout);
  }, []);

  // Resolve the signed-in user's role (so managers get the area organizer).
  useEffect(() => {
    if (!token) {
      setIsManager(false);
      return;
    }
    getMe()
      .then((m) => {
        setIsManager(!!m.manager);
        if (m.displayName) setDisplayName(m.displayName);
        setPicture(m.picture || '');
        setCanChangePassword(!!m.canChangePassword);
        setPasswordRules(m.passwordRules || null);
      })
      .catch((e) => {
        if (e && e.expired) {
          setTok(null);
          setExpiredNotice(e.message);
        }
      });
  }, [token]);

  function handleLogin(tok, name) {
    setToken(tok);
    localStorage.setItem(NAME_KEY, name || '');
    setTok(tok);
    setDisplayName(name || '');
    setExpiredNotice('');
    setAuthError('');
  }

  function handleLogout() {
    setToken(null);
    localStorage.removeItem(NAME_KEY);
    setTok(null);
    setDisplayName('');
    setIsManager(false);
    setPicture('');
    setCanChangePassword(false);
    setPasswordRules(null);
  }

  return (
    <>
      {!token ? (
        <Login
          onLogin={handleLogin}
          title={title}
          appIcon={appIcon}
          appImage={appImage}
          providers={providers}
          oauth={oauth}
          notice={expiredNotice}
          authError={authError}
        />
      ) : (
        <Dashboard
          displayName={displayName}
          onLogout={handleLogout}
          live={live}
          title={title}
          appIcon={appIcon}
          appImage={appImage}
          isManager={isManager}
          picture={picture}
          canChangePassword={canChangePassword}
          passwordRules={passwordRules}
        />
      )}
      <InstallPrompt persistent={!token} appName={appName} appIcon={appIcon} appImage={appImage} />
    </>
  );
}

function setLinkHref(rel, href) {
  let link = document.querySelector(`link[rel='${rel}']`);
  if (!link) {
    link = document.createElement('link');
    link.rel = rel;
    document.head.appendChild(link);
  }
  link.href = href;
}

function setMetaContent(name, content) {
  let m = document.querySelector(`meta[name='${name}']`);
  if (!m) {
    m = document.createElement('meta');
    m.name = name;
    document.head.appendChild(m);
  }
  m.content = content;
}

// iOS can't use an emoji or SVG as a home-screen icon, so paint the emoji onto
// an opaque PNG canvas it can use for apple-touch-icon.
function emojiToPng(emoji, size = 180) {
  try {
    const c = document.createElement('canvas');
    c.width = c.height = size;
    const ctx = c.getContext('2d');
    ctx.fillStyle = getComputedStyle(document.body).backgroundColor || '#0f1419';
    ctx.fillRect(0, 0, size, size);
    ctx.font = `${Math.floor(size * 0.66)}px serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(emoji, size / 2, size / 2 + size * 0.04);
    return c.toDataURL('image/png');
  } catch (e) {
    return null;
  }
}

// Top-level: the backend tells us which port we're on (Ingress/sidebar =
// management, published = user dashboard) and whether live push is enabled.
// The running build, read from the <meta name="app-version"> the server stamps
// from config.yaml - shown as a small label so you can tell which version is live.
const APP_VERSION =
  (typeof document !== 'undefined' &&
    document.querySelector('meta[name="app-version"]')?.getAttribute('content')) ||
  '';

function VersionTag() {
  if (!APP_VERSION || APP_VERSION === '__APP_VERSION__') return null;
  return (
    <div className="version-tag" aria-hidden="true">
      v{APP_VERSION}
    </div>
  );
}

function App() {
  const [session, setSession] = useState(null); // null = loading
  // An OAuth sign-in failure is handed back in the URL fragment so it renders
  // on the login page (themed), the same as a password sign-in failure. Read it
  // once at startup (before the fragment is stripped below).
  const [authNotice] = useState(() => {
    if (typeof location === 'undefined' || !location.hash) return null;
    const p = new URLSearchParams(location.hash.slice(1));
    const err = p.get('auth_error');
    if (!err) return null;
    return err === 'expired'
      ? { expired: true }
      : { message: p.get('auth_msg') || 'Sign-in failed. Please try again.' };
  });
  // App name -> browser tab + installed-app name. Title -> visible heading.
  const appName = session?.appName || 'Control Center';
  const title = session?.title || appName;
  const appIcon = session?.appIcon || '';
  const appImage = session?.appImage || null;
  const providers = session?.providers || { local: true, oauth: false };
  const oauth = {
    name: session?.oauthName || 'OAuth',
    isGoogle: !!session?.oauthIsGoogle,
    logo: session?.oauthLogo || '',
  };

  // OAuth callback hands the session token (or a sign-in error) back in the URL
  // fragment. Capture it, then strip the fragment before anything reads it.
  useEffect(() => {
    if (location.hash.includes('oauth_token=')) {
      const params = new URLSearchParams(location.hash.slice(1));
      const t = params.get('oauth_token');
      if (t) {
        setToken(t);
        const name = params.get('oauth_name');
        if (name) localStorage.setItem(NAME_KEY, name); // greet OAuth users by name
      }
      history.replaceState(null, '', location.pathname + location.search);
    } else if (location.hash.includes('auth_error=')) {
      history.replaceState(null, '', location.pathname + location.search);
    }
  }, []);

  useEffect(() => {
    getSession()
      .then(setSession)
      .catch(() => setSession({ mode: 'user', stream: false }));
  }, []);

  // Auto-reload when a newer build is deployed (the add-on restarts on update,
  // so the running page would otherwise keep old code until a manual refresh).
  // We compare the server's version to the one this page was built with; on a
  // mismatch we reload once (network-first SW then serves the fresh code). No
  // loop: after reload APP_VERSION matches again.
  useEffect(() => {
    if (!APP_VERSION || APP_VERSION === '__APP_VERSION__') return;
    let stopped = false;
    const check = async () => {
      try {
        const { version } = await getServerVersion();
        if (!stopped && version && version !== APP_VERSION) location.reload();
      } catch {
        /* offline / transient - ignore */
      }
    };
    const id = setInterval(check, 60000);
    const onVisible = () => document.visibilityState === 'visible' && check();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  useEffect(() => {
    if (!session) return;
    document.title = appName;
    // Favicon: a custom uploaded image when configured, else the bundled logo.
    // index.html ships TWO <link rel="icon"> (an SVG plus a PNG fallback), so
    // update them ALL - touching only the first left the PNG on the default,
    // which browsers often prefer, so the uploaded logo never showed. Drop the
    // stale `type` hint so the browser sniffs the uploaded image's real type.
    const iconHref = appImage ? new URL(appImage, document.baseURI).href : './icons/icon.svg';
    const iconLinks = document.querySelectorAll("link[rel~='icon']");
    if (iconLinks.length) {
      iconLinks.forEach((l) => {
        l.removeAttribute('type');
        l.href = iconHref;
      });
    } else {
      setLinkHref('icon', iconHref);
    }

    // iOS ignores the web manifest for "Add to Home Screen" - it reads these
    // tags from the live DOM instead. Keep them in sync with the configured
    // name/icon so the installed iOS app isn't stuck on the defaults.
    setMetaContent('apple-mobile-web-app-title', appName);
    const touch = appImage
      ? new URL(appImage, document.baseURI).href
      : './icons/apple-touch-icon.png';
    setLinkHref('apple-touch-icon', touch);
  }, [session, appName, appImage]);

  let content;
  if (session === null) {
    content = (
      <div className="centered">
        <p className="muted">Loading…</p>
      </div>
    );
  } else if (session.mode === 'manage') {
    content = <Admin standalone title={title} />;
  } else {
    content = (
      <UserApp
        live={session.stream}
        title={title}
        appName={appName}
        appIcon={appIcon}
        appImage={appImage}
        providers={providers}
        oauth={oauth}
        authNotice={authNotice}
      />
    );
  }
  return (
    <>
      {content}
      <VersionTag />
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
