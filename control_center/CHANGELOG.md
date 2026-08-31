# Changelog

## 2.9.34
- **Compact view: climate cards now keep both mode and fan, as dropdowns.**
  Previously compact dropped the fan section entirely and kept mode as a full
  button row. Now a compact climate card shows a small **Mode** dropdown and a
  **Fan** dropdown instead, so you can still change fan speed without leaving
  compact - and the card stays small. Regular (comfortable) view is unchanged
  (mode buttons + fan slider). The schedule editor keeps the full fan control in
  both views.

## 2.9.33
- **Fix: schedule fan controls were invisible in Compact view.** Compact view
  hides the fan section on dashboard cards (`.compact .fan-modes`), and since the
  schedule editor reuses that same control, it was hidden there too - the fan
  groups showed their labels but no slider or buttons. The editor's fan control is
  now exempt from the compact rule.

## 2.9.32
- **Schedule fan speed now covers every thermostat, per fan type.** Instead of one
  generic Auto/Low/Medium/High, the event editor groups the schedule's thermostats
  by their fan vocabulary and shows one control per type - the same slider (for
  graded speeds) and buttons the device card uses. Each thermostat gets the speed
  picked for its own type, so numeric (0-6), percentage (25-100%), and named
  fleets are all reachable. Values come straight from each unit's real fan modes,
  so the exact-match-at-fire safety is kept and a unit is never sent a speed it
  does not have. Units are matched to their group by the set of fan modes (order-
  and encoding-independent), and schedules saved with the old single level keep
  working.

## 2.9.31
- **Searchable dropdowns (device/thermostat pickers) never fall off screen.**
  Instead of flipping between left- and right-aligned - which on a narrow phone
  could push a wide menu off the opposite edge - the menu now clamps its
  horizontal position to the viewport: anchored under its trigger, shifted left
  just enough to fit when it would overrun the right edge, and never past the
  left edge. It re-checks continuously while open, so it stays on screen even
  when the trigger moves - e.g. adding a chip pushes "+ Add" sideways.

## 2.9.30
- **Fan speed is back in schedules - the safe way.** You pick a generic level
  (Auto / Low / Medium / High) and at fire time the scheduler only sends it to a
  thermostat whose own fan speeds contain that **exact** word (case-insensitive).
  No guessing: `auto` is never treated as `auto_low`/`fan_auto`, `low` is never
  `on_low`/`25%`/`0`. Anything ambiguous is left untouched. It's sent **once**,
  on the event, and never re-applied by the self-verify loop - so a finicky fan
  can't be re-hammered into the flap that removing it in 2.9.29 fixed.
- **Schedule-failure logging + Home Assistant notification.** When an event can't
  set a unit - offline, unavailable, disabled, or it keeps reverting - the add-on
  log gets one concise red line and HA raises a persistent notification naming
  the unit and the reason. The notification clears automatically once the unit is
  set successfully.

## 2.9.29
- **Schedules no longer control fan speed.** Sending a fan mode was knocking some
  thermostats off, which the self-verify loop then read as drift and re-applied -
  a flap that got worse as recent versions mapped fan speeds onto more units.
  Schedules now set **mode and temperature only**; fan is left to the thermostat.
  The fan picker is removed from the event editor, the scheduler never calls
  `set_fan_mode`, and any leftover `fan` values are stripped from stored
  schedules automatically on load. (The self-verify re-apply, with its 2-try
  give-up, and per-user/override scheduling are unchanged.)

## 2.9.28
- **Schedules now use Home Assistant's configured temperature unit** for every
  user, instead of guessing from a thermostat's min temperature. The old guess
  read the *first* granted thermostat and inferred °C/°F from its range, so some
  users saw °C when their fleet was really °F (or vice versa). Now the backend
  reads HA's unit system once and the schedule editor always shows that unit; HA
  itself converts each setpoint to the thermostat's native unit when the event
  fires.
- **Offline thermostats no longer show a phantom 22° setpoint.** An unreachable
  climate device (HA state `unavailable`) dropped its attributes, so the card
  fell back to a made-up 22. It now shows a dash and locks the +/- controls,
  like an off unit.

## 2.9.27
- **Consistent checkbox picker for lists and schedules.** Choosing devices for a
  list, or thermostats for a schedule, now uses the same compact tick menu the
  Activity log uses for its device filter: the chosen items stay as removable
  pills and a **+ Add** button opens a searchable list you check more from (it
  stays open so you can tick several at once). The scheduler's single-add picker
  becomes multi-select.
- The searchable dropdown now keeps itself on-screen: it right-aligns when a
  left-anchored menu would run off the right edge, and re-checks as the trigger
  moves (for example when adding a pill wraps the row and shifts "+ Add"), so it
  never hangs off either edge.

## 2.9.26
- **Fan mapping now handles compound speed names** like an Ecobee's
  `on_low` / `on_high` / `auto_low` / `auto_high`. Previously the semantic fan
  levels couldn't match those, so the fan pick was silently ignored on such
  units. Now a speed (Low/Medium/High) maps to the matching `on_*` speed (run
  the fan at that speed) and Auto maps to an `auto_*` mode (let the thermostat
  decide), reading the speed word out of the compound name.

