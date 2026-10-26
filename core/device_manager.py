#!/usr/bin/env python3
"""
Device Manager for XInput Pseudo-Seating.
Discovers displays (via xrandr/DRM) and input devices (via xinput and sysfs).
"""

import subprocess
import re
import glob
import logging
from pathlib import Path

logger = logging.getLogger("xinput_seating.device_manager")


def run_command(args, timeout=5, check=False):
    """Run a shell command safely, returning (stdout, stderr, returncode)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )
        return proc.stdout.strip(), proc.stderr.strip(), proc.returncode
    except subprocess.TimeoutExpired:
        logger.warning(f"Command timed out: {' '.join(args)}")
        return "", "Command timed out", -1
    except FileNotFoundError:
        logger.warning(f"Command not found: {args[0]}")
        return "", f"Command not found: {args[0]}", -1
    except Exception as exc:
        logger.warning(f"Error running command {args}: {exc}")
        return "", str(exc), -1


class DeviceManager:
    """Manages discovery and identification of displays and input devices."""

    def __init__(self):
        pass

    # -----------------------------------------------------------------------
    # Display Discovery
    # -----------------------------------------------------------------------
    def get_displays(self):
        """
        Discovers connected video displays using xrandr, falling back to sysfs DRM.
        Returns a list of dicts:
        [{
            'name': 'eDP-1',
            'connected': True,
            'primary': True,
            'width': 1920,
            'height': 1080,
            'x': 0,
            'y': 0,
            'geometry': '1920x1080+0+0',
            'refresh': 60.0
        }, ...]
        """
        displays = []
        out, err, rc = run_command(["xrandr", "--query"])
        total_screen = {"width": 1920, "height": 1080}

        if rc == 0 and out:
            # Parse total screen dimensions
            m_screen = re.search(r"current\s+(\d+)\s+x\s+(\d+)", out)
            if m_screen:
                total_screen["width"] = int(m_screen.group(1))
                total_screen["height"] = int(m_screen.group(2))

            for line in out.splitlines():
                if " connected" in line:
                    parts = line.split()
                    name = parts[0]
                    is_primary = "primary" in parts

                    # Look for geometry WxH+X+Y
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

        # Fallback / augment with sysfs DRM if xrandr returns empty
        if not displays:
            logger.info("xrandr returned no displays; falling back to sysfs DRM")
            drm_paths = glob.glob("/sys/class/drm/card*-*")
            for p in sorted(drm_paths):
                status_file = Path(p) / "status"
                try:
                    status = status_file.read_text().strip()
                except Exception:
                    status = "unknown"

                if status == "connected":
                    conn_name = Path(p).name
                    # strip cardX- prefix
                    name_clean = re.sub(r"^card\d+-", "", conn_name)
                    displays.append({
                        "name": name_clean,
                        "connected": True,
                        "primary": len(displays) == 0,
                        "width": 1920,
                        "height": 1080,
                        "x": 0,
                        "y": 0,
                        "geometry": "1920x1080+0+0",
                        "active": True,
                    })

        # Sort displays: primary first, then left-to-right by X coordinate
        displays.sort(key=lambda d: (not d.get("primary", False), d.get("x", 0)))
        return displays, total_screen

    # -----------------------------------------------------------------------
    # XInput Device Discovery
    # -----------------------------------------------------------------------
    def get_xinput_hierarchy(self):
        """
        Parses `xinput list` into a structured hierarchy of masters and slaves.
        Returns:
            masters: {id: {'id': int, 'name': str, 'type': 'pointer'|'keyboard', 'paired_id': int, 'slaves': [...]}}
            slaves_pointers: [dict, ...]
            slaves_keyboards: [dict, ...]
        """
        out, err, rc = run_command(["xinput", "list"])
        if rc != 0 or not out:
            logger.warning(f"Could not read xinput list: {err}")
            return {}, [], []

        pattern = re.compile(
            r"^[⎡⎜⎣\s]*([^\t]+?)\s+id=(\d+)\s+\[(master|slave|floating slave)\s*(pointer|keyboard)?\s*(?:\((\d+)\))?\]"
        )

        masters = {}
        slaves_pointers = []
        slaves_keyboards = []

        for line in out.splitlines():
            clean = line.strip()
            m = pattern.search(clean)
            if not m:
                continue

            name = m.group(1).strip("↳ \t")
            dev_id = int(m.group(2))
            node_type = m.group(3)
            dev_class = m.group(4) or ""
            attachment = int(m.group(5)) if m.group(5) else None
            is_xtest = "XTEST" in name

            dev_info = {
                "id": dev_id,
                "name": name,
                "type": node_type,
                "class": dev_class,
                "attachment": attachment,
                "is_xtest": is_xtest,
            }

            if node_type == "master":
                masters[dev_id] = {
                    "id": dev_id,
                    "name": name,
                    "class": dev_class,
                    "paired_id": attachment,
                    "slaves": [],
                }
            elif node_type == "slave":
                if dev_class == "pointer":
                    slaves_pointers.append(dev_info)
                elif dev_class == "keyboard":
                    slaves_keyboards.append(dev_info)

        # Associate slaves to masters
        for slave in slaves_pointers + slaves_keyboards:
            att = slave.get("attachment")
            if att in masters:
                masters[att]["slaves"].append(slave)

        return masters, slaves_pointers, slaves_keyboards

    def get_usable_pointers(self):
        """Returns non-XTEST slave pointers suitable for user assignment."""
        _, pointers, _ = self.get_xinput_hierarchy()
        return [p for p in pointers if not p["is_xtest"]]

    def get_usable_keyboards(self):
        """Returns non-XTEST slave keyboards suitable for user assignment."""
        _, _, keyboards = self.get_xinput_hierarchy()
        return [k for k in keyboards if not k["is_xtest"]]

    # -----------------------------------------------------------------------
    # Sysfs & USB Hardware Correlation
    # -----------------------------------------------------------------------
    def get_physical_input_devices(self):
        """
        Reads `/sys/class/input/` to detect physical USB/hardware input devices.
        Returns a list of device dicts.
        """
        devices = []
        try:
            for inp_dir in sorted(Path("/sys/class/input").iterdir()):
                if not inp_dir.name.startswith("input"):
                    continue
                name_file = inp_dir / "name"
                phys_file = inp_dir / "phys"
                cap_file = inp_dir / "capabilities" / "key"

                name = name_file.read_text().strip() if name_file.exists() else inp_dir.name
                phys = phys_file.read_text().strip() if phys_file.exists() else ""

                dev_type = "other"
                if cap_file.exists():
                    try:
                        caps = int(cap_file.read_text().strip().replace(" ", ""), 16)
                        if caps & 0x2:  # KEY_1
                            dev_type = "keyboard"
                        else:
                            dev_type = "mouse/pointer"
                    except Exception:
                        pass

                devices.append({
                    "sys_name": inp_dir.name,
                    "name": name,
                    "phys": phys,
                    "type": dev_type,
                    "path": str(inp_dir.resolve()),
                })
        except Exception as exc:
            logger.warning(f"Error enumerating sysfs input: {exc}")

        return devices

    # -----------------------------------------------------------------------
    # Auto-Configuration Heuristic
    # -----------------------------------------------------------------------
    def auto_detect_seat_assignments(self):
        """
        Proposes an automatic assignment for dual seats (Seat 1 and Seat 2):
        - Seat 1 gets Display 1 (Primary or left-most), Mouse 1, Keyboard 1.
        - Seat 2 gets Display 2, Mouse 2, Keyboard 2 (if available).
        """
        displays, _ = self.get_displays()
        pointers = self.get_usable_pointers()
        keyboards = self.get_usable_keyboards()

        assignment = {
            "seat1": {
                "display": displays[0]["name"] if len(displays) > 0 else "eDP-1",
                "mouse_id": pointers[0]["id"] if len(pointers) > 0 else None,
                "mouse_name": pointers[0]["name"] if len(pointers) > 0 else "",
                "keyboard_id": keyboards[0]["id"] if len(keyboards) > 0 else None,
                "keyboard_name": keyboards[0]["name"] if len(keyboards) > 0 else "",
            },
            "seat2": {
                "display": displays[1]["name"] if len(displays) > 1 else (displays[0]["name"] if displays else "HDMI-1"),
                "mouse_id": pointers[1]["id"] if len(pointers) > 1 else (pointers[0]["id"] if pointers else None),
                "mouse_name": pointers[1]["name"] if len(pointers) > 1 else (pointers[0]["name"] if pointers else ""),
                "keyboard_id": keyboards[1]["id"] if len(keyboards) > 1 else (keyboards[0]["id"] if keyboards else None),
                "keyboard_name": keyboards[1]["name"] if len(keyboards) > 1 else (keyboards[0]["name"] if keyboards else ""),
            }
        }
        return assignment
