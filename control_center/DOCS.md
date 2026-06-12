# Control Center

A per-user device dashboard. Each person sees and controls **only the devices
assigned to them** - lights, switches, fans, climate, covers, locks, media
players and more.

There are two interfaces:

- **Management** - the **Control Center** tab in the HA sidebar (admin-only, via
  Ingress). Add/edit users and assign their devices. No separate password; HA's
  own login protects it, like the Terminal add-on.
- **User dashboard** - published at `http://<home-assistant>:8099`. Household
  members open it in any browser and log in with the account you created.

## Installation

1. Install the add-on and click **Start**.
2. Open the **Control Center** tab in the sidebar - it goes straight to the management
   screen (no login).
3. Add your household members under **Manage users** and assign each their
   devices.
4. Send each member to `http://<your-home-assistant>:8099` to log in and
   control their devices. Enable port `8099` in the **Network** section if it
   isn't already.

## Configuration

```yaml
jwt_secret: a-long-random-string   # signs login sessions
```

| Option | Description |
|--------|-------------|
| `jwt_secret` | Secret used to sign login sessions. Set a long random string. |
| `oauth_*` | Optional OAuth / OpenID Connect sign-in (see below). |

Everything else is configured live in the sidebar's **Settings** tab (stored
in `/data`):

- **Title** - the heading people see on the **login page** and the **dashboard**.
- **App name** - the **browser-tab title** and the name of the **installed
  home-screen (PWA) app**. The Title falls back to this when left blank.
- **Header icon** - an *optional* emoji shown beside the title. Leave it blank
  to show the **Control Center logo** (the default).
- **App icon** - an **uploaded image** that becomes the browser favicon and the
  installed PWA / home-screen icon, overriding the default logo. Without an
  upload, the built-in logo is used everywhere.
- **Device types** - which entity domains can be assigned in **Manage users**.
- **Sign-in methods** - Local password, OAuth, or both (see below).

Each screen also has a **theme toggle** in the top corner to switch between
**System** (default), **Light** and **Dark**. The choice is per-device and
remembered in the browser.

## Sign-in with Google / OAuth

The user dashboard can authenticate people with Google (or any OpenID Connect
provider). Credentials go in the **add-on configuration**; you then turn it on
in **Settings → Sign-in methods**.

> The user dashboard must be reachable over **HTTPS at a public URL** (e.g.
> through a reverse proxy / Nginx Proxy Manager / Cloudflare Tunnel). Google
> won't redirect back to a bare `http://<ip>:8099` address.

### 1. Note your dashboard URL

Find the public base URL of the user dashboard, e.g. `https://home.example.com`.
Your **redirect URI** is that plus `/api/oauth/callback`:

```
https://home.example.com/api/oauth/callback
```

The provider **requires** this exact URL to be registered (step 2) - it will
only redirect back to addresses you've whitelisted.

#### Behind Cloudflare Tunnel / a reverse proxy

This works without anything special. Point your tunnel's public hostname at the
add-on's **user-dashboard port `8099`** (e.g. `service: http://<HA-IP>:8099`) -
the `/api/oauth/*` routes are served by that same app, so no extra route is
needed. Set `oauth_redirect_url` to your **public HTTPS hostname**
(`https://home.example.com`); the add-on builds the redirect from that config
value, not from the proxied request, so Google always sees the real HTTPS URL
even though Cloudflare reaches the add-on over plain HTTP internally. There are
no auth cookies in the flow (the CSRF `state` is a signed token in the URL), so
nothing breaks across the tunnel.

> If you also put **Cloudflare Access** (or any login gateway) in front of this
> hostname, either drop it here and rely on this app's OAuth, or add a bypass
> for `/api/oauth/*` - otherwise the gateway's own login page can intercept the
> callback.

