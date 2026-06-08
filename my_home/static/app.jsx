// My Home - React UI loaded from a CDN and transformed in the browser by
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
  if (res.status === 401) {
    const body = await res.json().catch(() => ({}));
    // A 401 with an active token = the session expired. A 401 with no token
    // (i.e. logging in) = bad credentials - surface the real message.
    if (token) {
      setToken(null);
      throw new Error('Your session expired. Please log in again.');
    }
    throw new Error(body.error || 'Wrong username or password');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

const login = (username, password) =>
  request('/login', { method: 'POST', body: JSON.stringify({ username, password }) });
const getDevices = () => request('/devices');
const getMe = () => request('/me');
const getSession = () => request('/session');
// Manager: organize HA devices into areas.
const managerGetDevices = () => request('/manager/devices');
const managerUpdateDevice = (device_id, fields) =>
  request('/manager/device', { method: 'POST', body: JSON.stringify({ device_id, ...fields }) });
const managerGetAreas = () => request('/manager/areas');
const managerSaveArea = (fields) =>
  request('/manager/area', { method: 'POST', body: JSON.stringify(fields) });
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
const adminSetSettings = (s) =>
  request('/admin/settings', { method: 'POST', body: JSON.stringify(s) });
const adminGetActivity = (limit = 200) => request(`/admin/activity?limit=${limit}`);
const adminClearActivity = () => request('/admin/activity', { method: 'DELETE' });
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

function Login({
  onLogin,
  title = 'My Home',
  appIcon = '🏠',
  providers = { local: true, oauth: false },
  oauth = { name: 'OAuth', isGoogle: false, logo: '' },
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const { token, displayName } = await login(username, password);
      onLogin(token, displayName);
    } catch (err) {
      setError(err.message);
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
        <h1><span className="app-icon">{appIcon}</span> {title}</h1>
        <p className="muted">Sign in to control your lights and AC</p>

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
            {error && <div className="error">{error}</div>}
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
function DeviceCard({ device, onChange, onEdit }) {
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
  useEffect(() => {
    if (Date.now() >= freezeUntil.current) setState(device.state);
  }, [device]);
  useEffect(
    () => () => {
      clearTimeout(commitTimer.current);
      clearTimeout(tempTimer.current);
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
        .catch(() => {});
    }, 250);
  }

  const slider = (label, value, setValue, service, toData) => (
    <label className="slider">
      {label}: {value}%
      <input
        type="range"
        min="0"
        max="100"
        value={value}
        disabled={busy}
        onChange={(e) => setValue(Number(e.target.value))}
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
              slider('Speed', pct, setPct, 'set_percentage', (v) => ({ percentage: v }))}
          </>
        );
      case 'climate': {
        const isOff = state === 'off';
        const min = a.min_temp ?? 16;
        const max = a.max_temp ?? 30;
        const step = a.target_temp_step ?? 0.5;
        // Update the shown value instantly on every tap, but only send the
        // final temperature to HA after a short pause - so going 70 -> 64 is
        // one call, not six, and no press has to wait on a round-trip.
        const nudge = (delta) => {
          const next = Math.min(max, Math.max(min, Number((targetRef.current + delta).toFixed(1))));
          targetRef.current = next;
          setTarget(next);
          clearTimeout(tempTimer.current);
          tempTimer.current = setTimeout(() => {
            control(device.entity_id, 'set_temperature', { temperature: next })
              .then(onChange)
              .catch(() => {});
          }, 1500);
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
              <button onClick={() => nudge(-step)} disabled={isOff}>−</button>
              <span className="temp-value">{isOff ? '-' : `${target}°`}</span>
              <button onClick={() => nudge(step)} disabled={isOff}>+</button>
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
            {(a.fan_modes || []).length > 0 && !isOff && (
              <div className="fan-modes">
                <span className="muted">Fan</span>
                <div className="mode-row">
                  {a.fan_modes.map((fm) => (
                    <button
                      key={fm}
                      className={`mode ${fanMode === fm ? 'selected' : ''}`}
                      onClick={() => setFan(fm)}
                      disabled={busy}
                    >
                      {humanize(fm)}
                    </button>
                  ))}
                </div>
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

  // Pick a state-reactive accent: warm light, blue cool, red heat, etc.
  function accent() {
    const d = device.domain;
    if (d === 'light' && on) return 'accent-warm glow';
    if (d === 'climate') {
      if (state === 'heat' || state === 'heat_cool') return 'accent-heat glow pulse';
      if (state === 'cool' || state === 'auto') return 'accent-cool glow';
      if (state === 'dry') return 'accent-dry glow';
      if (state === 'fan_only') return 'accent-on glow';
      return '';
    }
    if (d === 'cover' && state === 'open') return 'accent-sky glow';
    if (d === 'lock') return state === 'locked' ? 'accent-on glow' : 'accent-amber glow';
    if (d === 'media_player' && state === 'playing') return 'accent-media glow pulse';
    if (['switch', 'input_boolean', 'fan', 'automation'].includes(d) && on)
      return 'accent-on glow';
    return isActive ? 'accent-on glow' : '';
  }

  return (
    <div className={`card device ${accent()}`}>
      <div className="device-head">
        <span className="device-name">{device.name}</span>
        {onEdit && device.device_id && (
          <button
            type="button"
            className="ghost icon-only device-edit"
            title="Edit device (name &amp; area)"
            onClick={onEdit}
          >
            ✎
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

// Manager-only: move Home Assistant devices between areas / rename (writes to HA).
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
        Assistant.
      </p>
      <input
        type="search"
        className="search dashboard-search"
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
              <div key={dev.id} className="card org-row">
                <div className="org-info">
                  <span className="device-name">{dev.name}</span>
                  <span className="meta">
                    {dev.area || 'Unassigned'}
                    {dev.entities.length > 0
                      ? ` · ${dev.entities.slice(0, 2).join(', ')}${dev.entities.length > 2 ? '…' : ''}`
                      : ''}
                  </span>
                </div>
                <button
                  className="ghost icon-only org-edit"
                  title="Edit device"
                  onClick={() => setEditing(dev)}
                >
                  ✎
                </button>
              </div>
            ))}
          </section>
        ))
      )}
      {editing && (
        <DeviceEditDialog
          device={editing}
          areas={data.areas}
          onClose={() => setEditing(null)}
          onSave={applyUpdate}
        />
      )}
    </div>
  );
}

