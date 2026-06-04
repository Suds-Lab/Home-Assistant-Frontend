# Changelog

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