## 2.9.25
- **Respect hidden entities from Home Assistant.** A device you've marked
  **Hidden** in HA no longer shows up in Control Center's list, matching how HA
  keeps hidden entities off its own auto-generated dashboards. (Entities you
  **Disable** in HA were already absent, because HA drops them from its live
  state entirely; this adds the hidden ones, which HA still reports a state for.
  Disabled entities are filtered too, as a belt-and-suspenders measure.)

## 2.9.24
- **Smarter fan speed for schedules.** Instead of listing every raw speed name
  that the targeted thermostats report (`silent`, `25%`, `mediumHigh`, `4`, and
  so on), the event editor now offers one simple choice: **Auto, Low, Medium, or
  High**. When the schedule fires, each thermostat is set to its own nearest
  matching speed, so a single pick works even across a mixed fleet: **Low** picks
  the slowest speed a unit has, **High** the fastest, **Medium** the middle one,
  and **Auto** its automatic mode. A unit with no matching speed is simply left
  alone. Schedules saved with a specific speed before this change keep working.

## 2.9.23
- Follow-up to the fan fix for **mixed fleets**. When a schedule targets several
  thermostats that use different fan-speed names (some report `low/medium/high`,
  others `25%/50%/100%`, `silent/quiet/…`, or numbers), the editor now offers the
  combined set of speeds with a note that each unit applies a speed only if it has
  it, instead of hiding the fan row whenever the units didn't perfectly match.

## 2.9.22
- Fixed schedules **fighting a thermostat**. The fan-speed picker used a generic
  list that ignored what the unit actually supports, so a schedule could send a
  fan mode the AC rejected, and some units dropped to **off**, which the self-check
  then kept re-applying every couple of minutes. Now the picker only offers the
  fan speeds the targeted thermostat(s) actually support (and hides the row when
  a device has no fan control), the scheduler never sends an unsupported fan, and
  if a device keeps reverting the scheduler **stands down after two tries** instead
  of flapping. When it stands down it now says so in your **Activity** feed.
- **Cleaner add-on logs.** A remote instance that's offline (e.g. a Cloudflare
  530 / error 1033) no longer floods the log with a wall of headers every five
  seconds; it's summarized to one line, then quieted until it changes or
  recovers, with a "recovered" line when it comes back. Log lines are now
  colour-coded by severity.

## 2.9.21
- New **Overrides** (holiday / special-event schedules). In the Schedules panel's
  **Overrides** tab you can create a schedule that runs only on a date (or a
  From/To range) and, while active, takes over from your weekly schedules for the
  thermostats you include. Its events apply every day in the range, so a single
  event is an all-day hold and several events vary the temperature across the day.
  When the dates pass, those thermostats go back to their weekly program. Turn on
  an override mid-day and the event in effect right now applies immediately.
- In the admin Schedules view, overrides are clearly marked: each is badged with
  its active dates under **By user**, and listed in their own section (apart from
  the weekly program) under **By thermostat**.

## 2.9.20
- Switching between **comfortable and compact view** now animates: the cards and
  their controls springily resize instead of snapping.
- **Expanding and collapsing** now animates smoothly, both ways. Dashboard
  sections (and areas within a floor) slide open and closed, and so does the
  **Edit devices** panel on the Lists page. Moving between the dashboard, Lists,
  Organize and Schedules springs in too.
- Fixed the **device search on the Lists page** appearing *behind* the lists
  below it while adding devices.
- Fixed the **area/floor picker in the edit dialogs** getting cut off and hidden
  behind a scroll; it now shows in full over the dialog.

## 2.9.19
- Fixed a real problem with **changing your password**: entering the wrong
  current password used to silently log you out instead of telling you it was
  wrong. Now it stays put, shakes, and shows "Current password is incorrect.",
  and only a correct current password goes through.
- A **wrong password on the login screen** now shakes and gives the same firmer
  buzz, instead of just quietly showing the message.
- **Dropdowns** now clearly grow open and shrink closed out of the button they
  belong to, and their little arrow flips up while the menu is open, so opening a
  picker is something you can actually see rather than a tiny static arrow.

## 2.9.18
- More of that springy feel throughout. Menus, dialogs and the schedule editor
  now animate **closed** too (not just open), so dismissing them feels as smooth
  as opening them. On/off switches give their knob a little bounce as it slides,
  and the theme and view buttons in the header pop when tapped.
- A wrong **password change** now pushes back: the dialog shakes and the phone
  gives a firmer, longer buzz, so a rejected attempt is unmistakable. (All of it
  still honours your device's "reduce motion" setting.)

## 2.9.17
- Menus, dialogs and pop-ups now spring open with the same jelly-like motion as
  the list drag, instead of just appearing. The account menu and search dropdowns
  pop out from their edge, dialogs zoom in with a gentle bounce, and the schedule
  editor slides up from the bottom, each with a light haptic tap as it opens. It
  all honours your device's "reduce motion" setting, in which case things simply
  appear as before.

## 2.9.16
- You can now arrange your lists in any order. Open Lists from the account menu
  and drag the handle on each one to reposition it (works with a mouse or by
  touch); the order you set is the order the filter chips appear in on the
  dashboard. (Lists are no longer forced into alphabetical order.) The row you
  grab lifts and trails your finger with a springy, jelly-like weight, leaning
  and stretching as it moves while the others glide out of the way, then it
  settles into place when you let go, with a light haptic tap on pickup, each
  time it crosses another row, and on drop.

## 2.9.15
- The schedule editor and week preview now start the week on Sunday (Sun through
  Sat) instead of Monday. This is purely a display change: the days each schedule
  runs on are stored the same way as before, so your existing schedules keep
  firing on exactly the same days, no matter which version created them.

## 2.9.14
- Schedules now stand down when a person takes over. If someone changes a
  thermostat (in Home Assistant directly, or through Control Center) after a
  schedule fired, the schedule stops re-applying to that device until its next
  event, so it won't fight you. A change with no person behind it (a device
  quietly bouncing back on its own) is still treated as a miss and re-applied.