// Create a new area or rename an existing one (and pick a floor when creating).
function AreaEditDialog({ area, floors, onClose, onSave }) {
  const isNew = !area.area_id;
  const [name, setName] = useState(area.name || '');
  const [floorId, setFloorId] = useState(area.floor_id || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function save() {
    if (!name.trim()) {
      setErr('An area name is required');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const fields = isNew
        ? { name: name.trim(), floor_id: floorId || null }
        : { area_id: area.area_id, name: name.trim() };
      await onSave(fields);
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
          <h3>{isNew ? 'New area' : 'Rename area'}</h3>
          <button className="ghost icon-only" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <label>
          Name
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        {isNew && (
          <label>
            Floor
            <select value={floorId} onChange={(e) => setFloorId(e.target.value)}>
              <option value="">No floor</option>
              {floors.map((f) => (
                <option key={f.floor_id} value={f.floor_id}>{f.name}</option>
              ))}
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
                    <div className="org-info">
                      <span className="device-name">{a.name}</span>
                    </div>
                    {data.floors.length > 0 && (
                      <select
                        className="user-filter area-floor-select"
                        value={a.floor_id || ''}
                        onChange={(e) => move(a.area_id, e.target.value)}
                        aria-label={`Floor for ${a.name}`}
                      >
                        <option value="">No floor</option>
                        {data.floors.map((f) => (
                          <option key={f.floor_id} value={f.floor_id}>{f.name}</option>
                        ))}
                      </select>
                    )}
                    <button
                      className="ghost icon-only org-edit"
                      title="Rename area"
                      onClick={() => setEditing(a)}
                    >
                      ✎
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
          floors={data.floors}
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
      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'devices'}
          className={`seg${tab === 'devices' ? ' on' : ''}`}
          onClick={() => setTab('devices')}
        >
          Devices
        </button>
        <button
          role="tab"
          aria-selected={tab === 'areas'}
          className={`seg${tab === 'areas' ? ' on' : ''}`}
          onClick={() => setTab('areas')}
        >
          Areas &amp; floors
        </button>
      </div>
      {tab === 'devices' ? <Organizer /> : <AreaOrganizer />}
    </div>
  );
}

function Dashboard({
  displayName,
  onLogout,
  live = true,
  title = 'My Home',
  appIcon = '🏠',
  isManager = false,
}) {
  const [organizing, setOrganizing] = useState(false);
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

  // Fallback fetch (used for first paint and if the live stream drops).
  const refresh = useCallback(async () => {
    try {
      const data = await getDevices();
      setDevices(data.devices || []);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

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
    let ws;
    let retry;
    let stopped = false;
    let opened = false;

    function connect() {
      if (stopped) return;
      const u = new URL('api/ws', document.baseURI);
      u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
      u.searchParams.set('token', getToken() || '');
      ws = new WebSocket(u.href);
      ws.onopen = () => {
        if (opened) refresh(); // reconnect: catch up on anything missed
        opened = true;
      };
      ws.onmessage = (e) => {
        let m;
        try {
          m = JSON.parse(e.data);
        } catch {
          return;
        }
        if (m && m.entity_id) setDevices((prev) => applyUpdate(prev, m));
      };
      ws.onclose = () => {
        if (!stopped) retry = setTimeout(connect, 3000);
      };
      ws.onerror = () => {
        try {
          ws.close();
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
      try {
        ws && ws.close();
      } catch {}
    };
  }, [refresh, live]);

  const q = query.trim().toLowerCase();
  const visible = q
    ? devices.filter(
        (d) => d.name.toLowerCase().includes(q) || d.entity_id.toLowerCase().includes(q)
      )
    : devices;

  const hasRooms = devices.some((d) => d.area);
  const hasFloors = devices.some((d) => d.floor);
  const dense = devices.length >= GROUPING_THRESHOLD; // collapsible + group-by
  const OTHER = 'Other';
  // Resolve the active grouping ('room' is the legacy name for 'area'); fall
  // back to Type if the chosen grouping has no data.
  let mode = groupBy === 'room' ? 'area' : groupBy;
  if (mode === 'area' && !hasRooms) mode = 'type';
  if (mode === 'floor' && !hasFloors) mode = 'type';
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
          <h1><span className="app-icon">{appIcon}</span> {title}</h1>
          {displayName && <span className="muted">Hi, {displayName}</span>}
        </div>
        <div className="topbar-actions">
          {isManager && (
            <button
              className={`ghost ${organizing ? 'on' : ''}`}
              onClick={() => setOrganizing((o) => !o)}
              aria-pressed={organizing}
            >
              {organizing ? 'Done' : 'Organize'}
            </button>
          )}
          {!organizing && devices.length > 4 && (
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
          <button className="btn-danger" onClick={onLogout}>
            Log out
          </button>
        </div>
      </header>

      {organizing ? (
        <Organize />
      ) : (
      <>{/* normal dashboard */}

      {error && <div className="error banner">{error}</div>}
      {!loading && devices.length > 2 && (
        <input
          type="search"
          className="search dashboard-search"
          placeholder="Search devices…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      {!loading && dense && (hasRooms || hasFloors) && (
        <div className="group-by dashboard-groupby">
          <span className="muted">Group by</span>
          <button
            type="button"
            className={`seg ${mode === 'type' ? 'on' : ''}`}
            onClick={() => chooseGroupBy('type')}
          >
            Type
          </button>
          {hasRooms && (
            <button
              type="button"
              className={`seg ${mode === 'area' ? 'on' : ''}`}
              onClick={() => chooseGroupBy('area')}
            >
              Area
            </button>
          )}
          {hasFloors && (
            <button
              type="button"
              className={`seg ${mode === 'floor' ? 'on' : ''}`}
              onClick={() => chooseGroupBy('floor')}
            >
              Floor
            </button>
          )}
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
        <p className="muted">No devices match “{query}”.</p>
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
                  <span className="section-title">{groupLabelOf(key)}</span>
                  <span className="section-count muted">{groups[key].length}</span>
                </button>
              ) : (
                <h2>{groupLabelOf(key)}</h2>
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
    </div>
  );
}

function UserEditor({ user, entities, onSave, onCancel }) {
  const isNew = !user;
  const [username, setUsername] = useState(user?.username || '');
  const [displayName, setDisplayName] = useState(user?.displayName || '');
  const [password, setPassword] = useState('');
  const [picked, setPicked] = useState(new Set(user?.entities || []));
  const [all, setAll] = useState(!!user?.all);
  const [manager, setManager] = useState(!!user?.manager);
  const [search, setSearch] = useState('');
  const [groupBy, setGroupBy] = useState(null); // null = auto
  const [expanded, setExpanded] = useState(() => new Set());
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // Every entity (any type), for the per-user "add a specific device" search -
  // lets the admin grant one user a disabled-type entity without globalising it.
  const [allEntities, setAllEntities] = useState([]);
  useEffect(() => {
    adminGetAllEntities()
      .then((e) => setAllEntities(e.entities))
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
        all,
        manager,
        entities: [...picked],
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

  const checkRow = (e) => (
    <label key={e.entity_id} className={`pick-row ${picked.has(e.entity_id) ? 'picked' : ''}`}>
      <input
        type="checkbox"
        checked={picked.has(e.entity_id)}
        onChange={() => toggleEntity(e.entity_id)}
      />
      <span className="pick-icon" title={domainLabel(e.domain)}><DomainIcon domain={e.domain} /></span>
      <span className="pick-text">
        <span className="pick-name">{e.name}</span>
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

      <label className="checkbox-row all-toggle">
        <input type="checkbox" checked={manager} onChange={(e) => setManager(e.target.checked)} />
        <span>
          <strong>Manager</strong> - can organize devices and areas in Home Assistant (their own
          device access is set separately, below)
        </span>
      </label>

      <h4 className="devices-heading">Devices this user can control</h4>

      <label className="checkbox-row all-toggle">
        <input
          type="checkbox"
          checked={all}
          onChange={(e) => setAll(e.target.checked)}
        />
        <span>
          <strong>All devices</strong> - control everything, including devices added later
        </span>
      </label>

      {all ? (
        <p className="muted all-note">
          This user can control every device. Turn this off to choose specific devices.
        </p>
      ) : (
        <>
          {selected.length > 0 && (
            <div className="selected-box">
              <div className="selected-head">{selected.length} selected - tap to remove</div>
              <div className="chips">
                {selected.map((e) => (
                  <button
                    type="button"
                    key={e.entity_id}
                    className="chip"
                    onClick={() => toggleEntity(e.entity_id)}
                  >
                    {e.name} <span aria-hidden="true">✕</span>
                  </button>
                ))}
              </div>
            </div>
          )}

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
              <input
                type="text"
                className="search"
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
  const [status, setStatus] = useState('');

  useEffect(() => {
    adminGetSettings()
      .then((d) => {
        setVal(d.authProviders || 'local');
        setCfg({ configured: !!d.oauthConfigured, name: d.oauthName || 'OAuth' });
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
      a.download = 'my-home-backup.json';
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
        ⚠ The backup contains user <strong>passwords in plain text</strong>. Anyone with this file
        can sign in as those users - store it securely and don’t share it.
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
        <input type="text" value={s.name} onChange={set('name')} placeholder="My Home" />
      </label>
      <label>
        Home icon (an emoji)
        <input type="text" value={s.icon} onChange={set('icon')} placeholder="🏠" />
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
// round device-type icon, the entity in accent colour, the action, and a
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
                    </div>
                    <div className="lb-meta">
                      {timeLabel(e.ts)} · {relativeTime(e.ts)}
                      {e.name ? ` · ${e.name}` : ''}
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

function Admin({ onBack, standalone, title = 'My Home' }) {
  const [users, setUsers] = useState(null);
  const [entities, setEntities] = useState([]);
  const [editing, setEditing] = useState(null); // {user} or {} for new
  const [tab, setTab] = useState('users'); // 'users' | 'activity' | 'settings'
  const [error, setError] = useState('');

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

  async function handleSave(user) {
    await adminSaveUser(user);
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
          <BackupSettings />
        </>
      ) : tab === 'activity' ? (
        <ActivityLog />
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
  useEffect(() => {
    localStorage.setItem(THEME_KEY, pref);
    applyTheme(pref);
    // When following the system, re-apply live as the OS theme changes.
    if (pref === 'system' && window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: light)');
      const onChange = () => applyTheme('system');
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
  }, [pref]);

  const NEXT = { system: 'light', light: 'dark', dark: 'system' };
  const ICON = { system: '🌗', light: '☀️', dark: '🌙' };
  const TITLE = { system: 'Theme: System', light: 'Theme: Light', dark: 'Theme: Dark' };
  return (
    <button
      className="ghost icon-only"
      onClick={() => setPref(NEXT[pref])}
      title={TITLE[pref]}
      aria-label={TITLE[pref]}
    >
      {ICON[pref]}
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
function InstallPrompt({ persistent = false, appName = 'My Home', appIcon = '🏠' }) {
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
      <div className="install-icon">{appIcon}</div>
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

function UserApp({ live, title, appName, appIcon, providers, oauth }) {
  const [token, setTok] = useState(getToken());
  const [displayName, setDisplayName] = useState(localStorage.getItem(NAME_KEY) || '');
  const [isManager, setIsManager] = useState(false);

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
      })
      .catch(() => {});
  }, [token]);

  function handleLogin(tok, name) {
    setToken(tok);
    localStorage.setItem(NAME_KEY, name || '');
    setTok(tok);
    setDisplayName(name || '');
  }

  function handleLogout() {
    setToken(null);
    localStorage.removeItem(NAME_KEY);
    setTok(null);
    setDisplayName('');
    setIsManager(false);
  }

  return (
    <>
      {!token ? (
        <Login
          onLogin={handleLogin}
          title={title}
          appIcon={appIcon}
          providers={providers}
          oauth={oauth}
        />
      ) : (
        <Dashboard
          displayName={displayName}
          onLogout={handleLogout}
          live={live}
          title={title}
          appIcon={appIcon}
          isManager={isManager}
        />
      )}
      <InstallPrompt persistent={!token} appName={appName} appIcon={appIcon} />
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
function App() {
  const [session, setSession] = useState(null); // null = loading
  // App name -> browser tab + installed-app name. Title -> visible heading.
  const appName = session?.appName || 'My Home';
  const title = session?.title || appName;
  const appIcon = session?.appIcon || '🏠';
  const appImage = session?.appImage || null;
  const providers = session?.providers || { local: true, oauth: false };
  const oauth = {
    name: session?.oauthName || 'OAuth',
    isGoogle: !!session?.oauthIsGoogle,
    logo: session?.oauthLogo || '',
  };

  // OAuth callback hands the session token back in the URL fragment. Capture
  // and store it (then strip it) before anything reads the token.
  useEffect(() => {
    if (location.hash.includes('oauth_token=')) {
      const params = new URLSearchParams(location.hash.slice(1));
      const t = params.get('oauth_token');
      if (t) {
        setToken(t);
        const name = params.get('oauth_name');
        if (name) localStorage.setItem(NAME_KEY, name); // greet OAuth users by name
        history.replaceState(null, '', location.pathname + location.search);
      }
    }
  }, []);

  useEffect(() => {
    getSession()
      .then(setSession)
      .catch(() => setSession({ mode: 'user', stream: false }));
  }, []);

  useEffect(() => {
    if (!session) return;
    document.title = appName;
    // Favicon: a custom image when configured, else the emoji icon.
    let href;
    if (appImage) {
      href = new URL(appImage, document.baseURI).href;
    } else {
      const svg =
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>" +
        "<text x='50%' y='52%' dominant-baseline='central' text-anchor='middle' font-size='52'>" +
        appIcon +
        '</text></svg>';
      href = 'data:image/svg+xml,' + encodeURIComponent(svg);
    }
    setLinkHref('icon', href);

    // iOS ignores the web manifest for "Add to Home Screen" - it reads these
    // tags from the live DOM instead. Keep them in sync with the configured
    // name/icon so the installed iOS app isn't stuck on the defaults.
    setMetaContent('apple-mobile-web-app-title', appName);
    const touch = appImage
      ? new URL(appImage, document.baseURI).href
      : emojiToPng(appIcon) || './icons/apple-touch-icon.png';
    setLinkHref('apple-touch-icon', touch);
  }, [session, appName, appIcon, appImage]);

  if (session === null) {
    return (
      <div className="centered">
        <p className="muted">Loading…</p>
      </div>
    );
  }
  if (session.mode === 'manage') return <Admin standalone title={title} />;
  return (
    <UserApp
      live={session.stream}
      title={title}
      appName={appName}
      appIcon={appIcon}
      providers={providers}
      oauth={oauth}
    />
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
