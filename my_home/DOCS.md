# My Home

A per-user device dashboard. Each person sees and controls **only the devices
assigned to them** - lights, switches, fans, climate, covers, locks, media
players and more.

There are two interfaces:

- **Management** - the **My Home** tab in the HA sidebar (admin-only, via
  Ingress). Add/edit users and assign their devices. No separate password; HA's
  own login protects it, like the Terminal add-on.
- **User dashboard** - published at `http://<home-assistant>:8099`. Household
  members open it in any browser and log in with the account you created.

## Installation

1. Install the add-on and click **Start**.
2. Open the **My Home** tab in the sidebar - it goes straight to the management
   screen (no login).
3. Add your household members under **Manage users** and assign each their
   devices.
4. Send each member to `http://<your-home-assistant>:8099` to log in and
   control their devices. Enable port `8099` in the **Network** section if it
   isn't already.

## Configuration

```yaml
app_name: My Home                  # title shown in the app / tab / installed PWA
app_icon: "🏠"                     # an emoji, shown beside the name and as the tab icon
jwt_secret: a-long-random-string   # signs login sessions
```

| Option | Description |
|--------|-------------|
| `app_name` | The name shown in the dashboard header, browser tab, and the installed home-screen (PWA) app. Defaults to "My Home". |
| `app_icon` | An emoji shown next to the name in the app and used as the browser-tab favicon. Defaults to 🏠. |
| `device_types` | Which entity domains can be assigned in **Manage users** (e.g. `light`, `climate`, `cover`). Leave the list empty to allow every domain. |
| `jwt_secret` | Secret used to sign login sessions. Set a long random string. |

These options are just the **defaults**. Once the add-on is running, manage
everything live in the sidebar's **Settings** tab: the browser-tab title, the
in-app name, the home-screen emoji, an **uploaded app-icon image** (PWA /
favicon), and which **device types** are available. Those settings are stored
in `/data` and override the config.

**Users are not configured here** - manage them in the app (see below). No Home
Assistant token is required either; the add-on talks to HA through the
Supervisor proxy using its auto-injected token.

## Managing users

The sidebar **My Home** tab opens the management screen directly - the single
source of truth (saved to `/data`). Add, edit, and remove users, and tick each
person's devices from a searchable list of your real HA entities. The system
won't let you remove the last admin.

On first run a default admin **`alice` / `changeme`** is created so you can log
in - change its password (and add everyone else) from the Manage users screen
right away.

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