- The add-on log now shows detailed climate-scheduling diagnostics, so you can
  see exactly what the scheduler is doing and why. Every fire is logged with the
  device's state before and after and how long the command took; failures, offline
  devices, drift re-applies, and disabled schedules are all called out; and the
  log warns when the loop skipped one or more minutes (for example because a slow
  command blocked it or the add-on was down), which means events in that window
  did not fire. Thermostat mode and setpoint changes are logged too, so a change
  that didn't take (or bounced back) is visible. Lines are tagged `[sched]` and
  `[device]` for easy filtering.

## 2.9.13
- Users, schedules, and lists now show in alphabetical order wherever they're
  listed (the admin Users tab, the schedule switcher and admin Schedules view,
  the History user filter, and the dashboard list chips), so they're easier to
  find as they grow.

## 2.9.12
- Backups now include your climate schedules, the per-user scheduling
  permissions, and per-user device lists. Before, Export and Restore only covered
  users, settings, and the activity log, so a reinstall silently lost all of your
  schedules. Restoring a backup made by an older version leaves the current
  schedules and lists untouched rather than clearing them.

## 2.9.11
- The device and user filters in the History tab, the floor pickers in Organize
  (Areas & Floors), and the area picker when editing a device are now searchable
  dropdowns (the same kind the scheduler uses). When the list is long, just start
  typing to narrow it down.

## 2.9.10
- Schedules now double-check themselves. Some thermostats (Honeywell is a common
  culprit) don't always accept a change the first time. They look like they took
  it, then quietly snap back. So for about 10 minutes after a schedule runs,
  Control Center keeps an eye on the thermostat and sends the setting again if it
  didn't stick. That short window is deliberate: it lets a schedule fix itself
  without stepping on a change you make by hand later on.

## 2.9.9
- Fix: after you tapped a control, the card briefly ignored incoming states to
  avoid a stale reading bouncing it back. That hold could also hide a genuine
  quick change right after - most visibly a lock that auto-relocks moments after
  you unlock it. The hold now releases the instant Home Assistant confirms the
  state you set, so a real follow-up change shows immediately (applies to locks,
  power, and climate fan/swing).
- Lock (and other mode) buttons no longer tint their text with the card's accent
  colour, which clashed - especially in dark mode, where a selected button could
  show amber-on-amber. The active button now keeps neutral, readable text.

## 2.9.8
- Schedule times now display in your locale's clock format (12-hour with AM/PM or
  24-hour) instead of always showing 24-hour, so they follow Home Assistant's
  default. Applies to the user and admin schedule views.
- In the admin Schedules "By user" view, turning on **All climate** now collapses
  the per-device grid to a short summary, the same as the user editor.

