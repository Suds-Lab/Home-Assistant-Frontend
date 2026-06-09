# Changelog

## 1.35.1
- Themed the range sliders (fan speed, brightness, volume) to match the app - HA-blue fill and thumb on a muted track - instead of the browser's default control.

## 1.35.0
- Climate units with **named fan speeds** (Low / LowMedium / Medium / MediumHigh / High, etc.) now show those as a **slider** (with a haptic tick per step), reporting the matching `fan_mode` back to Home Assistant - instead of a long row of buttons. **Night** is hidden, and non-speed modes like **Auto** stay as a button. Units with ordinary fan modes are unchanged.

## 1.34.1
- Fan speed (and brightness / volume) sliders now give a **light haptic tick on each step** as you drag, and only send to Home Assistant when you let go. The fan slider snaps to the fan's real speed steps, so each "bump" is an actual speed.

## 1.34.0
- Subtle **haptic feedback** on tap: a light tick when you toggle a switch, pick a climate mode, step the temperature, change tabs - any control. It fires on press for a natural feel, is kept short and throttled so it never buzzes, and is a graceful no-op where the browser has no Vibration API (so iPhone web and desktop simply feel nothing; Android gets the tick). Disable per-device by setting `localStorage ha_haptics = '0'`.

## 1.33.0
- Climate cards: **press and hold** the + / − buttons to keep adjusting the temperature (it speeds up the longer you hold), and the new target is **sent once when you let go** - a more natural, fluid way to set the temperature than tapping repeatedly. A single tap still works, and keyboard users can still step it.

## 1.32.3
- Live updates: fixed **"All devices" users and managers getting no live pushes** - their device states (and newly-added devices) only refreshed on the 30-second safety-net poll. They now receive instant WebSocket updates for everything they can see, like specific-device users always did.
- A newly-added entity now refreshes the room/floor cache, so it lands in the right area instead of "Other".
- Settings are cached for a few seconds so the per-event access check on the live stream doesn't hit the disk each time.

## 1.32.2
- Manager again gets **All devices by default** - ticking Manager turns on All and hides the device picker, exactly like choosing "All devices". You can still **add specific extra entities** on top (including ones whose type is turned off) via the per-user search, for both Manager and "All devices" users.
- Fixed: those per-user "specific device" extras now reliably grant access even when the user has "All devices" (previously they were ignored for All users).

## 1.32.1
- Dashboard: when grouping by Area or Floor, each area's section header now shows the area's Home Assistant icon (same dynamic mdi icons as the Organize view). Areas without an icon set just show the name.

## 1.32.0
- Organize → Areas & floors: each area now shows **its Home Assistant icon** (the `mdi:*` icon set on the area). Icons are fetched on demand and cached by the add-on (served locally, so browsers don't call any external service); areas without an icon get a neutral placeholder.

## 1.31.5
- Organize: the Devices / Areas & floors tabs use the connected "split" segmented control again (matching the Activity These/All toggle).

## 1.31.4
- Climate card humidity now reads cleanly as "Now 24° · 52% humidity" (the temperature and humidity were running together as "24°52% humidity" on a stale stylesheet); the separator/spacing no longer depend on CSS.

## 1.31.3
- Climate cards now show the **current humidity** next to the current temperature, when the device reports it.

## 1.31.2
- Dashboard header: on narrow phones (or with a large accessibility font), the title and the action buttons (Organize, Log out…) no longer overlap - the buttons now wrap onto their own row instead.

## 1.31.1
- Organize: the **Devices / Areas & floors** sub-tabs and the per-area **floor dropdown** now use the app's themed controls (they were rendering unstyled). Bumped the service-worker cache so the styling refreshes.

## 1.31.0
- Organize now has two tabs: **Devices** (move devices into areas, as before) and **Areas & floors** - a Home Assistant overview-style view where a manager can **create areas** and **move them between floors** (and rename them). Floors themselves are still managed in Home Assistant; here they're only assigned.
- Manager access is no longer forced to "All devices": the **Manager** role now only grants the organize tools, and a manager's own device access (All, or a specific list) is set independently - so you can give a manager just a few devices.

## 1.30.2
- iPhone: the dashboard header (and the login/onboarding screens) now keep clear of the **Dynamic Island / notch** and the home indicator by honouring the safe-area insets, instead of sitting right at the top edge.

## 1.30.1
- Activity (All): a busy day's logbook no longer freezes the page - only the most recent 500 entries are rendered, with a note to narrow the date range or filter to a device to see the rest. The server also caps how much it returns.
- Activity (All): your own actions now show the real **app user's name** instead of a duplicate "by system" row - the match between the app's log and HA's logbook is wider and more robust (handled on both server and client).

## 1.30.0
- Activity: a **These logs / All** toggle. **All** also pulls **Home Assistant's own logbook** live for every device (nothing extra is stored) so you see all device events, not just the app's actions. The app's own changes - which HA records as "by system" - are filtered out so they don't appear twice.
- Activity: tap a device (or filter to one) to open an **interactive, zoomable history graph** pulled live from HA. Like HA's own entity history it plots the meaningful numbers per device - a climate's **current + target temperature** as two lines, a light's brightness, a fan's speed, a media player's volume, a cover's position - and falls back to a stepped chart of the state for on/off-style devices (locks, switches). Drag to zoom, double-click to reset. (Built on the lightweight uPlot library, vendored locally.)

## 1.29.1
- Activity: stack the filter controls vertically on phones so the wide date pickers can't run off the screen edge.

## 1.29.0
- Manage users: a **Manager** badge marks manager accounts in the user list.
- Activity tab is now **full width** on desktop and mobile, no longer overflows on phones, and you can **click a device name to filter to just that device** (with a "Show all" to clear) - like the Home Assistant logbook.
- Settings: fixed the title/description text running together; cleaner spacing.
- Manage users: clearer spacing and labels around the two device searches.

## 1.28.2
- Manager organizer now only lists devices the app actually exposes (at least one entity of an enabled type or in the Included list), instead of every Home Assistant device; the update endpoint enforces the same.

## 1.28.1
- Backup: an "Include the activity log" option (on by default) lets you export without the log for a smaller, cleaner backup.

## 1.28.0
- Managers get an edit **pencil on every device card** on the dashboard; it opens a popup to rename the device and set its area (written to HA; entity IDs unchanged, so automations keep working). The pencil is hidden for non-managers.

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
