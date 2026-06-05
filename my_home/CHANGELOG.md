# Changelog

## 1.27.0
- Manage users: a per-user **"Add a specific device"** search lets you grant one user any entity (even of a disabled type) without adding it globally - so it doesn't also land on every "All devices" user.

## 1.26.2
- Device picker: cleaner, mobile-friendly rows - the entity name sits over a muted entity id (no more crowding/wrapping), larger touch targets, and a clear highlight on selected rows.

## 1.26.1
- Organize tab: added a device search, and a pencil on each device that opens a quick dialog to rename it and set its area (both written back to Home Assistant).

## 1.26.0
- New **Manager** role: managers get an **Organize** view on their dashboard to move Home Assistant devices between areas (and place newly-added ones like MQTT devices). Changes are written back to HA's device registry. Managers always have full device access; set the role in Manage users.

## 1.25.1
- Manage users: the device picker now refreshes when you open a user, so entities just added to the global "Included entities" list appear without reloading the page.

## 1.25.0
- Settings: new **Included entities** list - hand-pick specific entities to always show in the picker and grant to "All devices" users, even when their device type is turned off. Lets you hide a noisy domain (e.g. switches) without losing the few you want. Uses a Gmail-style search + chips field.

## 1.24.5
- Polished the grouped dashboard: floor/type/area headers with a divider and accent count pills, rounded accordion rows for areas with hover, and a subtle fade-in when sections expand.

## 1.24.4
- Floor grouping: areas under a floor are now collapsed by default - expand an area to reveal its devices (remembered per browser).

## 1.24.3
- Floor grouping is now hierarchical: each floor shows its areas as sub-sections (Floor → Area → devices).

## 1.24.2
- Dashboard grouping now offers **Type / Area / Floor** (was Type / Room). Devices without an area or floor group under "Other".

## 1.24.1
- Compact view: climate cards now shrink properly by hiding the fan-speed and swing controls (temperature and mode stay); switch off compact for the full controls.

## 1.24.0
- Dashboard: when you have several devices, group them by **Type** or **Room** and collapse/expand each section (remembered per browser). The user dashboard now knows each device's room/floor from Home Assistant.

## 1.23.3
- Climate cards: more space between the mode/fan/swing buttons (gap 14px → 18px).

## 1.23.2
- Climate swing: recognise abbreviated 2-axis modes (e.g. Off/H/V/H+V), not just off/horizontal/vertical/both, so those also show the Horizontal/Vertical toggles.

## 1.23.1
- Climate cards: revert the larger buttons back to their normal size; the roominess now comes from wider gaps between buttons and sections, not bigger buttons.

## 1.23.0
- Climate swing: for 2-axis swing (off/horizontal/vertical/both), show independent **Horizontal** and **Vertical** toggles instead of four exclusive buttons, and derive the swing mode from the pair. Other swing setups keep the button list.

## 1.22.8
- Roomier climate cards: taller mode/fan/swing buttons, larger gaps, and more space between the temperature, mode, fan and swing sections. Compact view stays dense.

## 1.22.7
- Roomier sign-in form: even spacing between the fields and button, taller inputs, and a full-width Sign in button.

## 1.22.6
- Fix a brief theme flash on open: the theme is now set before first paint via a small inline script, instead of after the in-browser React/Babel load.

## 1.22.5
- More spacing around the sign-in form and between device cards for a roomier layout.

## 1.22.4
- Climate: lengthen the temperature send delay to 1.5s so more taps are batched into a single update.

## 1.22.3
- Activity log: collapse a run of continuous adjustments (temperature, fan speed, cover position, volume) by the same user on the same device into a single entry showing the final value, instead of logging every step.

## 1.22.2
- Climate: the temperature stepper now updates instantly on each tap and sends a single update after you stop, instead of waiting on every press.

## 1.22.1
- OAuth: use the account's real name from the provider (e.g. the Google name) as the display name on first sign-in, and greet the user by it. Falls back to the email's local part; never overwrites an admin-edited name.

## 1.22.0
- Redesigned to match Home Assistant's look: HA blue accent (#03a9f4), HA-style backgrounds/cards (rounded, elevated), Roboto typography.
- More symmetrical, professional layout - centered tabs and action buttons.
- New **Compact view** toggle on the dashboard (shown when you have several devices): smaller, denser cards. The grid is responsive too - more columns on larger screens, fewer on phones.

## 1.21.2
- Fix an oversized Google/OAuth logo (and device icons) for users on a stale cached stylesheet, by giving the inline SVGs/images intrinsic width/height. Bumped the service-worker cache so stale CSS refreshes.

## 1.21.1
- OAuth button: fall back to the generic icon if a configured `oauth_logo_url` fails to load (broken/blocked URL).

## 1.21.0
- OAuth: `oauth_allowed_emails` allow-lists individual addresses even outside the allowed domains.
- OAuth: the sign-in button shows the Google logo for Google, a configurable `oauth_logo_url` for other providers, or a generic icon.

## 1.20.0
- Manage users: an **All devices** toggle grants a user every device (including ones added later) and hides the picker. Respects the allowed device types.

## 1.19.3
- Docs: add the OAuth / Google sign-in setup to the main README guide (it was only in the add-on Documentation tab).

## 1.19.2
- Docs: explain the OAuth redirect URI works behind Cloudflare Tunnel / a reverse proxy, with a Cloudflare Access caveat.

## 1.19.1
- Docs: full step-by-step Google / OAuth setup walkthrough, plus a template for other OpenID Connect providers.

## 1.19.0
- Add OAuth / OpenID Connect sign-in (Google by default; any OIDC provider supported). Credentials live in the add-on config.
- Optional `oauth_allowed_domains` to restrict sign-in to specific email domains.
- Admin chooses sign-in methods (Local / OAuth / Both) in Settings.
- First OAuth sign-in auto-creates an un-onboarded user (no devices) who sees a "contact your administrator" screen until devices are assigned.
- Fix iOS home-screen install using the configured app name and icon (iOS reads the apple-* tags, not the web manifest).
- Add a warning that the backup export contains plain-text passwords.

## 1.18.0
- Add full backup **export / import** (users, passwords, device assignments, settings, activity log and app icon) so an uninstall + reinstall keeps all data intact.

## 1.17.1
- Reject duplicate usernames when creating/renaming a user instead of silently overwriting the existing account.

## 1.17.0
- Activity log: time-range picker, previous/next day paging, filter by user, multi-select filter by item. Removed the Clear button.

## 1.16.0
- Restyle the activity log like the Home Assistant logbook (grouped by day, device-type icons) with a filter by user.

## 1.15.2
- Install banner now names the app ("Install <App name>") with the configured icon.

## 1.15.1
- Fix the dashboard header buttons ballooning / wrapping on narrow screens.

## 1.15.0
- Split **Title** (login + dashboard heading) from **App name** (browser tab + installed PWA name).
- Remove the now-stale `app_name`, `app_icon` and `device_types` add-on options (managed in Settings instead).

## 1.14.0
- Add an in-app **activity log** that records who controlled what, with real per-user attribution.

## 1.13.0
- Use Material Design Icons for device types in the management entity picker and Settings.