## 2.9.7
- Fix: the device search when adding devices (to a list, a schedule's
  permissions, or a user's extras) showed at most 8 matches. It now shows every
  match and the results scroll, so a broad search no longer hides devices.

## 2.9.6
- Redesigned the admin **Schedules** view. Instead of every user's permission
  grid and every schedule stacked on one page, it now pivots **By user** or **By
  thermostat** and uses a searchable selector to focus on one at a time.
  - By user: their scheduling access (including the All-climate toggle) and their
    schedules, with enable/disable and delete.
  - By thermostat: which users may schedule it, plus its effective program merged
    across all users on a week strip, with conflict warnings for overlapping
    events.

## 2.9.5
- New: **Allow all climate devices (current & future)** toggle for climate
  scheduling. Instead of ticking each thermostat, an admin can grant a user
  scheduling for every climate device they can control, and any device added
  later is included automatically. Available both in the user editor and in the
  admin Schedules > Access grid. It never exceeds the devices the user is allowed
  to control.

## 2.9.4
- Fix: editing a device (the pencil button on a device card) that lives on a
  remote instance failed with "That device isn't available to manage." The
  device's id wasn't being tagged with its instance on the dashboard, so the
  edit was sent to the main instance instead of the remote one. It now routes to
  the correct instance.
- Managing devices and areas is also more resilient to a remote being briefly
  slow or unreachable: the manager reads registry data from the same warm cache
  the dashboard uses and no longer rejects a legitimate edit when a remote has a
  momentary hiccup.

## 2.9.3
- Fix: the dashboard now reconnects on its own after the live connection drops.
  Some reverse proxies (e.g. Cloudflare) close an idle connection silently, and
  the browser never noticed, so live updates would stop and a manual page
  refresh was the only way back. The app now watches the connection's heartbeat
  and reconnects itself when it goes quiet. "Retry now" still forces a full
  refresh.

## 2.9.2
- Sessions now last 30 days (up from 7) and are rolling: while you use the app,
  your session is quietly refreshed in the background, so regular use keeps you
  signed in. Only a session left unused for the full 30 days asks you to log in
  again. This addresses having to log in again every week.

## 2.9.1
- Lists refinements: the list chips now sit on a single row that scrolls sideways
  instead of wrapping onto a second line, and a list is now an alternative to
  Type/Area/Floor - picking a list is the active view, and picking Type (or
  Area/Floor) goes back to all your devices. The separate "All" chip is gone.
- Fix: selecting a list with no devices now shows "<list> has no devices yet"
  instead of the confusing "No devices match \"\"".

## 2.9.0
- New: **Lists**. Create your own named lists of devices from the account menu
  (top-right avatar), add any of your devices to them, and each list appears as a
  chip in the dashboard's sort row next to Type/Area/Floor. Tap a list to show
  only its devices; tap it again (or "All") to clear. Make as many lists as you
  like - handy when you have a lot of devices or want to organise them your own
  way. Lists are per-user: each person manages their own.

## 2.8.9
- The Schedules page now has a **Done** button in the top bar (like Organize) and
  no longer shows the compact-view button, which did nothing there. The old
  in-page Back button is removed, so leaving Organize or Schedules works the same
  way from the same place.
- The "Connection lost" toast's **Retry now** button now forces a full page
  refresh instead of only reconnecting in the background, so a stuck dashboard
  reloads cleanly.

## 2.8.8
- Fix: switching between the dashboard, Organize, and Schedules from the account
  menu now always lands on the view you picked. Previously, opening Organize while
  on the Schedules page left you on Schedules (Organize loaded behind it), and the
  Organize menu item vanished while organizing but Schedules did not. The three
  views are now mutually exclusive and both menu items stay available, so you can
  jump straight between them.

## 2.8.7
- New: scheduled climate changes now appear in the activity log. A schedule acts
  on the thermostat in its owner's name, so each entry is attributed to that
  person and marked with a "Schedule" badge (and the schedule's name), making it
  clear the change came from a schedule rather than a manual tap.
- A scheduled change is logged only when it actually reaches an online
  thermostat. If the device is unavailable at the time the event fires, nothing
  is logged, so the feed never shows a change that did not really happen.

## 2.8.6
- Fix: when a session simply times out, the login screen now says "Your session
  has ended. Please log in again" instead of the account-expired message telling
  the user to contact the system administrator. The administrator message is now
  shown only for a genuinely expired account.
- Fix: after "Connection lost. Reconnecting...", the dashboard now reliably
  reconnects on its own, and the "Retry now" button works. Reconnection is now
  single-flight: only one live connection exists at a time and a superseded one
  is fully torn down, so repeated drops or retries can no longer pile up leaked
  connections (which held server worker threads and stalled both auto-refresh
  and manual retry).

## 2.8.5
- Fix: controlling a remote-instance device now works when the remote instance
  `id` contains a hyphen, dot, uppercase letter, or other character (e.g. an id
  like `ha-2`). Entity-id validation previously accepted only `[a-z0-9_]` in the
  instance part, so every command to such a remote was silently rejected with a
  400 while the device list still showed fine. Validation now checks the real
  entity id strictly and accepts the instance prefix if it is a configured remote
  instance.
- Fix: API errors are now written to the add-on log (method, path, status, and
  reason). Rejected requests were previously invisible in the log, which made a
  failing command impossible to diagnose.
- A failed control action now shows a brief error message in the dashboard
  instead of silently reverting.

## 2.8.4
- Fix: a remote instance with little activity (e.g. a quiet secondary home) no
  longer reconnects every ~30 seconds. The read connection now sends a keepalive
  ping when it goes idle and only reconnects if the ping goes unanswered, instead
  of treating a quiet-but-healthy link as a dropped connection.
- Fix: remote commands now use the same 30-second connection timeout as the read
  stream. A shorter timeout could silently drop a command while a fresh
  connection was still completing its handshake over a slow (e.g. Cloudflare)
  link.
- Remote commands are now logged (what was sent, to which instance, and any
  failure reason) so a command that does not reach the remote HA leaves a clear
  trace in the add-on log instead of failing silently.

## 2.8.3
- Fix: changes made in Control Center to a remote-instance device (lights,
  climate, etc.) now actually reach the remote Home Assistant. Commands to
  remote instances are sent over the same WebSocket used for reading state,
  which carries the browser headers that get past Cloudflare bot protection.
  Previously commands went over a REST call that Cloudflare could block with a
  403 while the read stream kept working, so remote changes silently failed.
- Fix: scheduled climate events now fire against the correct instance. A
  schedule targeting a remote-instance thermostat was being sent to the main
  Home Assistant with an unknown entity id and silently did nothing; the target
  is now routed to its owning instance.
- Fix: a climate card's target temperature now stays in sync when it changes
  from anywhere other than that card (the scheduler, another user, or the HA
  instance itself). It previously froze at the value shown when the card first
  loaded, so current and target temperature could drift out of sync (worst for
  remote devices, which could stick at a default of 22).

## 2.8.2
- RGB lights now glow in their actual current color on the dashboard card.
  Color is read from HA's rgb_color attribute, with hs_color as fallback.
  Near-black or near-white colors fall back to the warm yellow glow.
- Device card name now truncates with ellipsis instead of pushing the edit
  pencil outside the card on long names.

## 2.8.1
- Fix scheduling permission grid: long device names now wrap instead of being
  truncated. Grid collapses from 2 columns to 1 automatically when the panel is
  narrow or names are long. Lone last item still spans the full width.

## 2.8.0
- Scheduler UI rewrite: searchable dropdowns (keyboard navigation: arrows, Enter,
  Escape) replace plain selects for the schedule switcher and AC picker.
  Enable/disable is now a sliding toggle (button[role=switch]) instead of a
  checkbox. Target ACs appear as removable chips; a multi-select searchable menu
  adds more. Bottom sheet editor slides up with animation and is fully
  mobile-friendly with safe-area insets.
- Week strip now shows hour-axis tick labels (0, 6, 12, 18, 24).
- Entry rows show a 26 px colored circle with a mode abbreviation, day letters
  at proportional opacity, and the source schedule name in the By-thermostat
  view. Conflict warning icon appears when two events share the same time slot.
- Admin Schedules tab: "All schedules" is now grouped by user with collapsible
  cards (chevron + status dot + toggle + delete). "Access" is a 2-column grid
  of clickable device buttons per user (replacing the old table) - enabled
  devices show a checkmark, disabled ones show a lock icon.
- UserEditor scheduling permissions now render as the same 2-column grid,
  consistent with the admin Access tab.
- Temperature unit support: slider range, step, and unit label (C or F) are
  read from the HA climate entity attributes (min_temp, max_temp,
  target_temp_step, temperature_unit). Falls back to inferring C vs. F from the
  range when the attribute is absent. Stored values are passed through to HA
  as-is so HA always receives its configured unit.

## 2.7.0
- Feature: per-user climate scheduling. Users can create named schedules that
  fire edge-triggered events (time + day-of-week) against their permitted
  thermostats: set mode (off/cool/heat/auto/dry/fan), temperature (C), and fan
  speed. A week preview strip visualizes the program at a glance. A "By
  thermostat" view merges all active schedules for one entity and flags
  same-time conflicts.
- Admin: new "Schedules" tab lists all schedules across users with
  enable/disable and delete controls. User editor now includes a "Climate
  scheduling" section to grant per-entity schedule permissions.
- Backend: schedules persisted in schedules.json and schedule_perms.json under
  /data; a background daemon thread fires HA climate services at the scheduled
  times (checks every 30 seconds, fires at most once per HH:MM).

## 2.6.13
- Fix: an expired or invalid session now silently redirects to the login screen
  instead of showing a "Failed to fetch" or "Session expired" error banner. The
  login page shows a "session expired" notice. No error state is shown on the
  dashboard.

## 2.6.12
- Fix: instance badge now appears on each area row in Organize - Areas & Floors
  (not on Devices tab section headers). Main-instance areas show no badge;
  remote-instance areas show a badge with the configured instance name.

## 2.6.11
- Manager Organize view now shows a small instance badge on each area section
  header when that area contains devices from a remote HA instance. Areas with
  only main-instance devices show no badge.

## 2.6.10
- (reverted - badges were incorrectly placed on the user dashboard)

## 2.6.9
- Fix: `_inst_url` was called in `_ws_loop` to build the WebSocket `Origin`
  header for remote instances but was missing from the `ha` import list, causing
  a `NameError` at startup when any remote instance is configured.

## 2.6.8
- Fix: remote instance state cache (entities on the dashboard) was still empty
  after 2.6.7 because the loop waited for all 7 WebSocket responses before
  writing anything to the cache. State is now seeded immediately on `get_states`
  result; registry data (areas, floors, devices) is collected lazily in the
  background event loop and written to cache as it arrives, without blocking
  the state snapshot.

## 2.6.7
- Fix: remote instance area and floor assignments now populate correctly on the
  dashboard. Floors, areas, entity and device registries are now fetched through
  the already-open persistent WebSocket at connect time, so no separate
  connections to the remote HA are needed - the data is cached immediately and
  remote devices appear in their correct rooms without any additional Cloudflare
  handshakes.

## 2.6.6
- Fix: remote instance state cache was empty after connect when Cloudflare (or
  a proxy) blocked the separate REST call to `/api/states`. The initial state
  snapshot is now fetched via the already-authenticated WebSocket (`get_states`
  command) so no additional HTTP request is needed - the entire real-time stream
  runs over a single WebSocket connection.

## 2.6.5
- Fix: remote instance connections to HA servers behind Cloudflare now send
  browser-like headers (`User-Agent`, `Origin`) so Cloudflare's basic bot
  protection does not block the WebSocket handshake. If Cloudflare is still
  blocking after this (e.g. Under Attack mode), use the direct local IP/port
  instead of the Cloudflare-proxied domain.
- Improvement: a Cloudflare 403 is now detected and logged with a specific
  actionable message rather than a raw error dump.

## 2.6.4
- The **Settings** tab now shows a **Remote instances** card when remote
  instances are configured. It displays each instance with a green/red
  connection indicator, its URL, how many entities are cached from it, and any
  error message if unreachable. A **Recheck** button lets you test connectivity
  on demand without reading the add-on log.

## 2.6.3
- Diagnostics: the add-on now logs at startup which remote instances were loaded
  and which URL each one is connecting to, making misconfiguration visible in the
  add-on log immediately. A new admin endpoint (`/api/admin/remote-status`) lets
  you check whether each remote is reachable and how many entities are cached from
  it - useful for verifying a remote instance is connected without reading logs.

## 2.6.2
- Docs: all four `remote_instances` fields (`id`, `name`, `url`, `token`) are
  now clearly marked as required in the documentation. Fixed a typo in the docs.

## 2.6.1
- Fix: `remote_instances` add-on schema now uses `name: str` instead of
  `name: str?` to avoid rejection by the HA add-on schema validator. The
  default options entry now shows all four fields so the expected format is
  visible in the add-on configuration UI.

## 2.6.0
- New: **remote instance support**. Connect additional Home Assistant instances to Control Center. Devices from every instance are pooled into a single view: they appear in the entity picker (tagged with a colorred instance badge), can be assigned to users exactly like local devices, and are controlled directly from the dashboard - user management stays in one place.
- The manager **Organize** tab now covers all instances: devices from remote HAs appear alongside local ones (with an instance badge), and rename/area changes write through to the correct HA registry automatically. The area dropdown in the device editor shows only areas from that device's own instance, and the new-area dialog lets the manager pick which instance to create it on when multiple are configured.
- Add remote instances in the add-on configuration via `remote_instances` (see docs).

## 2.5.3
- Hardening: the API is now **default-deny**. Every `/api/*` endpoint requires a valid session (or the trusted management port) unless it is explicitly public: sign-in, the OAuth handshake, the branding/session probe, and the version check. This is enforced centrally, so a newly added endpoint can't accidentally ship unauthenticated. (Internally, the backend was also reorganized into smaller modules; no behavior change.)

## 2.5.2
- The Manage users list now shows an **account-expiry badge** on each user: amber "Expires <date>" when a future expiry is set, and red "Expired <date>" once it has passed, so you can see at a glance who is time-limited without opening the editor.

## 2.5.1
- OAuth sign-in failures (including an expired account) now render on the **login page**, themed and consistent with password sign-in, instead of a separate plain "Sign-in failed" page. This also removes the old "Back to sign in" link that pointed at the wrong place (`/api/oauth/`).

## 2.5.0
- New: **account and device expiry** (both optional). In Manage users, an admin can set an **account expiration date**; the user works through that whole day and is blocked the next, shown a clear "your account has expired, contact the system administrator" prompt at sign-in. An already-signed-in session is cut off immediately. Each of a user's assigned devices can also be given its own expiry date, after which that device quietly disappears from their dashboard. Leave a date blank for no expiry.
- The Settings "Session security" card now only appears when there's something to do, i.e. when the session secret is auto-managed and can be regenerated. When the secret is pinned via the add-on's `jwt_secret`, the card is hidden instead of showing a dead-end note pointing you to the configuration.

## 2.4.8
- Fix: an **uploaded logo now actually shows in the browser tab**. The page ships an SVG favicon plus a PNG fallback, but only the SVG link was being updated, so browsers that prefer the PNG kept showing the default icon. All favicon links now point at the uploaded logo.

## 2.4.7
- An **uploaded logo** now also shows as the glyph beside the title (header, login, and install prompt), matching the browser tab and PWA icon, instead of only the emoji or default mark. Precedence is: uploaded logo first, then a Settings emoji, then the built-in logo.

## 2.4.6
- A climate device in **heat/cool** mode now shows a heat+cool blended (purple) glow when Home Assistant doesn't report what it's actively doing, which is common for this mode. When the live HVAC action is reported it still goes red while heating and blue while cooling.

## 2.4.5
- Every active device card now **pulses** its colored glow, not just heating climates and playing media, so an "on" or active state reads consistently across lights, switches, fans, covers, locks, climate, and the rest.

## 2.4.4
- A climate device in **heat/cool** mode now colors its card by what it's actually doing right now (the live HVAC action) instead of always showing red: red while heating, blue while cooling, and green when it's on but idle. Single-mode heat, cool, dry, and fan-only are unchanged.

## 2.4.3
- The admin **password rules** editor (Settings → Sign-in methods) is now themed to match the app: a boxed panel with properly styled number inputs and accent-colored checkboxes, instead of bare browser controls that looked out of place (especially on dark mode).

## 2.4.2
- The theme toggle is now two-state: it switches between **Auto** (follow your device) and the **opposite** of your device's current appearance, then back to Auto. The icon reflects this - a half-moon for Auto, and a sun or moon for the manual light/dark override.

## 2.4.1
- The password requirements in the Change password dialog now read as natural English (e.g. "an uppercase letter, a number, and a special character") and appear in a styled hint box that matches the app.

## 2.4.0
- Local users can now **change their own password** from the account menu (it verifies the current password first, and is rate-limited). Admins set the **password rules** under **Settings → Sign-in methods**: minimum/maximum length and whether an uppercase letter, lowercase letter, number, or special character is required.

## 2.3.0
- The dashboard header now has an **account menu**. Your avatar (your OAuth profile picture, or your initials on a per-user color) opens a dropdown with **Organize** (managers only) and **Log out**, tidying up the header. The organizer keeps its own **Done** button.

## 2.2.5
- The manager edit pencil on dashboard device cards is now shown in a bordered button (a box), matching the area organizer and the other icon buttons, instead of a borderless glyph.

## 2.2.4
- The manager edit pencil (on dashboard device cards and in the area organizer) now uses a crisp **outline pencil icon** instead of the plain text glyph, so it renders consistently across devices.

## 2.2.3
- Hardening: the integration brand-icon proxy (`/api/icon/brand/...`) now requires a valid session on the published dashboard port, so the set of installed integrations can't be enumerated by an unauthenticated visitor. The token rides as a query parameter (an `<img>` can't send a header), matching how the live WebSocket is authenticated; the Ingress/management port stays trusted by port.

## 2.2.2
- Integration badges now show real logos via Home Assistant's brands proxy (2026.3+), so a custom integration that ships its own logo, not just core integrations, displays correctly; older Home Assistant falls back to the brands CDN. When an integration genuinely has no logo, the badge shows a neutral puzzle-piece glyph instead of Home Assistant's "icon not available" placeholder.

## 2.2.1
- Integration badges now use Home Assistant's **real integration name** (from its manifest), so acronyms and special casing stay correct instead of being naively derived from the domain.
- Badges now also show logos for **some custom / HACS integrations** (those that publish brand art): the badge tries the core brand path, then the custom-integration path, and falls back to text if neither has an image.

## 2.2.0
- The manager's **device organizer** now shows each device's **integration** as a small brand badge (logo + name, e.g. Shelly, MQTT, TP-Link), pulled from Home Assistant's brand icons and falling back to just the name when a logo isn't available.
- In the organizer, **tapping anywhere on a device card** now opens its edit dialog (the separate pencil is gone) for a bigger, easier target, especially on mobile. A hint at the top makes it clear.

## 2.1.1
More security hardening from a follow-up audit. All server-side, no UI changes, automatic on update.

- **Login throttle now keys on the username**, not the client IP. The old per-IP key trusted the `X-Forwarded-For` header, which is spoofable (a rotating value bypassed the limit) and is shared behind a proxy/tunnel (which could lock everyone out at once). The counter is also memory-bounded.
- **Login is constant-time**: an unknown username now runs the same hash check as a real one, so response timing no longer reveals which usernames exist.
- **Device IDs and time ranges are validated** before any Home Assistant API call, closing an authenticated path-traversal into HA's API available to `all`/`manager` users.
- **Generic upstream errors**: Home Assistant's raw error text is no longer reflected to clients (it's logged server-side instead).
- **Security headers**: `X-Content-Type-Options: nosniff` on every response; set `block_iframe_embedding: true` to also send `X-Frame-Options: DENY` on the user dashboard (anti-clickjacking). It's off by default so it never breaks a `panel_iframe` embed, and it's never applied to the Ingress/management UI.
- **Standalone hardening**: the no-login management port now binds to `127.0.0.1` by default (`INGRESS_BIND=0.0.0.0` to override). The add-on is unaffected (it stays behind Ingress).

