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
- **Home icon** - an emoji used beside the title and as the browser-tab favicon.
- **App icon** - an **uploaded image** used for the PWA / favicon (overrides the
  emoji).
- **Device types** - which entity domains can be assigned in **Manage users**.
- **Sign-in methods** - Local password, OAuth, or both (see below).

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
Restart the add-on after saving.

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

The sidebar **My Home** tab opens the management screen directly - the single
source of truth (saved to `/data`). Add, edit, and remove users, and tick each
person's devices from a searchable list of your real HA entities. The system
won't let you remove the last admin.

On first run a default admin **`alice` / `changeme`** is created so you can log
in - change its password (and add everyone else) from the Manage users screen
right away.

## Activity log

The **Activity** tab in the sidebar shows who controlled what, newest first -
each entry names the real app user (e.g. "Alice set the temperature to 21°
Bedroom AC"). This is the app's own record, stored in `/data`.

It exists because app users are **not** Home Assistant users: when the add-on
calls a service it uses the Supervisor's token, so Home Assistant's own logbook
can only credit "Supervisor". The Activity tab is the source of truth that
always shows the actual person. You can clear it at any time.

## Backup & restore

The **Settings** tab has a **Backup & restore** card. **Export** downloads a
single JSON file containing everything in `/data` - users, passwords, each
user's device assignments, all settings, the activity log and the uploaded app
icon. Keep it somewhere safe (it contains passwords).

If you uninstall and reinstall the add-on, open Settings → **Restore from
file** and pick that JSON to bring everything back exactly as it was. Restoring
replaces all current users, assignments and settings.

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
