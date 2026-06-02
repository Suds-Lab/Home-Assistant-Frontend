// My Home - React UI loaded from a CDN and transformed in the browser by
// Babel (no npm, no build step). All components live in this one file.

const { useState, useEffect, useCallback, useRef } = React;

// --- API client ----------------------------------------------------------

const TOKEN_KEY = 'ha_app_token';
const NAME_KEY = 'ha_app_name';

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
    setToken(null);
    throw new Error('Your session expired. Please log in again.');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

const login = (username, password) =>
  request('/login', { method: 'POST', body: JSON.stringify({ username, password }) });
const getDevices = () => request('/devices');
const getSession = () => request('/session');
const getDevice = (entity_id) => request(`/entity/${encodeURIComponent(entity_id)}`);
// Call a whitelisted service on an entity (backend enforces what's allowed).
const control = (entity_id, service, data = {}) =>
  request('/control', { method: 'POST', body: JSON.stringify({ entity_id, service, data }) });

// Admin (only reachable by users flagged admin:true).
const adminGetUsers = () => request('/admin/users');
const adminGetEntities = () => request('/admin/entities');
const adminSaveUser = (user) =>
  request('/admin/users', { method: 'POST', body: JSON.stringify(user) });
const adminDeleteUser = (username) =>
  request(`/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });

// --- Components -----------------------------------------------------------

function Login({ onLogin, appName = 'My Home' }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const { token, displayName, admin } = await login(username, password);
      onLogin(token, displayName, admin);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="centered">
      <form className="card login" onSubmit={submit}>
        <h1>{appName}</h1>
        <p className="muted">Sign in to control your lights and AC</p>
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
function DeviceCard({ device, onChange, onDetails }) {
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
  const [vol, setVol] = useState(a.volume_level != null ? Math.round(a.volume_level * 100) : 50);

  // Optimistic state: reflect the command instantly instead of waiting for the
  // 5s poll. `freezeUntil` ignores incoming poll updates briefly so a stale
  // read from HA (state not yet propagated) can't bounce the control back.
  const [state, setState] = useState(device.state);
  const freezeUntil = useRef(0);
  const commitTimer = useRef(null);
  useEffect(() => {
    if (Date.now() >= freezeUntil.current) setState(device.state);
  }, [device]);
  useEffect(() => () => clearTimeout(commitTimer.current), []);

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
        const nudge = (delta) => {
          const next = Math.min(max, Math.max(min, Number((target + delta).toFixed(1))));
          setTarget(next);
          act('set_temperature', { temperature: next });
        };
        return (
          <>
            {a.current_temperature != null && (
              <div className="climate-readout">
                <span className="muted">Now {a.current_temperature}°</span>
              </div>
            )}
            <div className="temp-control">
              <button onClick={() => nudge(-step)} disabled={busy || isOff}>−</button>
              <span className="temp-value">{isOff ? '-' : `${target}°`}</span>
              <button onClick={() => nudge(step)} disabled={busy || isOff}>+</button>
            </div>
            <div className="mode-row">
              {(a.hvac_modes || []).map((mode) => (
                <button
                  key={mode}
                  className={`mode ${state === mode ? 'selected' : ''} ${mode === 'off' ? 'mode-off' : ''}`}
                  onClick={() => act('set_hvac_mode', { hvac_mode: mode }, mode)}
                  disabled={busy}
                >
                  {mode}
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
                      className={`mode ${a.fan_mode === fm ? 'selected' : ''}`}
                      onClick={() => act('set_fan_mode', { fan_mode: fm })}
                      disabled={busy}
                    >
                      {fm}
                    </button>
                  ))}
                </div>
              </div>
            )}
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
        <button className="info-btn" title="Details" onClick={() => onDetails(device)}>
          i
        </button>
      </div>
      <div className="device-state">{prettyState({ state, attributes: a })}</div>
      <div className="controls">{controls()}</div>
    </div>
  );
}

// Modal showing an entity's full state and every attribute.
function DetailPanel({ device, onClose }) {
  const entries = Object.entries(device.attributes || {});
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{device.name}</h3>
          <button className="info-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <table className="attr-table">
          <tbody>
            <tr>
              <td className="muted">Entity</td>
              <td><code>{device.entity_id}</code></td>
            </tr>
            <tr>
              <td className="muted">State</td>
              <td>{prettyState(device)}</td>
            </tr>
            {device.last_changed && (
              <tr>
                <td className="muted">Last changed</td>
                <td>{new Date(device.last_changed).toLocaleString()}</td>
              </tr>
            )}
          </tbody>
        </table>
        <h4>Attributes</h4>
        {entries.length === 0 ? (
          <p className="muted">No attributes.</p>
        ) : (
          <table className="attr-table">
            <tbody>
              {entries.map(([k, v]) => (
                <tr key={k}>
                  <td className="muted">{k}</td>
                  <td className="attr-val">{fmtValue(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Dashboard({ displayName, onLogout, live = true, appName = 'My Home' }) {
  const [devices, setDevices] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState(null);

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
    next[i] = u.state;
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

    // Live updates via Server-Sent Events. EventSource can't set headers, so
    // the session token rides as a query param; it auto-reconnects on drop.
    const url = `${API_BASE}/stream?token=${encodeURIComponent(getToken() || '')}`;
    const es = new EventSource(url);
    const dropped = { current: false };
    es.onerror = () => {
      dropped.current = true;
    };
    es.onopen = () => {
      // Re-sync the full list after a reconnect (we may have missed updates).
      if (dropped.current) {
        dropped.current = false;
        refresh();
      }
    };
    es.addEventListener('update', (e) => {
      setDevices((prev) => applyUpdate(prev, JSON.parse(e.data)));
    });

    // Safety net so the dashboard can't go stale if the stream is unavailable.
    const pollId = setInterval(refresh, 30000);

    return () => {
      es.close();
      clearInterval(pollId);
    };
  }, [refresh, live]);

  const groups = {};
  for (const d of devices) {
    if (!groups[d.domain]) groups[d.domain] = [];
    groups[d.domain].push(d);
  }
  for (const list of Object.values(groups)) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }
  const domains = Object.keys(groups).sort((x, y) =>
    domainLabel(x).localeCompare(domainLabel(y))
  );

  return (
    <div className="dashboard">
      <header className="topbar">
        <div>
          <h1>{appName}</h1>
          {displayName && <span className="muted">Hi, {displayName}</span>}
        </div>
        <button className="ghost" onClick={onLogout}>
          Log out
        </button>
      </header>

      {error && <div className="error banner">{error}</div>}
      {loading ? (
        <p className="muted">Loading your devices…</p>
      ) : devices.length === 0 ? (
        <p className="muted">No devices assigned to you.</p>
      ) : (
        domains.map((domain) => (
          <section key={domain}>
            <h2>{domainLabel(domain)}</h2>
            <div className="grid">
              {groups[domain].map((d) => (
                <DeviceCard
                  key={d.entity_id}
                  device={d}
                  onChange={refresh}
                  onDetails={setDetail}
                />
              ))}
            </div>
          </section>
        ))
      )}

      {detail && <DetailPanel device={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

function UserEditor({ user, entities, onSave, onCancel }) {
  const isNew = !user;
  const [username, setUsername] = useState(user?.username || '');
  const [displayName, setDisplayName] = useState(user?.displayName || '');
  const [password, setPassword] = useState('');
  const [admin, setAdmin] = useState(user?.admin || false);
  const [picked, setPicked] = useState(new Set(user?.entities || []));
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

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
        displayName: displayName.trim(),
        password,
        admin,
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

  // Group by Floor → Room when Home Assistant knows areas; otherwise by type.
  const groups = {};
  for (const e of filtered) {
    const top = hasAreas ? e.floor || 'Other' : 'All devices';
    const sub = hasAreas ? e.area || 'Unassigned' : domainLabel(e.domain);
    if (!groups[top]) groups[top] = {};
    if (!groups[top][sub]) groups[top][sub] = [];
    groups[top][sub].push(e);
  }
  const tops = Object.keys(groups).sort((a, b) => a.localeCompare(b));
  const selected = [...picked].map((id) => byId[id] || { entity_id: id, name: id });

  const checkRow = (e) => (
    <label key={e.entity_id} className="pick-row">
      <input
        type="checkbox"
        checked={picked.has(e.entity_id)}
        onChange={() => toggleEntity(e.entity_id)}
      />
      <span className="pick-name">{e.name}</span>
      <span className="pick-id">{e.entity_id}</span>
    </label>
  );

  return (
    <div className="card editor">
      <h3>{isNew ? 'Add user' : `Edit ${user.username}`}</h3>
      <label>
        Username
        <input
          type="text"
          value={username}
          disabled={!isNew}
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
      <label className="checkbox-row">
        <input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} />
        Administrator (can manage users)
      </label>

      <h4 className="devices-heading">Devices this user can control</h4>

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

      {entities.length === 0 ? (
        <p className="muted">No entities found in Home Assistant.</p>
      ) : (
        <>
          <input
            type="text"
            className="search"
            placeholder="Search devices…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {tops.length === 0 ? (
            <p className="muted">No matches.</p>
          ) : (
            tops.map((top) => (
              <div key={top} className="floor-group">
                {hasAreas && <div className="floor-head">{top}</div>}
                {Object.keys(groups[top])
                  .sort((a, b) => a.localeCompare(b))
                  .map((sub) => (
                    <div key={sub} className="entity-group">
                      <h4>{sub}</h4>
                      {groups[top][sub]
                        .slice()
                        .sort((a, b) => a.name.localeCompare(b.name))
                        .map(checkRow)}
                    </div>
                  ))}
              </div>
            ))
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

function Admin({ onBack, standalone }) {
  const [users, setUsers] = useState(null);
  const [entities, setEntities] = useState([]);
  const [editing, setEditing] = useState(null); // {user} or {} for new
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
    <div className="admin">
      <header className="topbar">
        <h1>Manage users</h1>
        <div className="topbar-actions">
          <button className="btn-primary" onClick={() => setEditing({ user: null })}>
            Add user
          </button>
          {!standalone && (
            <button className="ghost" onClick={onBack}>
              Back
            </button>
          )}
        </div>
      </header>

      {error && <div className="error banner">{error}</div>}
      {users === null ? (
        <p className="muted">Loading…</p>
      ) : (
        users.map((u) => (
          <div key={u.username} className="card user-row">
            <div>
              <span className="device-name">{u.displayName || u.username}</span>
              {u.admin && <span className="badge">admin</span>}
              <div className="meta">
                {u.username} · {u.entities.length} device
                {u.entities.length === 1 ? '' : 's'}
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
    </div>
  );
}

// The household-facing experience (served on the published user port): app
// login -> personal device dashboard. No access to user management.
// First-visit "Install" banner. Registers the service worker, captures the
// browser's install prompt (Android/desktop Chrome), and shows tailored iOS
// instructions (Safari has no install event). Dismissal is remembered.
const PWA_DISMISS_KEY = 'ha_pwa_dismissed';

function InstallPrompt() {
  const [deferred, setDeferred] = useState(null);
  const [show, setShow] = useState(false);
  const [ios, setIos] = useState(false);

  useEffect(() => {
    const standalone =
      window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;
    if (standalone || localStorage.getItem(PWA_DISMISS_KEY) === '1') return;

    // Service workers need a secure context (HTTPS or localhost).
    if ('serviceWorker' in navigator && window.isSecureContext) {
      navigator.serviceWorker
        .register(new URL('sw.js', document.baseURI))
        .catch(() => {});
    }

    function onPrompt(e) {
      e.preventDefault();
      setDeferred(e);
      setShow(true);
    }
    window.addEventListener('beforeinstallprompt', onPrompt);
    window.addEventListener('appinstalled', () => setShow(false));

    const ua = navigator.userAgent;
    const isIOS = /iphone|ipad|ipod/i.test(ua);
    const isSafari = /safari/i.test(ua) && !/crios|fxios|android/i.test(ua);
    if (isIOS && isSafari) {
      setIos(true);
      setShow(true);
    }

    return () => window.removeEventListener('beforeinstallprompt', onPrompt);
  }, []);

  function dismiss() {
    setShow(false);
    localStorage.setItem(PWA_DISMISS_KEY, '1');
  }

  async function install() {
    if (!deferred) return;
    deferred.prompt();
    await deferred.userChoice;
    setDeferred(null);
    setShow(false);
  }

  if (!show) return null;
  return (
    <div className="install-banner">
      <div className="install-icon">🏠</div>
      <div className="install-text">
        <strong>Install My Home</strong>
        <span className="muted">
          {ios
            ? 'Tap the Share button, then “Add to Home Screen”.'
            : 'Add it to your home screen for a full-screen, native feel.'}
        </span>
      </div>
      {!ios && deferred && (
        <button className="btn-primary" onClick={install}>
          Install
        </button>
      )}
      <button className="install-close" onClick={dismiss} aria-label="Dismiss">
        ✕
      </button>
    </div>
  );
}

function UserApp({ live, appName }) {
  const [token, setTok] = useState(getToken());
  const [displayName, setDisplayName] = useState(localStorage.getItem(NAME_KEY) || '');

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
  }

  return (
    <>
      {!token ? (
        <Login onLogin={handleLogin} appName={appName} />
      ) : (
        <Dashboard displayName={displayName} onLogout={handleLogout} live={live} appName={appName} />
      )}
      <InstallPrompt />
    </>
  );
}

// Top-level: the backend tells us which port we're on (Ingress/sidebar =
// management, published = user dashboard) and whether live push is enabled.
function App() {
  const [session, setSession] = useState(null); // null = loading
  const appName = session?.appName || 'My Home';

  useEffect(() => {
    getSession()
      .then(setSession)
      .catch(() => setSession({ mode: 'user', stream: false }));
  }, []);

  useEffect(() => {
    if (session) document.title = appName;
  }, [session, appName]);

  if (session === null) {
    return (
      <div className="centered">
        <p className="muted">Loading…</p>
      </div>
    );
  }
  if (session.mode === 'manage') return <Admin standalone />;
  return <UserApp live={session.stream} appName={appName} />;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