## 2.1.0
Security hardening (recommended before exposing the dashboard to the internet). Everything below is automatic on update - no data loss, and no one is locked out.

- **Passwords are now hashed** (PBKDF2-HMAC-SHA256 with a per-password salt) instead of stored in plain text. Existing passwords are upgraded to a hash automatically on update and on first login, and old plaintext backups are hashed on import. Exports now contain hashes, not plaintext.
- **The session secret is auto-generated.** When `jwt_secret` is left blank (now the default), a random secret is generated and persisted to `/data` on first run, so sessions are never signed with the guessable default that used to ship. You can still pin your own via the add-on config or `JWT_SECRET`, and rotate the managed one from **Settings → Session security** (this signs everyone out).
- **OAuth fails closed.** If OAuth is enabled but no `oauth_allowed_domains` / `oauth_allowed_emails` are set, sign-in is now **refused** (with a warning in Settings) instead of allowing any Google account. Set the new `oauth_allow_any: true` to deliberately allow anyone with a verified email. For personal Gmail, list the addresses in `oauth_allowed_emails`.
- **OAuth requires a verified email**: providers that don't return `email_verified: true` are rejected (previously only an explicit `false` was blocked).
- **OAuth sign-in is bound to the browser that started it** (the `state` is tied to a short-lived cookie), preventing login-CSRF.
- **Login is rate-limited** per username + IP to slow brute-force attempts, with constant-time password checks.