### 2. Create Google OAuth credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   create (or pick) a project.
2. **APIs & Services → OAuth consent screen**:
   - User type: **Internal** if you use Google Workspace and only your own
     domain signs in; otherwise **External**.
   - Fill in the app name and your support email.
   - Add the scopes `openid`, `.../auth/userinfo.email`,
     `.../auth/userinfo.profile`.
   - If you chose **External**, either add each person under **Test users**, or
     **Publish** the app so anyone can sign in.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Under **Authorised redirect URIs**, add the redirect URI from step 1
     exactly (scheme, host and `/api/oauth/callback`, no trailing slash).
   - Create, then copy the **Client ID** and **Client secret**.

### 3. Configure the add-on

In the add-on **Configuration** tab:

```yaml
oauth_client_id: "1234…apps.googleusercontent.com"
oauth_client_secret: "GOCSPX-…"
oauth_redirect_url: "https://home.example.com"   # public base URL (no trailing slash needed)
oauth_allowed_domains: ["my.domain"]             # optional; empty = any verified email
```

`oauth_allowed_domains` restricts sign-in to those email domains (so only
`*@my.domain` can get in). Leave it empty (`[]`) to allow any verified email.
To let in a specific guest who is *outside* your domain, add their address to
`oauth_allowed_emails: ["guest@gmail.com"]` - simpler and safer than a one-off
exception, and you revoke it by removing the line. Restart the add-on after
saving. (For a non-Google provider, `oauth_logo_url` sets the button's logo.)

### 4. Turn it on

Open the sidebar **Settings** tab → **Sign-in methods** and choose **Google**
(OAuth only) or **Both** (password + Google). The option stays disabled until
the credentials above are valid.

### 5. First sign-in & onboarding

When someone signs in for the first time, an app user is created automatically
**with no devices**. They see a "reach out to your administrator" screen until
an admin assigns them devices in **Manage users** (onboarding). Only users with
at least one assigned device see the dashboard.

### Other (non-Google) providers

Any OpenID Connect provider works - set the endpoints to match it:

```yaml
oauth_provider_name: "Authentik"
oauth_authorize_url: "https://id.example.com/application/o/authorize/"
oauth_token_url: "https://id.example.com/application/o/token/"
oauth_userinfo_url: "https://id.example.com/application/o/userinfo/"
oauth_scopes: "openid email profile"
```

The provider must return an `email` (and ideally `email_verified`) from its
userinfo endpoint, and you must register the same `/api/oauth/callback`
redirect URI with it.

**Users are not configured here** - manage them in the app (see below). No Home
Assistant token is required either; the add-on talks to HA through the
Supervisor proxy using its auto-injected token.

## Managing users

The sidebar **Control Center** tab opens the management screen directly - the single
source of truth (saved to `/data`). Add, edit, and remove users, and tick each
person's devices from a searchable list of your real HA entities. The system
won't let you remove the last admin.

To give **one** user a device whose **type is turned off** (e.g. a single
`switch.*`), use **Add a specific device** in their editor - it searches every
entity and assigns it to that user only. (The Settings **Included entities**
list is the opposite: it shows entities to *everyone* and grants them to
**All devices** users.)

On first run a default admin **`alice` / `changeme`** is created so you can log
in - change its password (and add everyone else) from the Manage users screen
right away.

### Manager role

Tick **Manager** when editing a user to make them a manager. A manager gets an
**Organize** button on their dashboard with two tabs:

- **Devices** - a searchable list of the Home Assistant devices the app exposes
  (those with at least one entity of an enabled type, or in the Included list),
  grouped by area. Each device has a **pencil** that opens a quick dialog to
  **rename** it and set its **area**.
- **Areas & floors** - a Home Assistant overview-style view that lists each
  **floor** with the areas inside it. The manager can **create a new area**
  (optionally placing it on a floor), **move an area to another floor** (or to
  *No floor*), and **rename** an area. Floors themselves are created/removed in
  Home Assistant - here they're only assigned.

The manager role grants only these organize tools. A manager's **own device
access** is set independently in the editor (turn on **All devices**, or pick a
specific list) - a manager does not have to have access to everything.

Renaming a device or moving it (or an area) is written straight back to **Home
Assistant's registry**, so the change shows up in HA itself and anywhere else.
Renaming a device only sets its display name - entity IDs are left unchanged, so
automations keep working.

Managers also get an **edit pencil on each device card** on their dashboard,
which opens the same rename + area popup without leaving the dashboard. This lets a trusted household member tidy up newly
added devices (e.g. a fresh MQTT device that arrives Unassigned) without giving
them Home Assistant admin access.

## Activity log

The **Activity** tab in the sidebar shows who controlled what, newest first -
each entry names the real app user (e.g. "Alice set the temperature to 21°
Bedroom AC"). This is the app's own record, stored in `/data`.

It exists because app users are **not** Home Assistant users: when the add-on
calls a service it uses the Supervisor's token, so Home Assistant's own logbook
can only credit "Supervisor". The Activity tab is the source of truth that
always shows the actual person. You can clear it at any time.

**These logs / All.** Use the toggle at the top to switch between the app's own
log (**These logs**) and **All** - which also pulls **Home Assistant's own
logbook** live for every device, so you see all device events and not just the
ones triggered through this app. Nothing extra is stored: HA's logbook is
fetched on demand for the visible range. The app's own changes (which HA records
as "by system") are filtered out of the HA side so they don't show up twice -
you still see them as the proper named entry from this app's log.

**History graph.** Tap a device name (or filter to a single device) to open an
**interactive, zoomable history graph** pulled live from Home Assistant. Like
HA's own entity history, it charts the meaningful numbers for that device: a
climate shows its **current and target temperature** as two lines, a light its
brightness, a fan its speed, a media player its volume, a cover its position. A
plain numeric sensor shows its value; on/off-style entities (locks, switches)
fall back to a stepped chart of their state. Drag across it to zoom in;
double-click to reset.

> Note: a climate device is a single entity - its target and current
> temperature are *attributes* of it, not separate entities - so both appear on
> the one device's graph rather than as two devices.

## Backup & restore

The **Settings** tab has a **Backup & restore** card. **Export** downloads a
single JSON file containing everything in `/data` - users, passwords, each
user's device assignments, all settings, the activity log and the uploaded app
icon. Keep it somewhere safe (it contains passwords).

If you uninstall and reinstall the add-on, open Settings → **Restore from
file** and pick that JSON to bring everything back exactly as it was. Restoring
replaces all current users, assignments and settings.

## Live updates

The dashboard updates in **real time** over a WebSocket: any change - from the
app, a physical switch, or an automation - appears within a fraction of a second,
with no refresh. If the connection drops, a **"Connection lost. Reconnecting…"**
notice appears with a **Retry now** button; it clears itself and re-syncs once
the connection is back. When a new version of the app is deployed, open
dashboards **reload themselves** to pick it up - no manual refresh needed. The
build version is shown as a small label in the corner of each screen.

## Installable app (PWA)

The user dashboard is a Progressive Web App. On a phone or desktop it offers an
**Install** prompt; installing adds a **Control Center** icon to the home screen
and runs full-screen, like a native app, with the app shell cached so it loads
instantly (and shows the last-seen state offline).

- **Android / desktop Chrome:** tap **Install** in the prompt (or the browser's
  install button).
- **iOS Safari:** use **Share → Add to Home Screen**.
- Install and the offline cache need a **secure context** (`https://` or
  `localhost`). Over plain `http://<ip>:8099` Android won't offer install, though
  iOS "Add to Home Screen" still gives a full-screen shortcut.
- Taps give subtle **haptic feedback** on phones that support it (iOS and
  Android).

> Changing the **App icon** after people have installed the PWA won't update an
> icon that's already on a home screen - iOS (and some Android launchers) freeze
> it at install time. Remove and re-add the app to pick up a new icon.

## Supported devices

Lights (with brightness), switches, fans (with speed), climate (modes +
temperature), covers (open/close + position), locks, media players (transport +
volume), scenes, scripts, automations, buttons, vacuums. Any other entity
(e.g. sensors) is shown read-only. Every device has a detail view with its full
state and attributes.

## Security notes

- Access is enforced server-side: users can only control entities assigned to
  them, and only through a fixed allow-list of services.
- Passwords are stored in plain text in the user store - keep it private. This
  is intended for a trusted home network.
