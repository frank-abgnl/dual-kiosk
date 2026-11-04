#!/usr/bin/env python3
"""
Screen Resolution and Display Utilities.
Communicates with the system via xrandr and sysfs DRM to discover connected monitors,
extract active resolutions, geometries, and refresh rates.
"""

import subprocess
import re
import glob
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("xinput_seating.screen_utils")


def run_command(args: List[str], timeout: int = 5) -> Tuple[str, str, int]:
    """Execute command safely and return (stdout, stderr, returncode)."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.stdout.strip(), proc.stderr.strip(), proc.returncode
    except Exception as exc:
        logger.error(f"Error running {' '.join(args)}: {exc}")
        return "", str(exc), -1


def detect_screen_resolutions() -> List[Dict]:
    """
    Queries xrandr and sysfs DRM to detect all connected displays with their exact
    resolutions, offsets, geometry strings, and primary flag.

    Returns:
        List of dicts:
        [
            {
                "name": "eDP-1",
                "connected": True,
                "primary": True,
                "width": 1920,
                "height": 1080,
                "x": 0,
                "y": 0,
                "geometry": "1920x1080+0+0",
                "refresh": 60.0,
                "active": True
            }, ...
        ]
    """
    displays = []
    out, err, rc = run_command(["xrandr", "--query"])

    if rc == 0 and out:
        for line in out.splitlines():
            if " connected" in line:
                parts = line.split()
                name = parts[0]
                is_primary = "primary" in parts

                # Match WxH+X+Y
                m_geom = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
                if m_geom:
                    w, h, x, y = [int(v) for v in m_geom.groups()]
                    displays.append({
                        "name": name,
                        "connected": True,
                        "primary": is_primary,
                        "width": w,
                        "height": h,
                        "x": x,
                        "y": y,
                        "geometry": f"{w}x{h}+{x}+{y}",
                        "active": True,
                    })
                else:
                    displays.append({
                        "name": name,
                        "connected": True,
                        "primary": is_primary,
                        "width": 0,
                        "height": 0,
                        "x": 0,
                        "y": 0,
                        "geometry": "inactive",
                        "active": False,
                    })

    # Fallback to sysfs DRM if xrandr gave no results
    if not displays:
        logger.info("xrandr returned no displays; reading sysfs DRM.")
        for p in sorted(glob.glob("/sys/class/drm/card*-*")):
            status_file = Path(p) / "status"
            try:
                status = status_file.read_text().strip()
            except Exception:
                status = "unknown"

            if status == "connected":
                conn_name = Path(p).name
                name_clean = re.sub(r"^card\d+-", "", conn_name)
                # Attempt to read modes if available
                modes_file = Path(p) / "modes"
                w, h = 1920, 1080
                if modes_file.exists():
                    try:
                        first_mode = modes_file.read_text().splitlines()[0]
                        mw, mh = first_mode.strip().split("x")
                        w, h = int(mw), int(mh)
                    except Exception:
                        pass

                displays.append({
                    "name": name_clean,
                    "connected": True,
                    "primary": len(displays) == 0,
                    "width": w,
                    "height": h,
                    "x": 0,
                    "y": 0,
                    "geometry": f"{w}x{h}+0+0",
                    "active": True,
                })

    # Sort displays: primary first, then left-to-right by X coordinate
    displays.sort(key=lambda d: (not d.get("primary", False), d.get("x", 0)))
    return displays


def detect_primary_resolution() -> Tuple[int, int]:
    """
    Returns the (width, height) of the primary monitor, or first active monitor.
    Falls back to (1920, 1080) if no display is connected.
    """
    displays = detect_screen_resolutions()
    for d in displays:
        if d.get("primary") and d.get("active") and d.get("width", 0) > 0:
            return d["width"], d["height"]

    for d in displays:
        if d.get("active") and d.get("width", 0) > 0:
            return d["width"], d["height"]

    return 1920, 1080


def detect_resolution() -> Tuple[int, int]:
    """Backward-compatible alias for detect_primary_resolution."""
    return detect_primary_resolution()


def get_screen_resolution_map() -> Dict[str, Tuple[int, int]]:
    """Returns a mapping of display name -> (width, height)."""
    displays = detect_screen_resolutions()
    return {d["name"]: (d.get("width", 0), d.get("height", 0)) for d in displays}


def format_display_summary() -> str:
    """Returns a human-readable one-line summary of detected screens and resolutions."""
    displays = detect_screen_resolutions()
    if not displays:
        return "No displays detected"
    parts = []
    for d in displays:
        p_flag = " (Primary)" if d.get("primary") else ""
        parts.append(f"{d['name']}: {d['width']}x{d['height']}{p_flag}")
    return ", ".join(parts)