## 2.0.2
- The theme button now **toggles** instead of cycling through three states. From **System**, the first tap flips to the opposite of whatever's currently shown (System-light → Dark, System-dark → Light); after that it just switches **Light ↔ Dark**. (It no longer cycles back to System from the button.)

## 2.0.1
- Fix: the exported backup file is now named **`control-center-backup.json`** (was the old `my-home-backup.json`). Existing backup files still restore regardless of their name.
- Fix: the default **logo** beside the title now scales with the heading, so it no longer looks too small on the larger login-page title. (Emoji header icons were already correct.)

## 2.0.0
**Rebrand: "My Home" is now Control Center.** A new name, a new logo, and a blue theme throughout - no change to how the add-on works or to your existing setup.

- **New name everywhere it's shown.** The add-on title, browser tab, Home Assistant sidebar panel, login screen, dashboard header, install prompt, and the installed PWA all now read **Control Center**.
- **New logo.** A custom mark (two bars, a divider with an indicator dot, and a dial arc) on a deep-navy background is now the default icon - used for the header, browser favicon, PWA/home-screen icon, maskable icon, and Apple touch icon. The icon art is rendered from a single source SVG so every size stays crisp, and the background is full-bleed so it looks right whether or not the platform crops it to a rounded shape.
- **The header shows the logo by default.** Previously it showed a house emoji. The Settings → *Header icon* field still lets you set an emoji instead, and as before an **uploaded app icon overrides the default** everywhere. The upload/override mechanism is unchanged - only the built-in default art changed.
- **New blue theme.** Accent `#5B8DB8`, navy surfaces and PWA theme-color `#1C3A5E`, and a `#7AB3E0` highlight, with backgrounds kept neutral. Both light and dark modes are re-themed.

