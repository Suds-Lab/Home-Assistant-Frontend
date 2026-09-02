# Control Center

A per-user device dashboard. Each person sees and controls **only the devices
assigned to them**: lights, switches, fans, climate, covers, locks, media
players and more. Entities you have **hidden or disabled in Home Assistant**
are left out automatically, just as HA keeps them off its own dashboards.

There are two interfaces:

- **Management**: the **Control Center** tab in the HA sidebar (admin-only, via
  Ingress). Add/edit users and assign their devices. No separate password; HA's
  own login protects it, like the Terminal add-on.
- **User dashboard**: published at `http://<home-assistant>:8099`. Household
  members open it in any browser and log in with the account you created.

## Installation

1. Install the add-on and click **Start**.
2. Open the **Control Center** tab in the sidebar, which goes straight to the
   management screen (no login).
3. Add your household members under **Manage users** and assign each their
   devices.
4. Send each member to `http://<your-home-assistant>:8099` to log in and
   control their devices. Enable port `8099` in the **Network** section if it
   isn't already.

## Configuration

```yaml
jwt_secret: ""
remote_instances: []   # optional - see "Remote instances" below
```

| Option | Description |
|--------|-------------|
| `jwt_secret` | Signs login sessions. **Optional** - leave blank to auto-generate and persist a random secret (recommended); set a value only to pin your own. |
| `remote_instances` | List of additional HA instances whose devices you want to manage here (see below). |
| `oauth_*` | Optional OAuth / OpenID Connect sign-in (see below). |

Everything else is configured live in the sidebar's **Settings** tab (stored
in `/data`):

- **Title**: the heading people see on the **login page** and the **dashboard**.
- **App name**: the **browser-tab title** and the name of the **installed
  home-screen (PWA) app**. The Title falls back to this when left blank.
- **Header icon**: an *optional* emoji shown beside the title. Leave it blank
  to show the **Control Center logo** (the default).
- **App icon**: an **uploaded image** that becomes the browser favicon and the
  installed PWA / home-screen icon, overriding the default logo. Without an
  upload, the built-in logo is used everywhere.
- **Device types**: which entity domains can be assigned in **Manage users**.
- **Sign-in methods**: Local password, OAuth, or both (see below). When local
  sign-in is enabled you can also set **password rules** here (minimum/maximum
  length and whether an uppercase letter, lowercase letter, number, or special
  character is required); they apply when a user changes their own password.
- **Session security**: the login-session secret is auto-managed; you can
  **regenerate** it here to sign everyone out (e.g. if a token leaked).

Each screen also has a **theme toggle** in the top corner to switch between
**System** (default), **Light** and **Dark**. The choice is per-device and
remembered in the browser.

On the user dashboard, an **account menu** (your avatar, top right) holds **Log
out**, the manager **Organize** tools, and **Change password** (for local
accounts). The avatar is your OAuth picture if you have one, otherwise your
initials on a per-user color.

## Remote instances

Control Center can pool devices from **multiple Home Assistant instances** into a single management view. Users are managed in one place; devices from any connected instance can be assigned to them exactly like local ones.

### Setup

**1. On each remote HA - create a long-lived access token**

Profile > Security > Long-lived access tokens > Create token. Copy it.

**2. Add the remote to the add-on configuration**

```yaml
remote_instances:
  - id: garage
    name: Garage
    url: http://192.168.1.50:8123
    token: <long-lived-token>
  - id: cabin
    name: Cabin
    url: http://cabin.local:8123
    token: <long-lived-token>
```

| Field | Description |
|-------|-------------|
| `id` | Short slug used to namespace entities (`garage:light.bedroom`). Any characters are accepted (e.g. `ha-2`), but a simple lowercase slug is easiest to read. Must be unique. Required. |
| `name` | Display name shown as a badge in the entity picker. Required. |
| `url` | Base URL of the remote HA (local IP or hostname). Required. |
| `token` | Long-lived access token from step 1. Required. |

Restart the add-on after saving. Remote entities appear immediately in **Manage users** with a colored instance badge next to their name. Assign them to users the same way as any local entity.

### Managing remote devices

