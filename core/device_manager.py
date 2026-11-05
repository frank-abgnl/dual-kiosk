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
from typing import Dict, List, Optional, Tuple, Set

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

    @staticmethod
    def _is_usb_xinput(dev_id: int) -> bool:
        """Determines if an xinput node belongs to a physical USB device."""
        out, _, rc = run_command(["xinput", "list-props", str(dev_id)])
        if rc != 0 or not out:
            return False
        m = re.search(r'Device Node \(\d+\):\s*"([^"]+)"', out)
        if not m:
            return False
        dev_node = m.group(1).strip()
        sys_dev = Path(f"/sys/class/input/{Path(dev_node).name}/device")
        try:
            cur = sys_dev.resolve()
        except OSError:
            return False
        while cur and cur != cur.parent and str(cur) != "/sys":
            if (cur / "idVendor").exists():
                return True
            cur = cur.parent
        return False

    def get_usb_pointers(self):
        """Returns non-XTEST slave pointers that are physical USB devices."""
        pointers = self.get_usable_pointers()
        return [p for p in pointers if self._is_usb_xinput(p["id"])]

    def get_usb_keyboards(self):
        """Returns non-XTEST slave keyboards that are physical USB devices."""
        keyboards = self.get_usable_keyboards()
        return [k for k in keyboards if self._is_usb_xinput(k["id"])]

    # -----------------------------------------------------------------------
    # Persistent Peripheral Identification & Resolution
    # -----------------------------------------------------------------------
    @staticmethod
    def get_device_product_id(dev_id: int) -> Optional[Tuple[int, int]]:
        """Returns (vendor_id, product_id) from xinput list-props, if available."""
        out, _, rc = run_command(["xinput", "list-props", str(dev_id)])
        if rc != 0 or not out:
            return None
        m = re.search(r'Device Product ID \((\d+)\):\s*(\d+),\s*(\d+)', out)
        if m:
            try:
                return int(m.group(2)), int(m.group(3))
            except ValueError:
                return None
        return None

    def find_device_by_name(
        self,
        name: str,
        dev_class: str = "pointer",
        exclude_ids: Optional[List[int]] = None,
        expected_product_id: Optional[Tuple[int, int]] = None,
    ) -> Optional[Dict]:
        """
        Locates a connected device by matching its peripheral name (and optionally product ID).
        Prefers exact name match, then substring/case-insensitive match.
        Guarantees that when a device is replugged on a different USB port and receives a new ID,
        it is accurately identified and retrieved.
        """
        if not name:
            return None

        exclude: Set[int] = set(exclude_ids or [])
        candidates = self.get_usb_pointers() if dev_class == "pointer" else self.get_usb_keyboards()
        if not candidates:
            candidates = self.get_usable_pointers() if dev_class == "pointer" else self.get_usable_keyboards()

        clean_target = name.strip().lower()

        # 1. Exact name match + expected product ID
        if expected_product_id:
            for dev in candidates:
                if dev["id"] in exclude:
                    continue
                if dev["name"].strip().lower() == clean_target:
                    prod = self.get_device_product_id(dev["id"])
                    if prod == expected_product_id:
                        return dev

        # 2. Exact name match
        for dev in candidates:
            if dev["id"] in exclude:
                continue
            if dev["name"].strip().lower() == clean_target:
                return dev

        # 3. Substring match (e.g. if driver prefix slightly varies)
        for dev in candidates:
            if dev["id"] in exclude:
                continue
            dev_clean = dev["name"].strip().lower()
            if clean_target in dev_clean or dev_clean in clean_target:
                return dev

        return None

    def resolve_seat_devices(
        self,
        seats_config: List[Dict],
        preserve_memory: bool = True,
    ) -> Tuple[List[Dict], List[str]]:
        """
        Scans current live XInput devices and matches them against each seat's locked peripheral names.
        - If a device was unplugged and plugged into a different USB port (new ID),
          its live ID is updated.
        - If a device is unplugged, it retains its locked name in memory so it can be reseated upon replug.
        Returns (updated_seats, log_messages).
        """
        resolved_seats = []
        messages = []
        used_pointer_ids = set()
        used_keyboard_ids = set()

        for seat in seats_config:
            s_copy = dict(seat)
            seat_id = s_copy.get("id", "seat1")
            disp = s_copy.get("display", "unknown")
            is_locked = s_copy.get("lock_peripherals", True)

            mouse_name = s_copy.get("mouse_name", "")
            orig_mouse_id = s_copy.get("mouse_id")
            mouse_prod = s_copy.get("mouse_product_id")

            kbd_name = s_copy.get("keyboard_name", "")
            orig_kbd_id = s_copy.get("keyboard_id")
            kbd_prod = s_copy.get("keyboard_product_id")

            # Resolve Mouse by locked peripheral name
            live_mouse = self.find_device_by_name(
                mouse_name, dev_class="pointer", exclude_ids=list(used_pointer_ids), expected_product_id=mouse_prod
            )
            if live_mouse:
                new_m_id = live_mouse["id"]
                used_pointer_ids.add(new_m_id)
                s_copy["mouse_id"] = new_m_id
                s_copy["mouse_connected"] = True
                s_copy["mouse_name"] = live_mouse["name"]
                s_copy["mouse_product_id"] = self.get_device_product_id(new_m_id)
                if orig_mouse_id != new_m_id and orig_mouse_id is not None:
                    messages.append(
                        f"Mouse '{live_mouse['name']}' locked to {disp} ({seat_id}) recognized on new port (ID: {orig_mouse_id} -> {new_m_id})"
                    )
            else:
                s_copy["mouse_connected"] = False
                if mouse_name:
                    messages.append(f"Mouse '{mouse_name}' locked to {disp} ({seat_id}) is currently unplugged.")
                if not preserve_memory:
                    s_copy["mouse_id"] = None

            # Resolve Keyboard by locked peripheral name
            live_kbd = self.find_device_by_name(
                kbd_name, dev_class="keyboard", exclude_ids=list(used_keyboard_ids), expected_product_id=kbd_prod
            )
            if live_kbd:
                new_k_id = live_kbd["id"]
                used_keyboard_ids.add(new_k_id)
                s_copy["keyboard_id"] = new_k_id
                s_copy["keyboard_connected"] = True
                s_copy["keyboard_name"] = live_kbd["name"]
                s_copy["keyboard_product_id"] = self.get_device_product_id(new_k_id)
                if orig_kbd_id != new_k_id and orig_kbd_id is not None:
                    messages.append(
                        f"Keyboard '{live_kbd['name']}' locked to {disp} ({seat_id}) recognized on new port (ID: {orig_kbd_id} -> {new_k_id})"
                    )
            else:
                s_copy["keyboard_connected"] = False
                if kbd_name:
                    messages.append(f"Keyboard '{kbd_name}' locked to {disp} ({seat_id}) is currently unplugged.")
                if not preserve_memory:
                    s_copy["keyboard_id"] = None

            s_copy["lock_peripherals"] = is_locked
            resolved_seats.append(s_copy)

        return resolved_seats, messages

    # -----------------------------------------------------------------------
    # Auto-Configuration Heuristic (with Memory Retention)
    # -----------------------------------------------------------------------
    def auto_detect_seat_assignments(self, existing_seats: Optional[List[Dict]] = None):
        """
        Auto-identifies screens and assigns connected USB devices (mouses & keyboards) to dual seats.
        Retains memory: if existing_seats has locked peripheral names for displays,
        it preserves those associations even if devices were plugged into different ports.
        """
        displays, _ = self.get_displays()
        usb_pointers = self.get_usb_pointers()
        usb_keyboards = self.get_usb_keyboards()
        all_pointers = self.get_usable_pointers()
        all_keyboards = self.get_usable_keyboards()

        pointers = usb_pointers + [p for p in all_pointers if p not in usb_pointers]
        keyboards = usb_keyboards + [k for k in all_keyboards if k not in usb_keyboards]

        d1 = displays[0] if len(displays) > 0 else {"name": "eDP-1", "width": 1920, "height": 1080}
        d2 = displays[1] if len(displays) > 1 else (displays[0] if displays else {"name": "HDMI-1", "width": 1920, "height": 1080})

        # Check if existing seats already have locked peripheral names to retain
        has_locked_memory = False
        if existing_seats and len(existing_seats) >= 2:
            s1_locked = existing_seats[0].get("lock_peripherals", True) and (
                existing_seats[0].get("mouse_name") or existing_seats[0].get("keyboard_name")
            )
            s2_locked = existing_seats[1].get("lock_peripherals", True) and (
                existing_seats[1].get("mouse_name") or existing_seats[1].get("keyboard_name")
            )
            has_locked_memory = s1_locked or s2_locked

        if has_locked_memory and existing_seats:
            logger.info("Auto-detection: Preserving memory of locked peripheral names to displays...")
            resolved, _ = self.resolve_seat_devices(existing_seats, preserve_memory=True)
            return {s["id"]: s for s in resolved}

        # Clean assignment from connected hardware
        m1 = pointers[0] if len(pointers) > 0 else None
        m2 = pointers[1] if len(pointers) > 1 else (pointers[0] if pointers else None)

        k1 = keyboards[0] if len(keyboards) > 0 else None
        k2 = keyboards[1] if len(keyboards) > 1 else (keyboards[0] if keyboards else None)

        assignment = {
            "seat1": {
                "id": "seat1",
                "display": d1.get("name", "eDP-1"),
                "resolution": f"{d1.get('width', 1920)}x{d1.get('height', 1080)}",
                "width": d1.get("width", 1920),
                "height": d1.get("height", 1080),
                "mouse_id": m1["id"] if m1 else None,
                "mouse_name": m1["name"] if m1 else "",
                "keyboard_id": k1["id"] if k1 else None,
                "keyboard_name": k1["name"] if k1 else "",
                "lock_peripherals": True,
                "locked_display": d1.get("name", "eDP-1"),
            },
            "seat2": {
                "id": "seat2",
                "display": d2.get("name", "HDMI-1"),
                "resolution": f"{d2.get('width', 1920)}x{d2.get('height', 1080)}",
                "width": d2.get("width", 1920),
                "height": d2.get("height", 1080),
                "mouse_id": m2["id"] if m2 else None,
                "mouse_name": m2["name"] if m2 else "",
                "keyboard_id": k2["id"] if k2 else None,
                "keyboard_name": k2["name"] if k2 else "",
                "lock_peripherals": True,
                "locked_display": d2.get("name", "HDMI-1"),
            }
        }
        return assignment