**Upgrading from 1.x - what to expect**
- **All your data is preserved.** Users, passwords, device assignments, settings, and the activity log carry over untouched. The add-on's identity (its slug) is unchanged, so Home Assistant maps the same `/data` to the renamed add-on - there is nothing to migrate.
- **Old backups still restore.** Backup files exported by 1.x versions are still accepted by the import in this version.
- **Refresh once.** The app auto-reloads to pick up the new build; if you don't see the new branding immediately, refresh the page.
- **PWA icon on phones:** iOS (and some Android launchers) freeze a home-screen icon at install time. To get the new logo there, remove the app from your home screen and re-add it. The in-browser favicon and in-app logo update on their own.

## 1.39.0
- Added a **"Connection lost. Reconnecting…"** toast on the dashboard when the live connection to the add-on drops, with a **Retry now** button. It clears itself once the connection is back, and only appears after a brief grace so quick blips (or the add-on restarting during an update) don't flash it.

## 1.38.0
- The app now **auto-reloads when a new version is deployed** - no manual refresh. It checks the server's version on a timer and whenever you return to the app, and reloads once if it changed (the network-first service worker then serves the fresh code). The live WebSocket only carries device state; picking up new app *code* needs a reload, which is now automatic.

## 1.37.11
- Fix: taps that disable their own button - like the **climate mode / fan / swing** buttons - now give haptic feedback too. The tick is now captured before the button disables itself (previously it was skipped because the button was already disabled by the time the haptic ran).