A manager can rename a remote device or move it to another area exactly like a local one, from the pencil **Edit device** button on a device card or in the Organize view. Each edit is routed back to the instance the device belongs to (its id is tagged with the instance), so the change is written to the correct Home Assistant. If a remote is momentarily slow or unreachable, an edit is not falsely blocked; a genuine failure to reach the remote is reported and logged.

### Troubleshooting

If remote entities don't appear after restarting the add-on:

1. **Check the Settings tab.** Open the Control Center sidebar and go to
   Settings. If remote instances are configured, a **Remote instances** card
   appears with a green/red status dot for each one, the cached entity count,
   and any error. Use the **Recheck** button to test connectivity on demand.

2. **Check the add-on log** (Supervisor > Control Center > Log). On startup you
   should see a line like:
   ```
   Remote instances loaded: ['garage -> http://192.168.1.50:8123']
   Launching WebSocket thread for remote instance 'garage' at http://192.168.1.50:8123
   HA WebSocket connected (garage); streaming state changes
   ```
   If the first line says "No remote instances configured", the add-on is not
   reading your `remote_instances` config - re-save the add-on configuration in
   Supervisor and restart. If it says "HA WebSocket error", see the error message
   for the cause (bad token, unreachable host, SSL error).

   When you control a remote device, the log also records the outcome of each
   remote command (`HA WS command OK (instance: garage): light.turn_on ...` or a
   failure reason). So if a change you make in Control Center does not reach the
   remote HA, the log shows exactly why (timeout, auth, or a rejection from HA).
   Any request the server rejects is logged too, as
   `[api-error <status>] <method> <path>: <reason>` (for example a `400` for an
   entity id it does not recognise).

3. **Hit the status endpoint** from a browser (while logged in as admin on the
   management port):
   ```
   http://<home-assistant>:4000/api/admin/remote-status
   ```
   It returns `reachable`, `cached_entities`, and `error` for each remote.

4. **Re-save the add-on config** after updating. If the add-on schema was
   previously rejecting your configuration, HA may not have written
   `remote_instances` to options.json. Open the add-on Configuration tab in
   Supervisor, verify the entries look correct, and click Save before restarting.

5. **Token**: the token must be a **long-lived access token** created in the
   remote HA's own Profile settings - not the local Supervisor token.

6. **HTTPS / self-signed cert**: if the remote URL starts with `https://` and
   the cert is self-signed, the WebSocket connection will be refused with an SSL
   error. Use `http://` for local network connections, or set up a trusted cert.

7. **Cloudflare-proxied domain**: if the remote HA is behind a Cloudflare
   Tunnel or a Cloudflare-proxied domain, Control Center sends browser-like
   headers to bypass basic bot protection. If Cloudflare is in "Under Attack"
   mode or has strict Bot Fight Mode enabled, the connection will still be
   blocked. Options:
   - Use the remote HA's **direct local IP and port** (e.g.
     `http://192.168.1.50:8123`) instead of the Cloudflare domain - this
     bypasses Cloudflare entirely and is the most reliable option for devices
     on the same LAN.
   - In Cloudflare, create a **WAF skip rule** for the path `/api/websocket`
     on that hostname to bypass bot checks for WebSocket connections. Both
     reading state and sending control commands run over `/api/websocket`, so
     this single rule is enough for full remote functionality.

### How it works

- In the manager's **Organize - Areas & Floors** view, each area row shows a small badge with the remote instance name. Main-instance areas show no badge.
- Each remote instance gets its own persistent WebSocket connection. The initial state snapshot is fetched immediately on connect; area/floor registry data (used for room grouping on the dashboard) is fetched over the same connection in the background and cached within seconds - no separate HTTP calls are made to the remote.
- Remote entity IDs are namespaced internally as `{id}:{entity_id}` (e.g. `garage:light.bedroom`) so they never collide with local or other-remote entities. This namespacing is transparent to users.
- Control commands (from the dashboard and from climate schedules) are routed to the correct instance automatically, and for remote instances they are sent over the same WebSocket used for reading state - so remote control works wherever the state stream works, including through Cloudflare.
- If a remote is unreachable, its entities are excluded gracefully; the rest of the app continues normally.
- The remote HA does not need to know about Control Center at all - only the token is required.

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

