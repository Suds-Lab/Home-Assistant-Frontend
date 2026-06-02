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

The options below seed the **first run only**. After that, manage everyone from
the in-app **Manage users** screen (saved to `/data`, so it persists).

```yaml
jwt_secret: a-long-random-string   # signs login sessions
users:
  - username: alice
    password: changeme
    displayName: Alice
    admin: true                    # can open "Manage users"
    entities:
      - light.bedroom
      - climate.bedroom_ac
```

| Option | Description |
|--------|-------------|
| `jwt_secret` | Secret used to sign login sessions. Set a long random string. |
| `users` | Seed accounts. Each needs `username`, `password`, optional `displayName`, optional `admin`, and the `entities` they may control. |

No Home Assistant token is required - the add-on talks to HA through the
Supervisor proxy using its auto-injected token.

## Managing users

The sidebar **My Home** tab opens the management screen directly. Add, edit, and
remove users, and tick each person's devices from a searchable list of your real
HA entities. The system won't let you remove the last admin.

The `users` you set in **Configuration** seed the first run only; after that the
management screen is the source of truth (saved to `/data`).

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