## 1.37.10
- Fix: un-checking **Manager** no longer leaves **All devices** stuck on. Managers get full device access from the role itself, so the "All devices" flag is now independent - turning off Manager reveals the real all-devices choice (and we no longer store a redundant `all` on managers).

## 1.37.9
- Removed the temporary `?haptictest=1` haptics diagnostic page now that iOS haptics work.

## 1.37.8
- **iOS haptics now fire on taps.** The tick is triggered on `click` instead of a passive `pointerdown` - iOS only plays the switch haptic from inside a real click. Buttons, toggles, climate modes, tabs etc. now buzz on both iOS and Android. (Sliders still tick per step on Android only; iOS has no per-step click while dragging.)

## 1.37.7
- Added a temporary **haptics diagnostic page** (open the dashboard URL with `?haptictest=1`) to identify which iOS trigger actually fires on a device - a real-switch tap, a programmatic hidden/visible switch click, or the Vibration API. Used to pin down why iOS haptics aren't firing; not shown in normal use.

## 1.37.6
- Fix the version label showing **"vdev"**: `config.yaml` is now included in the add-on image, so the app can read its real version at runtime (the Dockerfile wasn't copying it).

## 1.37.5
- The running build version is now shown as a small label in the bottom corner of every screen, so you can confirm which version is live at a glance (handy on a phone, where checking page source isn't).

## 1.37.4
- The app now loads **fresh every time**: the service worker is **network-first** (cached copies are used only when offline), so a new build is never served stale - no more cache-version juggling to see updates.
- The running build is stamped into the page automatically from the add-on version - visible as `?v=` on the CSS/JS and an `app-version` meta tag - so you can always tell which version is live.

## 1.37.3
- iOS haptics fix: match the reference web-haptics element setup exactly (the hidden switch now uses `all:initial` + `appearance:auto` and `display:none`, not `opacity`/`pointer-events:none`, which were suppressing the toggle). This is what made it not fire on iOS even though the technique itself works there.

## 1.37.2
- More breathing room between the fan-speed slider and the "Auto" button below it.

## 1.37.1
- Sliders now theme reliably across browsers (drawn via the track element with a properly centered thumb, instead of a thin styled input that could render the thumb oddly), so the fan-speed, brightness and volume sliders all look consistent.
- More space between the fan-speed slider and the "Auto" button below it.

## 1.37.0
- Search bars (the dashboard search, the Organize device search, and the device filter in the user editor) now have a **clear (✕) button** that appears once you've typed, to wipe the search in one tap.

## 1.36.1
- iOS haptics fix: the hidden switch element is now mounted in the page body and kept in the DOM (it was wrongly placed in `<head>` and removed right after clicking, which prevented the haptic from firing), matching the reference web-haptics implementation.

## 1.36.0
- Tap haptics now also fire on **iOS**, not just Android. iOS Safari has no Vibration API, so on iOS we use the hidden `<input type="checkbox" switch>` technique (toggling it makes the system play its switch haptic); Android keeps using the Vibration API. Pure browser code - no libraries or build step. (Note: Apple reportedly closed the programmatic-trigger path in iOS 26.5, so it may not fire on the very latest iOS.)

## 1.35.2
- Climate fan slider now also recognises **numeric** speed lists (e.g. 0-6, or 1-3), not just named ones - it auto-detects whichever scheme the unit uses (numbers or low/med/high) and renders the slider when there are 3+ steps. Non-speed modes like "auto" stay as buttons.

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