The provider **requires** this exact URL to be registered (step 2); it will
only redirect back to addresses you've whitelisted.

#### Behind Cloudflare Tunnel / a reverse proxy

This works without anything special. Point your tunnel's public hostname at the
add-on's **user-dashboard port `8099`** (e.g. `service: http://<HA-IP>:8099`);
the `/api/oauth/*` routes are served by that same app, so no extra route is
needed. Set `oauth_redirect_url` to your **public HTTPS hostname**
(`https://home.example.com`); the add-on builds the redirect from that config
value, not from the proxied request, so Google always sees the real HTTPS URL
even though Cloudflare reaches the add-on over plain HTTP internally. There are
no auth cookies in the flow (the CSRF `state` is a signed token in the URL), so
nothing breaks across the tunnel.

> If you also put **Cloudflare Access** (or any login gateway) in front of this
> hostname, either drop it here and rely on this app's OAuth, or add a bypass
> for `/api/oauth/*`; otherwise the gateway's own login page can intercept the
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
oauth_allowed_domains: ["my.domain"]             # restrict to these domains (optional)
```

`oauth_allowed_domains` restricts sign-in to those email domains (so only
`*@my.domain` can get in). For personal **Gmail** (or anyone without a domain of
their own), list the individual addresses instead with `oauth_allowed_emails:
["alice@gmail.com", "bob@gmail.com"]` - revoke one by removing its line.

**Sign-in fails closed:** if you set neither a domain nor an email allow-list,
OAuth sign-in is **refused** (and the Settings tab shows a warning). Set
`oauth_allow_any: true` only if you deliberately want anyone with a verified
email to sign in - they still land on the onboarding screen with no devices
until an admin assigns some. Restart the add-on after saving. (For a non-Google
provider, `oauth_logo_url` sets the button's logo.)

### 4. Turn it on

Open the sidebar **Settings** tab → **Sign-in methods** and choose **Google**
(OAuth only) or **Both** (password + Google). The option stays disabled until
the credentials above are valid.

### 5. First sign-in & onboarding

When someone signs in for the first time, an app user is created automatically
**with no devices**. They see a "reach out to your administrator" screen until
an admin assigns them devices in **Manage users** (onboarding). Only users with
at least one assigned device see the dashboard.

You can also **pre-create** a user whose username is their email and assign
devices before they ever sign in; an OAuth login for that email then adopts that
account. The same account can be used with a local password *and* OAuth.

### Other (non-Google) providers

Any OpenID Connect provider works; set the endpoints to match it:

```yaml
oauth_provider_name: "Authentik"
oauth_authorize_url: "https://id.example.com/application/o/authorize/"
oauth_token_url: "https://id.example.com/application/o/token/"
oauth_userinfo_url: "https://id.example.com/application/o/userinfo/"
oauth_scopes: "openid email profile"
```

The provider must return an `email` **and a truthy `email_verified`** from its
userinfo endpoint (sign-in is refused otherwise), and you must register the same
`/api/oauth/callback` redirect URI with it.

**Users are not configured here**; manage them in the app (see below). No Home
Assistant token is required either; the add-on talks to HA through the
Supervisor proxy using its auto-injected token.

## Managing users

The sidebar **Control Center** tab opens the management screen directly, the
single source of truth (saved to `/data`). Add, edit, and remove users, and
tick each person's devices from a searchable list of your real HA entities. The
system won't let you remove the last admin. Users are listed alphabetically.

To give **one** user a device whose **type is turned off** (e.g. a single
`switch.*`), use **Add a specific device** in their editor; it searches every
entity and assigns it to that user only. (The Settings **Included entities**
list is the opposite: it shows entities to *everyone* and grants them to
**All devices** users.)

On first run a default admin **`alice` / `changeme`** is created so you can log
in; change its password (and add everyone else) from the Manage users screen
right away.

### Manager role

Tick **Manager** when editing a user to make them a manager. A manager gets an
**Organize** button on their dashboard with two tabs:

- **Devices**: a searchable list of the Home Assistant devices the app exposes
  (those with at least one entity of an enabled type, or in the Included list),
  grouped by area, and each tagged with the **integration** it comes from (a
  small brand badge, e.g. Shelly / MQTT / TP-Link). **Tap a device** to open a
  quick dialog to **rename** it and set its **area**. When remote instances are
  configured, devices from all instances appear here with an instance badge; the
  area dropdown shows only areas from that device's own instance, and changes
  write through to the correct HA automatically.
- **Areas & floors**: a Home Assistant overview-style view that lists each
  **floor** with the areas inside it. The manager can **create a new area**
  (optionally placing it on a floor), **move an area to another floor** (or to
  *No floor*), and **rename** an area. Floors themselves are created/removed in
  Home Assistant; here they're only assigned. With multiple instances configured,
  areas and floors from all of them are shown; the new-area dialog lets the
  manager choose which instance to create it on.

The manager role grants only these organize tools. A manager's **own device
access** is set independently in the editor (turn on **All devices**, or pick a
specific list); a manager does not have to have access to everything.

Renaming a device or moving it (or an area) is written straight back to **Home
Assistant's registry**, so the change shows up in HA itself and anywhere else.
Renaming a device only sets its display name; entity IDs are left unchanged, so
automations keep working.

Managers also get an **edit pencil on each device card** on their dashboard,
which opens the same rename + area popup without leaving the dashboard. This
lets a trusted household member tidy up newly added devices (e.g. a fresh MQTT
device that arrives Unassigned) without giving them Home Assistant admin access.

## Activity log

The **Activity** tab in the sidebar shows who controlled what, newest first;
each entry names the real app user (e.g. "Alice set the temperature to 21°
Bedroom AC"). This is the app's own record, stored in `/data`.

It exists because app users are **not** Home Assistant users: when the add-on
calls a service it uses the Supervisor's token, so Home Assistant's own logbook
can only credit "Supervisor". The Activity tab is the source of truth that
always shows the actual person. You can clear it at any time.

**Scheduled changes** appear here too. A schedule acts in its owner's name, so
each fired event is attributed to that person and carries a **Schedule** badge
plus the schedule's name (e.g. "Cabin Heat Pump was set to Heat 21° - Alice's
schedule 'Weekday heating'"). A scheduled change is only recorded when it
actually reaches an online thermostat, so an event that fires at an unavailable
device leaves no entry.

**These logs / All.** Use the toggle at the top to switch between the app's own
log (**These logs**) and **All**, which also pulls **Home Assistant's own
logbook** live for every device, so you see all device events and not just the
ones triggered through this app. Nothing extra is stored: HA's logbook is
fetched on demand for the visible range. The app's own changes (which HA records
as "by system") are filtered out of the HA side so they don't show up twice;
you still see them as the proper named entry from this app's log.

**History graph.** Tap a device name (or filter to a single device) to open an
**interactive, zoomable history graph** pulled live from Home Assistant. Like
HA's own entity history, it charts the meaningful numbers for that device: a
climate shows its **current and target temperature** as two lines, a light its
brightness, a fan its speed, a media player its volume, a cover its position. A
plain numeric sensor shows its value; on/off-style entities (locks, switches)
fall back to a stepped chart of their state. Drag across it to zoom in;
double-click to reset.

> Note: a climate device is a single entity (its target and current temperature
> are *attributes* of it, not separate entities), so both appear on the one
> device's graph rather than as two devices.

## Backup & restore

The **Settings** tab has a **Backup & restore** card. **Export** downloads a
single JSON file containing everything in `/data`: users, passwords, each
user's device assignments, all settings, climate schedules and their per-user
scheduling permissions, per-user device lists, the activity log and the uploaded
app icon. Keep it somewhere safe (it contains passwords).

If you uninstall and reinstall the add-on, open Settings → **Restore from
file** and pick that JSON to bring everything back exactly as it was. Restoring
replaces all current users, assignments, settings, schedules, scheduling
permissions and lists. A backup made by an older version that predates these
extras simply leaves the current ones untouched instead of clearing them.

## Session expiry

A login lasts **30 days** and is **rolling**: while a user keeps using the app,
their session is refreshed automatically in the background, so regular use keeps
them signed in. Only a session left completely unused for 30 days expires.

When a session does expire or become invalid, the app redirects silently to the login screen with a "Your session has ended. Please log in again" notice. No error banner is shown on the dashboard. This is distinct from an expired **account**, which shows a message to contact the system administrator.

(Sessions survive add-on restarts and updates because the signing secret is stored under `/data`. If users are logged out on every restart, check that the add-on's `/data` is writable/persistent.)

## Live updates

The dashboard updates in **real time** over a WebSocket: any change (from the
app, a physical switch, or an automation) appears within a fraction of a second,
with no refresh. If the connection drops, a **"Connection lost. Reconnecting…"**
notice appears with a **Retry now** button; it clears itself and re-syncs once
the connection is back. The reconnect is driven by a heartbeat, so it recovers
even when a reverse proxy (e.g. Cloudflare) closes an idle connection silently
without the browser noticing; **Retry now** forces an immediate full refresh.
When a new version of the app is deployed, open
dashboards **reload themselves** to pick it up; no manual refresh needed. The
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
  Android). Menus, dialogs, sheets, dashboard sections and the views you navigate
  **animate open and closed** (with a matching tap on menus), and a rejected
  action (like a wrong password change) gives a firmer buzz. All of this respects
  your device's "reduce motion" setting.

> Changing the **App icon** after people have installed the PWA won't update an
> icon that's already on a home screen, because iOS (and some Android launchers)
> freeze it at install time. Remove and re-add the app to pick up a new icon.

## Climate scheduling

Users can automate their thermostats through the **Schedules** item in the
profile menu. Schedules are listed alphabetically by name.

### Admin setup

In the **Control Center** sidebar tab, open a user and scroll to **Climate
scheduling**. The permission panel shows a grid of the climate devices that user
already controls; tap a device to grant or revoke scheduling access for it.

Turn on **Allow all climate devices (current & future)** to grant scheduling for
every climate device the user can control, including any added later - so you
don't have to tick each new thermostat by hand. It never exceeds the devices the
user is actually allowed to control (locked devices stay locked).

The **Schedules** tab in the admin view pivots **By user** or **By thermostat**,
with a searchable selector to focus on one at a time:

- **By user**: that user's scheduling access (the same permission grid and
  All-climate toggle as the user editor) plus all of their schedules - expand any
  to see its events, and enable/disable or delete it.
- **By thermostat**: which users may schedule it (toggle access from this side
  too), plus its effective program merged across every user's schedules, shown on
  a week strip with warnings on any overlapping (conflicting) events.

### User view

The **Schedules** panel lets each user:

1. Create named schedules using the searchable schedule switcher (keyboard
   navigation supported). The switcher dropdown has a **Create schedule** action
   at the bottom.
2. Pick target thermostats as chips - tap a chip's **x** to remove it, or tap
   **+ AC** to open a searchable multi-select for adding more.
3. Add **events** - each event fires at a specific time on selected days (the
   day picker and week preview start on Sunday), with Weekdays / Weekends /
   Every day presets, and sets a mode (off, cool, heat, auto, dry, fan) and a
   temperature. The temperature slider range and step are read from the
   thermostat (from the first one that reports a range, so an offline unit doesn't
   force a fallback), and the unit (C or F) is Home Assistant's own configured unit
   - schedules are always set and shown in that unit, and HA converts the setpoint
   to each thermostat's native unit when the event fires, so a mixed fleet needs
   no manual conversion. If no thermostat reports a range, the slider falls back to
   a sensible range in that same unit. You can also set an optional **fan speed**,
   which is off by default: flip the **Fan speed** toggle on to reveal the picker,
   and leave it off (or turn it off) to leave each thermostat's fan untouched. When
   a
   schedule targets thermostats with different fan controls, the editor groups
   them by fan type and shows one control per type (the same slider and buttons as
   the device card), so numeric, percentage, and named-speed units are all
   reachable - each thermostat gets the speed picked for its own type. The offered
   values come straight from each unit's real fan modes, so a unit is only ever
   sent a speed it actually has; anything left unset is untouched. Fan is set once,
   on the event, and never re-sent. (Schedules saved before this keep working.)
4. See a **week preview strip** with hour-axis labels (0, 6, 12, 18, 24) and
   colour coding by mode/setpoint.
5. Switch to **By thermostat** to see the merged program for one unit across all
   active schedules, with a warning icon on any same-time conflicts.

Events are edge-triggered: a thermostat holds whatever state the last fired
event set until the next event fires. The scheduler checks for events every
30 seconds. If an event can't set a unit - it's offline, unavailable, disabled,
or keeps reverting - the add-on log gets a concise line and Home Assistant shows
a **notification** naming the unit and the reason; the notification clears once
the unit is set successfully.

**Only sends what changed:** when an event fires, the scheduler checks the unit's
current state and sends only the commands that actually differ. If the thermostat
already holds the target temperature or fan speed, those commands are skipped and
only the mode change is sent. This matters for units (some Midea ACs) that bounce
back to off when a temperature or fan command lands right after being told to turn
on: sending just the mode change - the same single command they accept when turned
on by hand - lets the start stick. Skipping only ever sends fewer commands, never
different ones, so units that already worked are unaffected.

**Self-verifying:** some thermostats (e.g. Honeywell) don't always apply a change
on the first command, and quietly revert. For about 10 minutes after an event
fires, the scheduler keeps checking that the thermostat's live mode and
temperature match what the schedule set, and re-sends the command (at most once
every couple of minutes) if they don't. A change that didn't take fixes itself.

If a **person** changes the thermostat during that window (in Home Assistant, or
through Control Center), the schedule stops re-applying to that device until its
next event, so it never fights a real person. Only a change with no one behind it
(a device quietly bouncing back on its own) keeps getting re-applied, and even
then only a couple of times: if a unit keeps reverting, the scheduler **stands
down** rather than flapping, and notes it in the Activity feed.

### Overrides (holidays and special events)

An **override** is a schedule that runs only on a set of dates and, while active,
takes over from your weekly schedules for the thermostats you include. Use it for
a holiday setback, a party, or any one-off. Open the **Overrides** tab in the
Schedules panel and **Create override**, then:

1. Set an **active date range** (From / To). For a single day, leave both the same.
2. Pick the **thermostats** it applies to (same picker as weekly schedules).
3. Add **events**. An override's events apply **every day** in the range (there is
   no day-of-week picker), so a single event acts as an all-day hold, and several
   events vary the temperature across the day.

While an override is active, its thermostats follow only the override; every
weekly schedule is ignored for those thermostats until the range ends, when they
return to their weekly program. An override with no events does nothing (it won't
take over), and you can turn one off with its switch without deleting it. If you
turn on an override whose day has already started, the event in effect right now
is applied immediately rather than waiting for the next one.

**Diagnostics:** if you need to check whether schedules are firing, the add-on
log (Supervisor > Control Center > Log) records the details. Lines tagged
`[sched]` show each event as it fires (with the device's state before and after,
and how long the command took), plus failures, offline devices, drift re-applies,
disabled schedules, and a warning whenever the loop skipped one or more minutes
(so events in that window did not fire). Lines tagged `[device]` show thermostat
mode and setpoint changes, so you can confirm a scheduled change actually took
and spot any that bounced back.

## Lists

Every user can create their own **lists** to organise their devices - handy when
you have a lot of them or want to sort them differently than by Type, Area, or
Floor. Open the account menu (the avatar, top-right) and choose **Lists**:

- **Create** a list by name; make as many as you like.
- **Edit devices** on a list to add or remove any of your devices (a device can
  be in several lists).
- **Reorder** lists by dragging the handle (mouse or touch). The order you set is
  the order the filter chips appear in on the dashboard.
- **Rename** or **Delete** a list at any time. Deleting a list never affects the
  devices themselves.

Each list then appears as a chip in the dashboard's sort row, as an alternative
to Type/Area/Floor (the chips sit on one line and scroll sideways when there are
many). Tap a list to show **only** its devices; tap **Type** (or Area/Floor) to
go back to all your devices. Lists are per-user - everyone manages their own, and
one person's lists are never visible to another.

## Telegram Notifications

Some households use Telegram channels as a cheap, unlimited place to keep logs and
notifications. This feature lets users **read and search** those channels (with inline photos)
from inside Control Center, with the admin deciding which channels exist and who
may see each one. It is a self-contained, optional module: if no account is
connected it stays hidden from users and affects nothing else.

It reads Telegram over the **user API** (so full history and search work), which
means connecting one Telegram **account** that is a member of the channels.
**Strongly recommended:** use a **dedicated account** that only sits in the log
channels (nothing personal to lose) and give it a **2FA password**. You can see and
revoke the add-on's session anytime in Telegram (**Settings, Devices** - it appears
as "Control Center"); revoking it instantly disconnects Control Center.

### Admin setup

Open the sidebar (Ingress) admin panel and pick the **Telegram** tab:

1. **Channels** - add each channel you want to make available, giving it a short
   **id** and a **display name**. The id identifies the channel; the name is what
   users see.
2. **Who can see which channel** - a grid of users by channels. Tick a channel for
   a user, or tick **All** to give them every channel (including ones you add
   later). Changes save immediately.
3. **Connect an account** - first create an app at
   [my.telegram.org](https://my.telegram.org/apps) to get an `api_id` and
   `api_hash`. Then connect one of two ways:
   - **Sign in here (in-app)** - enter your `api_id`, `api_hash`, and phone number;
     type the code Telegram sends you (and your 2FA password if set). Simplest.
   - **Paste a session string (offline)** - run `tools/telegram_login.py` on your
     own computer (`pip install telethon`), which logs in there and prints a
     session string; paste it, with the `api_id`/`api_hash`, into the credentials
     card. This keeps the login (phone, code, password) entirely off the add-on.

   Credentials are stored **write-only** (never shown back). The **Connection** line
   at the top of the tab shows whether the account is connected.

### User view

Users with at least one shared channel get a **Telegram Notifications** item in
their profile menu. It opens a **list of their channels**; tapping one shows that
channel's log (read-only, newest-first). **Load older** pages back, **Search**
filters within the channel, and **‹ Channels** returns to the list. **Images posted
to a channel are shown inline** with their caption. New messages appear
automatically while a channel is open. Users only ever see the channels an admin
shared with them.

A red **unread dot** appears on the account avatar and the **Telegram
Notifications** menu item (and on each unread channel in the list) when there are
new messages since you last opened that channel; opening it clears the dot. Unread
is per user and separate from Telegram's own read state, so reading here never
clears notifications in the real Telegram app.

## Supported devices

Lights (with brightness), switches, fans (with speed), climate (modes +
temperature), covers (open/close + position), locks, media players (transport +
volume), scenes, scripts, automations, buttons, vacuums. Any other entity
(e.g. sensors) is shown read-only. Every device has a detail view with its full
state and attributes.

## Security notes

- Access is enforced server-side: users can only control entities assigned to
  them, and only through a fixed allow-list of services.
- Passwords are stored **hashed** (PBKDF2-HMAC-SHA256 with a per-password salt);
  the store never holds plaintext, and any legacy plaintext is upgraded to a
  hash automatically. Keep the store private regardless.
- **Login is rate-limited** per username to slow brute-force attempts (the
  spoofable client `X-Forwarded-For` is ignored), with constant-time password
  checks that don't reveal whether a username exists.
- The session-signing secret is auto-generated and persisted on first run unless
  you set `jwt_secret`; rotate it from **Settings - Session security**.
- **Device IDs and time ranges are validated** before any Home Assistant call, so
  a logged-in user can't smuggle a path into the HA API.
- Responses send `X-Content-Type-Options: nosniff`. Set `block_iframe_embedding:
  true` to also send `X-Frame-Options: DENY` on the user dashboard
  (anti-clickjacking); off by default so it won't break a `panel_iframe` embed,
  and the management UI is always exempt.
- Standalone only: the management port binds to `127.0.0.1` by default
  (`INGRESS_BIND=0.0.0.0` to override). The add-on keeps it behind Ingress.
