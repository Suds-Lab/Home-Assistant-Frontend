# My Home - a simple Home Assistant companion app

A small web app with a basic login that shows each person **only their own**
lights and air conditioning, and lets them control them. It talks to your
Home Assistant instance through a small Python backend that keeps your Home
Assistant token private (the browser never sees it).

This repo is a **Home Assistant add-on repository** - the add-on lives in the
`my_home/` subfolder:

```
repository.yaml      Marks this repo as an HA add-on repository
my_home/             The add-on
  config.yaml          Add-on manifest
  Dockerfile           Builds the add-on image
  app.py               Flask backend (login + talks to Home Assistant)
  requirements.txt     Python dependencies
  users.json           Seed accounts + which entities each may control
  static/              The web UI (React vendored locally - no build step)
```

The frontend is React, but vendored locally and transformed in the browser by
Babel - so there is **no npm and no build step** anywhere.

## Install it (add-on repository)

On Home Assistant OS / Supervised:

1. **Settings → Add-ons → Add-on Store → ⋮ (top-right) → Repositories**.
2. Paste `https://github.com/Suds-Lab/Home-Assistant-Frontend` and **Add**.
3. The **My Home** add-on appears in the store. Open it → **Install**.
4. **Configuration** tab: set `jwt_secret` (a long random string). **Start** the
   add-on. (Users are *not* configured here - see step 5.)
5. Open **My Home** in the sidebar. A default admin **`alice` / `changeme`** is
   created on first run - log in, change its password, and add everyone else
   from the **Manage users** screen. Point household members at
   `http://<your-home-assistant>:8099` for their dashboard.

Updates then arrive as a normal **Update** button when you push new commits.

## 1. One-time setup

### a) Create a Home Assistant token
1. Open Home Assistant in your browser.
2. Click your **profile** (bottom-left), open the **Security** tab.
3. Under **Long-lived access tokens**, click **Create Token**, name it
   (e.g. "My Home App"), and copy the token.

### b) Configure the backend
Copy `.env.example` to `.env` and fill in:
- `HA_URL` - your Home Assistant address, e.g. `http://homeassistant.local:8123`
  or `http://192.168.1.50:8123` (no trailing slash).
- `HA_TOKEN` - the token you just created.
- `JWT_SECRET` - any long random string.

### c) Set up users and their devices

The easiest way is the built-in **Manage users** screen (see below) - but the
first admin account has to be seeded in a file. Edit [`users.json`](users.json):
each user has a username, password, display name, an optional `admin` flag, and
the list of **entity IDs** they're allowed to control:

```json
{
  "username": "alice",
  "password": "changeme",
  "displayName": "Alice",
  "admin": true,
  "entities": ["light.bedroom", "climate.bedroom_ac"]
}
```

`"admin": true` lets that user open the Manage users screen. Entity IDs are
found in HA under **Settings → Devices & Services → Entities** or **Developer
Tools → States**.

### Supported device types

Assign **any** entity to a user - the dashboard groups devices by type and
shows controls tailored to each:

| Type | Controls |
|------|----------|
| `light` | on/off toggle + brightness slider |
| `switch`, `input_boolean` | on/off toggle |
| `fan` | on/off + speed slider |
| `climate` | mode buttons + target-temperature stepper |
| `cover` | open / stop / close + position slider |
| `lock` | lock / unlock |
| `media_player` | prev / play-pause / next + volume |
| `scene`, `script` | activate / run |
| `automation` | trigger + enable toggle |
| `button` | press |
| `vacuum` | start / pause / dock |
| anything else (e.g. `sensor`) | read-only state display |

Every card has an **ⓘ** button that opens a **detail panel** showing the
entity's full state, last-changed time, and every attribute.

Control is enforced server-side: the backend only calls a fixed allow-list of
services (`ALLOWED_SERVICES` in `app.py`), always scoped to the entity's own
domain and only for entities the user owns.

## Two interfaces (two ports)

The app serves two different experiences, decided by which port a request
arrives on:

| | Management | User dashboard |
|---|---|---|
| **Who** | You, the admin/owner | Each household member |
| **Where** | HA sidebar tab (Ingress, port 4000) | `http://<home-assistant>:8099` |
| **Auth** | HA's own login (admin-only, like Terminal) | the app account you created |
| **Does** | add/edit/delete users, assign devices | control only *their* devices |

The management port (4000) is **never published** - it's reachable only through
Home Assistant's Ingress, so a request there is trusted as an authenticated HA
admin (no separate password). The user port (8099) is published for household
members and is protected by the app's per-user login; the admin endpoints are
not available on it at all.

### Managing users (no file editing)

