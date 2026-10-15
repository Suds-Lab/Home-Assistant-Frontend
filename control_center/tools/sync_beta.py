#!/usr/bin/env python3
"""Regenerate the `control_center_beta/` add-on folder from `control_center/`.

The beta add-on is NOT a hand-maintained fork: `control_center/` is the single
source of truth, and this mirrors every tracked source file into
`control_center_beta/`, then overrides only the handful of `config.yaml` identity
fields that must differ so the two add-ons can be installed side-by-side on one
machine:

  slug         ha_frontend        -> control_center_beta   (separate /data)
  name         Control Center     -> Control Center (Beta)
  panel_title  Control Center     -> Control Center (Beta)
  panel_icon   mdi:tune-vertical  -> mdi:flask-outline      (distinct sidebar)
  host port    8099               -> 8098                   (container stays 8099)

Usage:
  python control_center/tools/sync_beta.py [--version X.Y.Z]

--version sets the beta's config.yaml version independently of stable (the beta
channel is advanced on its own cadence). Without it, the mirrored stable version
is kept. Run this whenever stable source changes so the beta never drifts.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]           # control_center/tools/sync_beta.py -> repo root
SRC = REPO / "control_center"
DST = REPO / "control_center_beta"
# dev-only trees that are not part of the add-on runtime
EXCLUDE = ("control_center/tools/", "control_center/tests/")

# (exact old -> new). Each must appear exactly once in config.yaml, or we bail -
# that guards against silent drift if the stable config changes shape.
OVERRIDES = [
    ("slug: ha_frontend", "slug: control_center_beta"),
    ("name: Control Center", "name: Control Center (Beta)"),
    ("panel_title: Control Center", "panel_title: Control Center (Beta)"),
    ("panel_icon: mdi:tune-vertical", "panel_icon: mdi:flask-outline"),
    ("  8099/tcp: 8099", "  8099/tcp: 8098"),
]


def main(argv):
    version = None
    if "--version" in argv:
        version = argv[argv.index("--version") + 1]

    tracked = subprocess.check_output(
        ["git", "ls-files", "control_center/"], cwd=REPO, text=True
    ).splitlines()
    files = [f for f in tracked if f and not f.startswith(EXCLUDE)]
    if not any(f.endswith("config.yaml") for f in files):
        print("ERROR: control_center/config.yaml not tracked; nothing to sync")
        return 1

    if DST.exists():
        shutil.rmtree(DST)
    for rel in files:
        dstf = DST / Path(rel).relative_to("control_center")
        dstf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dstf)

    cfg = (DST / "config.yaml").read_text()
    for old, new in OVERRIDES:
        n = cfg.count(old)
        if n != 1:
            print(f"ERROR: expected exactly one '{old}' in config.yaml, found {n}. "
                  "Stable config changed shape; update OVERRIDES in sync_beta.py.")
            return 1
        cfg = cfg.replace(old, new)
    if version:
        cfg = re.sub(r'version: "[^"]*"', f'version: "{version}"', cfg, count=1)
    (DST / "config.yaml").write_text(cfg)

    ver = re.search(r'version: "([^"]*)"', cfg).group(1)
    print(f"regenerated control_center_beta/ from control_center/ "
          f"({len(files)} files; slug control_center_beta, port 8098, version {ver})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
