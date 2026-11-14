#!/usr/bin/env python3
"""
Device Manager for XInput Pseudo-Seating.
Discovers displays (via xrandr/DRM) and input devices (via xinput and sysfs).
Only real, user-facing hardware (keyboards, mice, physical displays) is reported.
"""

import subprocess
import re
import glob
import json
import logging
from pathlib import Path
import time
from typing import Dict, List, Optional, Tuple, Set

from core.peripheral_resolver import _denied  # name deny-list (power button, consumer control, webcam, ...)

logger = logging.getLogger("xinput_seating.device_manager")

VIRTUAL_DISPLAY_RE = re.compile(r"(?i)^(virtual|none-|writeback)")
MIN_KEYBOARD_KEYS = 60     # real keyboards report 80+; extra HID nodes report a handful
MIN_MOUSE_BUTTONS = 3


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

    _displays_cache: Optional[Tuple[List[Dict], Dict]] = None
    _displays_cache_ts: float = 0.0
    _hierarchy_cache: Optional[Tuple[Dict, List[Dict], List[Dict]]] = None
    _hierarchy_cache_ts: float = 0.0
    _probe_cache: Dict[Tuple[int, str], Tuple[float, Dict]] = {}
    CACHE_TTL: float = 2.0
    PROBE_TTL: float = 10.0

    @classmethod
    def clear_cache(cls):
        """Invalidates all cached display and xinput queries."""
        cls._displays_cache = None
        cls._displays_cache_ts = 0.0
        cls._hierarchy_cache = None
        cls._hierarchy_cache_ts = 0.0
        cls._probe_cache = {}

    def __init__(self):
        pass

    # -----------------------------------------------------------------------
    # Display Discovery & Auto-Assignment
    # -----------------------------------------------------------------------
    def probe_and_activate_displays(self) -> Tuple[List[Dict], Dict[str, int]]:
        """
        Forces a hardware re-probe of displays via xrandr/DRM, activates any connected
        but unconfigured or mirrored displays (via ensure_extended), and returns
        (active_displays, total_canvas).
        """
        displays, total = self.get_displays()
        try:
            from core.display_guard import ensure_extended
            from core.config_manager import ConfigManager
            import time
            cfg_loaded = ConfigManager().load_config()
            saved = cfg_loaded.get("display_layout") or cfg_loaded.get("settings", {}).get("display_layout", {})
            acts = ensure_extended(displays, saved)
            if acts:
                time.sleep(0.3)
        except Exception as exc:
            logger.debug(f"ensure_extended notice: {exc}")

        # Re-query after xrandr updates
        displays, total = self.get_displays()
        active = [d for d in displays if d.get("connected", True) and d.get("width", 0) > 0]
        return active, total

    def assign_displays_to_seats(self, seats: List[Dict], displays: List[Dict]) -> Tuple[List[Dict], List[str]]:
        """
        Assigns active displays to seats respecting preferences (locked_display, primary)
        and priority rules. Returns (updated_seats, log_messages).
        """
        active = [d for d in displays if d.get("connected", True) and d.get("width", 0) > 0]
        if not active:
            return seats, ["No active displays found to assign."]

        messages = []
        disp_names = [d["name"] for d in active]
        assigned_displays = set()
        assigned_seats = set()
        updated_seats = [dict(s) for s in seats]

        # Phase 1: Preference for locked_display match
        for s in updated_seats:
            sid = s.get("id", "seat")
            pref = s.get("locked_display")
            if pref and pref in disp_names and pref not in assigned_displays:
                s["display"] = pref
                assigned_displays.add(pref)
                assigned_seats.add(sid)
                messages.append(f"{sid}: Retained preferred locked display '{pref}'")

        # Phase 2: Preserve current seat['display'] if active and unassigned
        for s in updated_seats:
            sid = s.get("id", "seat")
            if sid in assigned_seats:
                continue
            cur = s.get("display")
            if cur and cur in disp_names and cur not in assigned_displays:
                s["display"] = cur
                assigned_displays.add(cur)
                assigned_seats.add(sid)

        # Phase 3: Fill unassigned seats from remaining connected displays
        rem_displays = [d for d in active if d["name"] not in assigned_displays]
        rem_displays.sort(key=lambda d: (not d.get("primary", False), d.get("x", 0)))

        for s in updated_seats:
            sid = s.get("id", "seat")
            if sid not in assigned_seats:
                if rem_displays:
                    chosen = rem_displays.pop(0)
                    old_disp = s.get("display")
                    s["display"] = chosen["name"]
                    assigned_displays.add(chosen["name"])
                    assigned_seats.add(sid)
                    messages.append(f"{sid}: Automatically assigned newly detected display '{chosen['name']}' (was '{old_disp}')")
                    if not s.get("locked_display"):
                        s["locked_display"] = chosen["name"]
                else:
                    # No display available for this seat; clear duplicate/disconnected display
                    s["display"] = None
                    messages.append(f"{sid}: No display available to assign.")

        return updated_seats, messages

    def get_displays(self, force: bool = False):
        """
        Discovers connected physical video displays using xrandr, falling back to sysfs DRM.
        Virtual / dummy / writeback connectors are ignored.
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
        Uses 2-second TTL cache unless force=True.
        """
        now = time.time()
        if not force and self._displays_cache is not None and (now - self._displays_cache_ts < self.CACHE_TTL):
            return json.loads(json.dumps(self._displays_cache[0])), dict(self._displays_cache[1])

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

        # Ignore virtual / dummy / writeback connectors (not real monitors)
        displays = [d for d in displays if not VIRTUAL_DISPLAY_RE.match(d["name"])]

        # Sort displays: primary first, then left-to-right by X coordinate
        displays.sort(key=lambda d: (not d.get("primary", False), d.get("x", 0)))
        DeviceManager._displays_cache = (displays, total_screen)
        DeviceManager._displays_cache_ts = now
        return json.loads(json.dumps(displays)), dict(total_screen)

    # -----------------------------------------------------------------------
    # XInput Device Discovery
    # -----------------------------------------------------------------------
    _floating_class_cache: Dict[int, str] = {}

    def _classify_floating(self, dev_id: int, name: str = "") -> str:
        if dev_id in self._floating_class_cache:
            return self._floating_class_cache[dev_id]
        out, _, _ = run_command(["xinput", "list", str(dev_id)])
        out_lower = out.lower()
        if "reporting" in out_lower or "keys" in out_lower or "key" in out_lower:
            cls = "keyboard"
        elif "button" in out_lower:
            cls = "pointer"
        elif "keyboard" in name.lower() or "kbd" in name.lower():
            cls = "keyboard"
        else:
            cls = "pointer"
        self._floating_class_cache[dev_id] = cls
        return cls

    # -----------------------------------------------------------------------
    # Physical-device filtering (ignore power buttons, consumer control, sibling nodes...)
    # -----------------------------------------------------------------------
    @staticmethod
    def _bus_of(dev_id: int) -> Tuple[str, Optional[str]]:
        """Returns ('usb', sysfs_path) | ('internal', None) | ('unknown', None)."""
        out, _, rc = run_command(["xinput", "list-props", str(dev_id)])
        if rc != 0 or not out:
            return "unknown", None
        m = re.search(r'Device Node \(\d+\):\s*"([^"]+)"', out)
        if not m:
            return "unknown", None
        try:
            start = Path(f"/sys/class/input/{Path(m.group(1)).name}/device").resolve()
        except OSError:
            return "unknown", None
        # During a (re)plug the sysfs link may not exist yet: resolve() then stays under
        # /sys/class and tells us nothing, so report 'unknown' (device is kept).
        if not str(start).startswith("/sys/devices"):
            return "unknown", None
        cur = start
        while cur != cur.parent and str(cur) != "/sys":
            if (cur / "idVendor").exists():
                return "usb", str(cur)
            cur = cur.parent
        if "/usb" in str(start):
            return "unknown", None   # on a USB path but descriptor not readable yet
        return "internal", None

    def _probe_node(self, dev: Dict) -> Dict:
        """
        Returns {'keys', 'buttons', 'bus', 'usb_parent', 'conclusive'} for an xinput node.
        Only the static capability counts (keys / buttons) are cached, per device name+class,
        and only when the probe succeeded. Bus / USB parent are looked up fresh every time:
        xinput IDs are recycled and sysfs may not be ready right after a replug, so caching
        them could wrongly drop a freshly replugged device.
        """
        ckey = (dev["name"], dev.get("class", ""))
        now = time.time()
        hit = DeviceManager._probe_cache.get(ckey)
        if hit and now - hit[0] < self.PROBE_TTL:
            keys, buttons = hit[1]
            conclusive = True
        else:
            out, _, rc = run_command(["xinput", "list", str(dev["id"])])
            # Modern xinput: "Keycodes supported: 248"; legacy xinput: "Reporting 248 keys"
            m_k = (re.search(r"Keycodes supported:\s*(\d+)", out)
                   or re.search(r"Reporting (\d+) keys", out))
            m_b = re.search(r"Buttons supported:\s*(\d+)", out)
            keys = int(m_k.group(1)) if m_k else 0
            buttons = int(m_b.group(1)) if m_b else 0
            conclusive = rc == 0 and bool(out) and bool(m_k or m_b)
            if conclusive:
                DeviceManager._probe_cache[ckey] = (now, (keys, buttons))
        bus, path = self._bus_of(dev["id"])
        return {"keys": keys, "buttons": buttons, "bus": bus,
                "usb_parent": path, "conclusive": conclusive}

    @staticmethod
    def _allow_internal() -> bool:
        try:
            from core.config_manager import ConfigManager
            return bool(ConfigManager().load_config().get("settings", {}).get("allow_internal_input", False))
        except Exception:
            return False

    def filter_physical(self, devices: List[Dict], dev_class: str) -> List[Dict]:
        """
        Keeps only real, user-facing keyboards / mice:
          * drops XTEST, power/sleep buttons, consumer/system control, webcams, audio jacks...
          * drops nodes that don't look like a real keyboard (<60 keys) or mouse (<3 buttons)
          * drops non-USB devices (laptop keyboard/touchpad) unless settings.allow_internal_input
          * collapses the several xinput nodes of ONE physical USB device into a single entry
        Sibling nodes are still moved together with their primary by attach_with_siblings().
        Nodes whose sysfs info cannot be read ('unknown') are kept so odd/floating devices don't vanish.
        """
        result = self._filter_physical(devices, dev_class, strict=True)
        if not result:
            # Safety net: if the key/button probe rejected everything (unexpected xinput
            # output format), fall back to name deny-list + USB sibling dedupe only, so
            # the launcher never gets stuck with zero keyboards / mice.
            result = self._filter_physical(devices, dev_class, strict=False)
            if result:
                logger.warning(f"Strict {dev_class} filter found nothing; using relaxed filter.")
        return result

    def _filter_physical(self, devices: List[Dict], dev_class: str, strict: bool) -> List[Dict]:
        allow_internal = self._allow_internal()
        singles: List[Dict] = []
        groups: Dict[str, Tuple[int, Dict]] = {}
        for d in devices:
            if d.get("is_xtest") or _denied(d["name"]):
                continue
            info = self._probe_node(d)
            if strict and dev_class == "keyboard" and info["keys"] < MIN_KEYBOARD_KEYS and info["conclusive"] and info["bus"] != "unknown":
                continue
            if strict and dev_class == "pointer" and info["buttons"] < MIN_MOUSE_BUTTONS and info["conclusive"] and info["bus"] != "unknown":
                continue
            if info["bus"] == "internal" and not allow_internal:
                continue
            parent = info["usb_parent"]
            if not parent:
                singles.append(d)
                continue
            score = info["keys"] if dev_class == "keyboard" else info["buttons"]
            best = groups.get(parent)
            if best is None or score > best[0]:
                groups[parent] = (score, d)
        result = singles + [d for _, d in groups.values()]
        return sorted(result, key=lambda d: d["id"])

    def get_xinput_hierarchy(self, force: bool = False):
        """
        Parses `xinput list` into a structured hierarchy of masters and slaves.
        Returns:
            masters: {id: {'id': int, 'name': str, 'type': 'pointer'|'keyboard', 'paired_id': int, 'slaves': [...]}}
            slaves_pointers: [dict, ...]
            slaves_keyboards: [dict, ...]
        Uses 2-second TTL cache unless force=True.
        NOTE: this is the RAW hierarchy (all nodes). Use get_usable_* for real hardware only.
        """
        now = time.time()
        if not force and self._hierarchy_cache is not None and (now - self._hierarchy_cache_ts < self.CACHE_TTL):
            return (
                json.loads(json.dumps(self._hierarchy_cache[0])),
                json.loads(json.dumps(self._hierarchy_cache[1])),
                json.loads(json.dumps(self._hierarchy_cache[2])),
            )

        out, err, rc = run_command(["xinput", "list"])
        if rc != 0 or not out:
            logger.warning(f"Could not read xinput list: {err}")
            return {}, [], []

        pattern = re.compile(
            r"^[⎡⎜⎣\s~∼]*([^\t]+?)\s+id=(\d+)\s+\[(master|slave|floating slave)\s*(pointer|keyboard)?\s*(?:\((\d+)\))?\]"
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
                "floating": False,
            }

            if node_type == "master":
                masters[dev_id] = {
                    "id": dev_id,
                    "name": name,
                    "class": dev_class,
                    "paired_id": attachment,
                    "slaves": [],
                    "floating": False,
                }
            elif node_type == "slave":
                dev_info["floating"] = False
                if dev_class == "pointer":
                    slaves_pointers.append(dev_info)
                elif dev_class == "keyboard":
                    slaves_keyboards.append(dev_info)
            elif node_type == "floating slave":
                dev_info["floating"] = True
                dev_info["attachment"] = None
                cls = dev_class or self._classify_floating(dev_id, name)
                dev_info["class"] = cls
                if cls == "pointer":
                    slaves_pointers.append(dev_info)
                elif cls == "keyboard":
                    slaves_keyboards.append(dev_info)

        # Associate slaves to masters
        for slave in slaves_pointers + slaves_keyboards:
            att = slave.get("attachment")
            if att in masters:
                masters[att]["slaves"].append(slave)

        DeviceManager._hierarchy_cache = (masters, slaves_pointers, slaves_keyboards)
        DeviceManager._hierarchy_cache_ts = now
        return (
            json.loads(json.dumps(masters)),
            json.loads(json.dumps(slaves_pointers)),
            json.loads(json.dumps(slaves_keyboards)),
        )

    def get_usable_pointers(self):
        """Real physical mice only (no XTEST / buttons / sibling nodes)."""
        _, pointers, _ = self.get_xinput_hierarchy()
        return self.filter_physical(pointers, "pointer")

    def get_usable_keyboards(self):
        """Real physical keyboards only, one entry per physical device."""
        _, _, keyboards = self.get_xinput_hierarchy()
        return self.filter_physical(keyboards, "keyboard")

    # -----------------------------------------------------------------------
    # Sysfs & USB Hardware Correlation
    # -----------------------------------------------------------------------
    def get_physical_input_devices(self):
        """
        Reads `/sys/class/input/` to detect physical USB/hardware input devices.
        Non-hardware entries (power button, video bus, ...) are skipped.
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
                if _denied(name):
                    continue
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
        """Returns real slave pointers that are physical USB devices."""
        pointers = self.get_usable_pointers()
        return [p for p in pointers if self._is_usb_xinput(p["id"])]

    def get_usb_keyboards(self):
        """Returns real slave keyboards that are physical USB devices."""
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
            from core.peripheral_resolver import PeripheralResolver, PeripheralMemory
            resolved, _, _, _ = PeripheralResolver().resolve(
                existing_seats, pointers, keyboards, {}, PeripheralMemory()
            )
            return {s["id"]: s for s in resolved}

        # Clean assignment from connected hardware
        m1 = pointers[0] if len(pointers) > 0 else None
        m2 = pointers[1] if len(pointers) > 1 else None

        k1 = keyboards[0] if len(keyboards) > 0 else None
        k2 = keyboards[1] if len(keyboards) > 1 else None

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