Open the **My Home** tab in the HA sidebar - it goes straight to the management
screen. There you add, edit, and delete users and tick each person's devices
from a **searchable, grouped list of all your real Home Assistant entities** -
no typing entity IDs. Accounts are saved to a persistent store
(`/data/users.json` in the add-on, `users.json` standalone) and survive
restarts. Passwords left blank on edit are kept, and the system won't let you
remove the last admin.

`users.json` is only the **initial seed** (it bootstraps a default admin on
first run). Once the store exists, the **Manage users** screen is the single
source of truth - users are *not* set in the add-on's Configuration tab.

## 2. Run it (standalone / dev)

From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r my_home/requirements.txt
copy my_home\.env.example .env   # then fill in HA_URL + HA_TOKEN
python my_home/app.py
```

This serves both ports from one process:
- **http://localhost:4000** - the management screen (opens directly, no login,
  since this port stands in for HA's Ingress).
- **http://localhost:8099** - the user dashboard; log in with a user from
  `users.json`.

## 3. Install locally without the repository (alternative)

Instead of adding the repository URL (see **Install it** above), you can drop
the add-on onto the machine directly:

1. **Copy the `my_home/` folder** into `/addons/my_home/` on your HA machine.
   Easiest ways to get files there: the **Samba share**, **Studio Code Server**,
   or **Advanced SSH & Web Terminal** add-ons.
2. In Home Assistant go to **Settings → Add-ons → Add-on Store**, open the
   **⋮** menu (top-right) → **Check for updates**. "My Home" appears under
   **Local add-ons**.
3. Click it → **Install** (this builds the image; takes a few minutes).
4. Open the **Configuration** tab and set `jwt_secret`. **Start** the add-on.
5. Thanks to Ingress, **My Home** appears as its own admin tab in the Home
   Assistant sidebar (like the Terminal / Mosquitto add-ons) - click it to open
   the **management** screen right inside HA. No separate password: HA already
   authenticated you as an admin.
6. A default admin (**`alice` / `changeme`**) is seeded on first run. Use
   **Manage users** to change it and add household members + their devices.
7. Tell each member to open **`http://<your-home-assistant>:8099`** and log in
   with the account you created - that's their personal device dashboard. (Make
   sure port 8099 is enabled in the add-on's **Network** section.)

The add-on also gets a proper **Documentation** tab (from `DOCS.md`) and an
icon/logo in the store and sidebar (`icon.png` / `logo.png`).

Notes:
- Requires an install with the Supervisor (HA OS or Supervised). Plain Docker
  (HA Container) or pip (Core) installs don't have add-ons - use the standalone
  run above and a `panel_iframe:` sidebar link instead.
- `homeassistant_api: true` in `config.yaml` grants the proxied API access; the
  app talks to `http://supervisor/core/api` with the injected `SUPERVISOR_TOKEN`.
- HA's own login gates the page (Ingress); the app's per-user login then scopes
  each person to their own entities.

## Installable app (PWA)

The user dashboard is a Progressive Web App. On a first visit it shows an
**Install** banner; installing adds a "My Home" icon to the phone's home screen
and runs full-screen with no browser chrome - it feels like a native app, and
the app shell is cached so it loads instantly (and offline).

- **Android / desktop Chrome:** tap **Install** in the banner (or the browser's
  install button).
- **iOS Safari:** the banner says to use **Share → Add to Home Screen**.
- ⚠️ Installing (and the service worker / offline cache) needs a **secure
  context** - i.e. `https://` or `localhost`. Over plain `http://<ip>:8099` on
  a LAN, Android won't offer install and offline won't work, though iOS
  "Add to Home Screen" still gives a full-screen shortcut. To get the full
  experience, serve it over HTTPS (e.g. a reverse proxy, or Home Assistant's
  own TLS / Nabu Casa in front of it).

## How it works / security notes
- **Real-time updates.** The backend holds one WebSocket to Home Assistant and
  relays state changes to each browser over a **WebSocket** (`/api/ws`), so the
  UI reflects any change (the app, a physical switch, an automation) within a
  fraction of a second - no polling. The browser reconnects and re-syncs over
  REST automatically, so it can't go stale.
  - Behind a reverse proxy, allow the WebSocket upgrade on `/api/ws` (nginx:
    `proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection upgrade;`
    - Cloudflare passes WebSockets through automatically). If you can't proxy
    WebSockets, set `STREAM=0` to fall back to polling.
- The browser only ever talks to this backend. Your Home Assistant token lives
  only in `.env` (standalone) or never exists at all (add-on, via Supervisor).
- Login issues a 7-day session token (JWT). Each request is checked against the
  user's allowed entity list, so users can't control devices that aren't theirs.
- Passwords are stored in plain text for simplicity - keep `users.json` (or the
  add-on config) private. This is intended for a trusted home network. For
  internet exposure, put it behind HTTPS and consider hashed passwords.
