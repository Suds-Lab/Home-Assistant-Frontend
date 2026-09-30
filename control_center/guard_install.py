"""Install the bundled Restart Guard integration into Home Assistant.

Control Center ships a copy of the Restart Guard custom integration (a fork of
igraph100/ha-restart-guard, MIT) under ``bundled_guard/``. On start-up we copy
it into Home Assistant's ``custom_components/`` so a restart-time warning is
available without the user hunting down and installing it by hand.

Deliberately conservative:
  * It only writes when Home Assistant's config directory is actually mounted
    (the add-on needs ``homeassistant_config:rw``); with no config dir it does
    nothing, so the dev server and a misconfigured add-on both stay inert.
  * It never touches a copy it did not install. A ``.cc_managed`` marker marks
    ours; a copy without it (installed by hand or through HACS) is left alone,
    so Control Center never fights another source of the same integration.
  * A custom integration only loads on a Home Assistant restart, so after a
    fresh install or an upgrade it posts a persistent notification asking for
    one rather than restarting Home Assistant itself.
  * Every failure is caught and logged. Nothing here can keep the add-on from
    starting.
"""

from __future__ import annotations

import json
import os
import shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "bundled_guard", "restart_guard")

# Written into our copy so a later start knows the copy is ours and how old it
# is. A dotfile, so Home Assistant's component loader ignores it.
_MARKER_NAME = ".cc_managed"
_NOTIFY_ID = "control_center_restart_guard"


def _log(msg: str) -> None:
    print(f"[restart-guard bundle] {msg}", flush=True)


def _ver_tuple(ver: str) -> tuple:
    """"0.0.9" -> (0, 0, 9). Non-numeric parts sort as -1 so a weird version is
    treated as older than any real one (we would rather re-copy than skip)."""
    out = []
    for part in str(ver or "").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(-1)
    return tuple(out)


def _read_version(manifest_path: str) -> str:
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "")
    except Exception:  # noqa: BLE001
        return ""


def _marker_version(marker_path: str) -> str:
    try:
        with open(marker_path, encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "")
    except Exception:  # noqa: BLE001
        return ""


def _ha_config_dir() -> str | None:
    """Home Assistant's own config directory, or None if it isn't mounted.

    A test/dev run can point us at a scratch dir with GUARD_CONFIG_DIR. In the
    add-on the supervisor mounts the config dir at ``/homeassistant`` (newer) or
    ``/config`` (older); we pick whichever actually looks like an HA config root
    so we never write custom_components into the add-on's own /config by mistake.
    """
    override = os.environ.get("GUARD_CONFIG_DIR")
    if override:
        return override if os.path.isdir(override) else None
    for cand in ("/homeassistant", "/config"):
        if not os.path.isdir(cand):
            continue
        looks_like_ha = os.path.exists(
            os.path.join(cand, "configuration.yaml")
        ) or os.path.isdir(os.path.join(cand, ".storage"))
        if looks_like_ha:
            return cand
    return None


def _copy_fresh(src: str, dst: str) -> None:
    """Replace dst with src wholesale, so an upgrade leaves nothing stale."""
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    # copy2 metadata, skip our own marker from any previous copy in src
    shutil.copytree(src, dst)


def _write_marker(marker_path: str, version: str) -> None:
    with open(marker_path, "w", encoding="utf-8") as fh:
        json.dump({"managed_by": "control_center", "version": version}, fh)


def _notify_restart(action: str, version: str) -> None:
    """Ask Home Assistant to show a 'please restart' notification. Best-effort:
    if HA is unreachable at boot the copy still happened and the next start will
    not re-notify (the versions now match), so a missed notification is benign."""
    if os.environ.get("MOCK_HA"):
        _log(f"(mock) would notify: Restart Guard {action} v{version}")
        return
    verb = "installed" if action == "install" else "updated to"
    message = (
        f"Control Center {verb} the **Restart Guard** integration "
        f"(v{version}).\n\n"
        "Restart Home Assistant (Settings > System > top-right menu > "
        "Restart Home Assistant) to enable it. Once it is running, Home "
        "Assistant's restart dialog will warn you before a restart lands on a "
        "Control Center schedule."
    )
    try:
        from ha import ha_request

        ha_request(
            "/api/services/persistent_notification/create",
            "POST",
            {
                "notification_id": _NOTIFY_ID,
                "title": "Restart Guard installed",
                "message": message,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"restart notification not sent ({exc}); copy still applied")


def install_bundled_guard() -> None:
    """Copy the bundled Restart Guard into HA if ours is missing or newer.

    Returns quietly on every path that shouldn't act (no config dir, an
    unmanaged copy present, already up to date). Never raises."""
    try:
        _install()
    except Exception as exc:  # noqa: BLE001 - must never break start-up
        _log(f"install skipped: {exc}")


def _install() -> None:
    if not os.path.isdir(_SRC):
        _log("nothing to install (bundled_guard/ missing)")
        return
    bundled_ver = _read_version(os.path.join(_SRC, "manifest.json"))

    cfg = _ha_config_dir()
    if not cfg:
        _log(
            "Home Assistant config dir not mounted; skipping "
            "(add-on needs the homeassistant_config:rw mapping)"
        )
        return

    components = os.path.join(cfg, "custom_components")
    dst = os.path.join(components, "restart_guard")
    marker = os.path.join(dst, _MARKER_NAME)

    if os.path.isdir(dst):
        if not os.path.exists(marker):
            _log(
                "an unmanaged Restart Guard copy is already installed "
                "(HACS or manual); leaving it untouched"
            )
            return
        installed_ver = _marker_version(marker)
        if _ver_tuple(installed_ver) >= _ver_tuple(bundled_ver):
            return  # our copy is current: nothing to do, and nothing to announce
        action = "upgrade"
    else:
        action = "install"

    os.makedirs(components, exist_ok=True)
    _copy_fresh(_SRC, dst)
    _write_marker(marker, bundled_ver)
    _log(f"{action}: Restart Guard v{bundled_ver} -> {dst}; awaiting HA restart")
    _notify_restart(action, bundled_ver)
