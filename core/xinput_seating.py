#!/usr/bin/env python3
"""
XInput MPX Seating Engine.
Handles master pointer/keyboard creation, reattachment of slave devices,
and confinement of each seat's master pointer to its monitor.
"""

import os
import subprocess
import re
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from core.pointer_barriers import PointerConfiner
from core.peripheral_resolver import KEYS, PROMOTE_AFTER_SEC, PeripheralMemory, PeripheralResolver
from core import overlay_state

logger = logging.getLogger("xinput_seating.engine")


def run_command(args, timeout=5):
    """Run a command returning (stdout, stderr, returncode)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.stdout.strip(), proc.stderr.strip(), proc.returncode
    except Exception as exc:
        logger.warning(f"Error running command {args}: {exc}")
        return "", str(exc), -1


class XInputSeatingEngine:
    """Manages Multi-Pointer X (MPX) pseudo-seating configurations."""

    CORE_POINTER_ID = 2
    CORE_KEYBOARD_ID = 3

    def __init__(self):
        # Holds XFixes pointer barriers; they live as long as this process.
        self.confiner = PointerConfiner()
        self.seat_keyboards: Dict[str, int] = {}  # seat_id -> master keyboard id
        self.seat_pointers: Dict[str, int] = {}   # seat_id -> master pointer id
        self.seat_windows: Dict[str, str] = {}    # seat_id -> kiosk window id
        # Only the process that applied the seating may reseat hotplugged devices.
        self.owns_seating = False
        self.memory = PeripheralMemory()                   # short-term seat memory + stand-in clocks
        self.resolver = PeripheralResolver(self._promote_after())
        self._last_refresh = 0.0
        self._hotplug_lock = threading.Lock()
        self._verified_sig = None                          # topology we already verified
        self._attempts: Dict[tuple, int] = {}
        self._last_reseat: Dict[Tuple[str, str], float] = {}
        self._absent: set = set()

    # -----------------------------------------------------------------------
    # Master Pair Management
    # -----------------------------------------------------------------------
    def get_existing_masters(self) -> Dict[str, Dict]:
        """
        Returns a dictionary of current master pairs:
        {
            'Virtual core': {'pointer_id': 2, 'keyboard_id': 3},
            'Seat2': {'pointer_id': 14, 'keyboard_id': 15},
            ...
        }
        """
        out, _, rc = run_command(["xinput", "list"])
        if rc != 0 or not out:
            return {}

        masters = {}
        # Matches: "Name pointer id=X [master pointer (Y)]"
        p_pattern = re.compile(r"^[⎡\s]*([^\t]+?)\s+pointer\s+id=(\d+)\s+\[master pointer\s*\((\d+)\)\]", re.M)
        for m in p_pattern.finditer(out):
            name = m.group(1).strip("⎡ \t")
            ptr_id = int(m.group(2))
            kbd_id = int(m.group(3))
            masters[name] = {"pointer_id": ptr_id, "keyboard_id": kbd_id}

        return masters

    def create_master_pair(self, seat_name: str = "Seat2") -> Tuple[Optional[int], Optional[int]]:
        """
        Creates a master pointer and master keyboard pair via `xinput create-master`.
        Returns (master_pointer_id, master_keyboard_id).
        """
        masters = self.get_existing_masters()
        if seat_name in masters:
            logger.info(f"Master pair for '{seat_name}' already exists: {masters[seat_name]}")
            return masters[seat_name]["pointer_id"], masters[seat_name]["keyboard_id"]

        logger.info(f"Creating master pair for '{seat_name}'...")
        out, err, rc = run_command(["xinput", "create-master", seat_name])
        if rc != 0:
            logger.error(f"Failed to create master pair '{seat_name}': {err}")
            return None, None

        # Re-check masters
        updated = self.get_existing_masters()
        if seat_name in updated:
            ptr_id = updated[seat_name]["pointer_id"]
            kbd_id = updated[seat_name]["keyboard_id"]
            logger.info(f"Created master pair '{seat_name}' -> Pointer ID {ptr_id}, Keyboard ID {kbd_id}")
            return ptr_id, kbd_id

        # Fallback search if named differently
        for name, data in updated.items():
            if seat_name.lower() in name.lower():
                return data["pointer_id"], data["keyboard_id"]

        return None, None

    def remove_master_pair(self, seat_name: str = "Seat2") -> bool:
        """Removes a master pointer (which also removes its paired keyboard)."""
        ptr_target = f"{seat_name} pointer"
        logger.info(f"Removing master pair '{ptr_target}'...")
        _, err, rc = run_command(["xinput", "remove-master", ptr_target])
        if rc != 0:
            # Try removing by ID if found
            masters = self.get_existing_masters()
            if seat_name in masters:
                pid = masters[seat_name]["pointer_id"]
                _, err2, rc2 = run_command(["xinput", "remove-master", str(pid)])
                return rc2 == 0
            logger.warning(f"Could not remove master '{seat_name}': {err}")
            return False
        return True

    # -----------------------------------------------------------------------
    # Slave Device Reattachment
    # -----------------------------------------------------------------------
    def reattach_device(self, slave_id_or_name, master_id_or_name) -> bool:
        """
        Attaches a slave input device (mouse or keyboard) to a target master.
        """
        cmd = ["xinput", "reattach", str(slave_id_or_name), str(master_id_or_name)]
        logger.info(f"Reattaching device: {' '.join(cmd)}")
        out, err, rc = run_command(cmd)
        if rc != 0:
            logger.warning(f"Failed to reattach {slave_id_or_name} to {master_id_or_name}: {err}")
            return False
        return True

    # -----------------------------------------------------------------------
    # Sibling devices (one physical keyboard = several xinput nodes)
    # -----------------------------------------------------------------------
    @staticmethod
    def _usb_parent(dev_id) -> Optional[str]:
        """Returns the sysfs path of the USB device that owns this xinput node, if any."""
        out, _, rc = run_command(["xinput", "list-props", str(dev_id)])
        if rc != 0:
            return None
        m = re.search(r'Device Node \(\d+\):\s*"([^"]+)"', out)
        if not m:
            return None
        sys_dev = f"/sys/class/input/{os.path.basename(m.group(1))}/device"
        try:
            cur = os.path.realpath(sys_dev)
        except OSError:
            return None
        while cur and cur != "/sys":
            if os.path.exists(os.path.join(cur, "idVendor")):
                return cur
            cur = os.path.dirname(cur)
        return None

    def attach_with_siblings(self, dev_id: int, master_id, dev_class: str) -> bool:
        """
        Reattaches a device AND all other xinput nodes of the same class that belong to
        the same physical USB device (e.g. 'Keyboard' + 'Keyboard Consumer Control').
        Otherwise part of the keyboard keeps typing into the other seat.
        """
        ok = self.reattach_device(dev_id, master_id)
        parent = self._usb_parent(dev_id)
        if not parent:
            return ok
        from core.device_manager import DeviceManager
        _, ptrs, kbds = DeviceManager().get_xinput_hierarchy()
        for sib in (ptrs if dev_class == "pointer" else kbds):
            if sib["id"] == dev_id or sib["is_xtest"]:
                continue
            if self._usb_parent(sib["id"]) == parent:
                logger.info(f"Also attaching sibling node {sib['id']} ({sib['name']}) to {master_id}")
                self.reattach_device(sib["id"], master_id)
        return ok

    def copy_keyboard_layout(self, target_kbd_master: int):
        """New master keyboards start with a default layout; copy the core keyboard's."""
        out, _, rc = run_command(["setxkbmap", "-query"])
        if rc != 0:
            return
        q = dict(
            (k.strip(), v.strip())
            for k, v in (l.split(":", 1) for l in out.splitlines() if ":" in l)
        )
        cmd = ["setxkbmap", "-device", str(target_kbd_master)]
        if q.get("layout"):
            cmd += ["-layout", q["layout"]]
        if q.get("variant"):
            cmd += ["-variant", q["variant"]]
        if q.get("options"):
            cmd += ["-option", "", "-option", q["options"]]
        run_command(cmd)

    def focus_seat_keyboard(self, seat_id: str, window_id) -> bool:
        """
        Binds a seat's kiosk window to its own master pointer/keyboard.
        Seats whose Control Board overlay is whitelisted are skipped, otherwise a
        tick for the *other* seat would steal focus back from the overlay.
        """
        self.seat_windows[seat_id] = str(window_id)
        ok = True
        for sid, win in list(self.seat_windows.items()):
            if overlay_state.is_active(sid):
                continue
            ptr = self.seat_pointers.get(sid)
            kbd = self.seat_keyboards.get(sid)
            if ptr is not None:
                self.confiner.set_client_pointer(win, ptr)
            if kbd is not None:
                ok = self.confiner.set_focus(kbd, win) and ok
        return ok

    # -----------------------------------------------------------------------
    # Screen Confinement
    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # Live device resolution (stable, one-to-one)
    # -----------------------------------------------------------------------
    def _seat_masters(self, seats_config: List[Dict]) -> Dict[str, Dict]:
        m = {}
        for i, s in enumerate(seats_config):
            sid = s.get("id", f"seat{i + 1}")
            if s.get("is_primary", False):
                m[sid] = {"pointer": self.CORE_POINTER_ID, "keyboard": self.CORE_KEYBOARD_ID}
            else:
                m[sid] = {"pointer": self.seat_pointers.get(sid), "keyboard": self.seat_keyboards.get(sid)}
        return m

    @staticmethod
    def _promote_after() -> float:
        """Hours a stand-in must stay connected before it becomes the locked device (default 3)."""
        try:
            from core.config_manager import ConfigManager
            hours = ConfigManager().load_config().get("settings", {}).get("peripheral_promote_hours", 3)
            return float(hours) * 3600
        except Exception:
            return PROMOTE_AFTER_SEC

    def _persist_lock(self, seat_id: str, cls: str, name: str, ident: Dict):
        """Writes a (new) locked device into config.json."""
        try:
            from core.config_manager import ConfigManager
            cm = ConfigManager()
            cfg = cm.load_config()
            name_k, _, ident_k = KEYS[cls]
            for seat in cfg.get("seats", []):
                if seat.get("id") == seat_id:
                    seat[name_k], seat[ident_k] = name, ident
            cm.save_config(cfg)
        except Exception as exc:
            logger.warning(f"Could not persist locked device for {seat_id}: {exc}")

    def _handle_events(self, events: List[Dict]) -> List[str]:
        """Persists lock changes and turns resolver events into human-readable messages."""
        msgs = []
        for ev in events:
            label = "Mouse" if ev["cls"] == "pointer" else "Keyboard"
            sid, name, locked = ev["seat"], ev["name"], ev.get("locked") or "?"
            if ev["type"] == "lock_ident":
                self._persist_lock(sid, ev["cls"], name, ev["ident"])
            elif ev["type"] == "promote":
                self._persist_lock(sid, ev["cls"], name, ev["ident"])
                msgs.append(f"{label} '{name}' has served Seat '{sid}' for {self.resolver.promote_after / 3600:g} h "
                            f"-> now the LOCKED device (old locked '{locked}' forgotten)")
            elif ev["type"] == "substitute":
                msgs.append(f"{label} '{name}' seated on Seat '{sid}' as stand-in for missing locked '{locked}'")
            elif ev["type"] == "locked_back":
                msgs.append(f"Locked {label} '{name}' is back on Seat '{sid}' (stand-in released)")
        return msgs

    def resolve_live(self, seats_config: List[Dict]):
        """Maps saved seat peripherals to live xinput devices. Returns (resolved_seats, assignments)."""
        from core.device_manager import DeviceManager
        _, ptrs, kbds = DeviceManager().get_xinput_hierarchy()
        resolved, assignments, events, _ = self.resolver.resolve(
            seats_config, ptrs, kbds, self._seat_masters(seats_config), self.memory)
        for m in self._handle_events(events):
            logger.info(f"[Seat Resolver] {m}")
        self.memory.save()
        return resolved, assignments

    def discover_masters(self, seats_config: List[Dict]) -> bool:
        """
        Learns master IDs created by ANOTHER process (launcher / autostart) so this process
        (e.g. the overlay GUI) can bind its window to the right seat keyboard and pointer.
        """
        masters = self.get_existing_masters()
        found = False
        for i, s in enumerate(seats_config):
            sid = s.get("id", f"seat{i + 1}")
            if s.get("is_primary", False):
                self.seat_pointers[sid], self.seat_keyboards[sid] = self.CORE_POINTER_ID, self.CORE_KEYBOARD_ID
                found = True
            else:
                m = masters.get(f"Seat_{sid.upper()}")
                if m:
                    self.seat_pointers[sid], self.seat_keyboards[sid] = m["pointer_id"], m["keyboard_id"]
                    found = True
        return found

    def _is_absolute(self, dev_id) -> bool:
        """True for tablets/touchscreens. Normal mice are relative."""
        out, _, rc = run_command(["xinput", "list", str(dev_id)])
        return rc == 0 and "mode: absolute" in out.lower()

    def map_pointer_to_output(
        self,
        pointer_id: int,
        output_name: str,
        geometry: Optional[Tuple[int, int, int, int]] = None,
        total_screen: Optional[Tuple[int, int]] = None,
    ) -> bool:
        """
        Maps an *absolute* device (tablet/touchscreen) to a monitor.
        Relative mice are skipped: the transformation matrix offset has no effect on them
        (and its scale factor would only slow the mouse down). They are confined with
        pointer barriers on their master instead (see confine_master).
        """
        if not self._is_absolute(pointer_id):
            logger.info(f"Device {pointer_id} is relative; skipping output mapping (barriers handle it).")
            return True
        _, err, rc = run_command(["xinput", "map-to-output", str(pointer_id), output_name])
        if rc != 0:
            logger.warning(f"map-to-output failed for {pointer_id}: {err}")
        return rc == 0

    def confine_master(self, master_ptr_id: int, geometry: Tuple[int, int, int, int],
                       total_screen: Optional[Tuple[int, int]] = None) -> bool:
        """geometry = (width, height, x, y)."""
        w, h, x, y = geometry
        return self.confiner.confine(master_ptr_id, x, y, w, h, total_screen)

    def reset_pointer_matrix(self, pointer_id: int) -> bool:
        """Resets the Coordinate Transformation Matrix of a pointer to identity."""
        cmd = [
            "xinput", "set-prop", str(pointer_id),
            "--type=float", "Coordinate Transformation Matrix",
            "1", "0", "0",
            "0", "1", "0",
            "0", "0", "1"
        ]
        _, _, rc = run_command(cmd)
        return rc == 0

    # -----------------------------------------------------------------------
    # Full Teardown & Reset
    # -----------------------------------------------------------------------
    def reset_all_to_core(self) -> bool:
        """
        Restores standard single-seat environment:
        0. Releases pointer barriers.
        1. Reattaches all slave pointers to Virtual core pointer (id=2).
        2. Reattaches all slave keyboards to Virtual core keyboard (id=3).
        3. Resets coordinate transformation matrices.
        4. Removes all custom master pairs.
        """
        logger.info("Resetting all XInput devices to Virtual core...")
        self.confiner.release_all()
        self.seat_keyboards.clear()
        self.seat_pointers.clear()
        self.seat_windows.clear()
        self.owns_seating = False
        self._verified_sig = None
        self._absent.clear()

        from core.device_manager import DeviceManager
        dm = DeviceManager()
        _, pointers, keyboards = dm.get_xinput_hierarchy()

        for p in pointers:
            if not p["is_xtest"]:
                self.reattach_device(p["id"], self.CORE_POINTER_ID)
                self.reset_pointer_matrix(p["id"])

        for k in keyboards:
            if not k["is_xtest"]:
                self.reattach_device(k["id"], self.CORE_KEYBOARD_ID)

        masters = self.get_existing_masters()
        for name, data in masters.items():
            if name != "Virtual core":
                self.remove_master_pair(name)

        logger.info("XInput reset complete.")
        return True

    # -----------------------------------------------------------------------
    # Apply Full Seating Configuration
    # -----------------------------------------------------------------------
    @staticmethod
    def find_conflicts(seats_config: List[Dict]) -> List[str]:
        """Detects the same mouse/keyboard/display assigned to more than one seat."""
        problems = []
        for key, label in (("mouse_id", "mouse"), ("keyboard_id", "keyboard"), ("display", "display")):
            seen = {}
            for s in seats_config:
                v = s.get(key)
                if v in (None, ""):
                    continue
                if v in seen:
                    problems.append(f"{label} {v!r} is assigned to both {seen[v]} and {s.get('id')}")
                seen[v] = s.get("id")
        return problems

    def apply_seating(
        self,
        seats_config: List[Dict],
        displays_info: Optional[List[Dict]] = None,
        total_screen: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, bool]:
        """
        Applies a multi-seat configuration:
        - Resolves live IDs for locked peripheral names so devices replugged into different ports are properly mapped.
        - Seat 1 is assigned to Virtual core pointer (2) & keyboard (3).
        - Seat 2 creates/uses a master pair and attaches assigned devices.
        - Each seat's master pointer is confined to its monitor with pointer barriers.
        """
        # Resolve live devices one-to-one (never trusts stale IDs, never shares a device)
        resolved_seats, assignments = self.resolve_live(seats_config)
        for (sid, cls), dev in assignments.items():
            logger.info(f"[Seat Resolver] {sid} {cls}: " + (f"{dev['name']} (ID {dev['id']})" if dev else "not connected"))
        seats_config = resolved_seats
        self.owns_seating = True
        self._verified_sig = None

        results = {}
        disp_map = {d["name"]: d for d in displays_info} if displays_info else {}

        for problem in self.find_conflicts(seats_config):
            logger.error(f"Seat config conflict: {problem}")
            results[f"conflict: {problem}"] = False

        for seat in seats_config:
            seat_id = seat.get("id", "seat1")
            is_primary = seat.get("is_primary", False)
            mouse_id = seat.get("mouse_id")
            keyboard_id = seat.get("keyboard_id")
            display_name = seat.get("display")

            logger.info(f"Configuring seat '{seat_id}' (primary={is_primary})...")

            if is_primary:
                master_ptr = self.CORE_POINTER_ID
                master_kbd = self.CORE_KEYBOARD_ID
            else:
                master_name = f"Seat_{seat_id.upper()}"
                ptr_id, kbd_id = self.create_master_pair(master_name)
                master_ptr = ptr_id or f"{master_name} pointer"
                master_kbd = kbd_id or f"{master_name} keyboard"

            if mouse_id:
                results[f"{seat_id}_mouse"] = self.attach_with_siblings(mouse_id, master_ptr, "pointer")
                if display_name:
                    self.map_pointer_to_output(mouse_id, display_name)

            if keyboard_id:
                results[f"{seat_id}_keyboard"] = self.attach_with_siblings(keyboard_id, master_kbd, "keyboard")

            if isinstance(master_ptr, int):
                self.seat_pointers[seat_id] = master_ptr
            if isinstance(master_kbd, int):
                self.seat_keyboards[seat_id] = master_kbd
                if not is_primary:
                    self.copy_keyboard_layout(master_kbd)

            # Confine the *master* pointer to this seat's monitor.
            if isinstance(master_ptr, int) and display_name in disp_map:
                d = disp_map[display_name]
                if d.get("width") and d.get("height"):
                    geom = (d["width"], d["height"], d["x"], d["y"])
                    results[f"{seat_id}_confined"] = self.confine_master(master_ptr, geom, total_screen)
            else:
                logger.warning(f"Seat '{seat_id}': display {display_name!r} not found or master unknown; not confined.")
                results[f"{seat_id}_confined"] = False

        return results

    SETTLE_SEC = 0.8        # topology must be unchanged this long before we touch anything
    RESEAT_COOLDOWN = 2.5   # never reseat the same seat device more often than this

    def check_and_reseat_hotplugged_devices(
        self,
        seats_config: List[Dict],
        displays_info: Optional[List[Dict]] = None,
        total_screen: Optional[Tuple[int, int]] = None,
    ) -> List[str]:
        """
        Stabilised hotplug handling. Cheap when nothing changed (one `xinput list`), and when
        something changed it waits for the topology to settle, resolves devices one-to-one,
        and only reattaches devices that are really on the wrong master. Unplugged devices are
        ignored (no stale-ID reattaches, no pointer warps, other seats untouched).
        Returns human-readable messages.
        """
        if not self.owns_seating:
            return []
        if not self._hotplug_lock.acquire(blocking=False):
            return []
        try:
            return self._reseat_if_needed(seats_config, displays_info, total_screen)
        except Exception as exc:
            logger.warning(f"Hotplug check failed: {exc}")
            return []
        finally:
            self._hotplug_lock.release()

    @staticmethod
    def _topology_sig(ptrs, kbds):
        return tuple(sorted((d["id"], d["name"], d.get("attachment"))
                            for d in ptrs + kbds if not d["is_xtest"]))

    def _sibling_nodes(self, dev_id: int, pool: List[Dict]) -> List[Dict]:
        parent = self._usb_parent(dev_id)
        if not parent:
            return []
        return [x for x in pool if x["id"] != dev_id and not x["is_xtest"] and self._usb_parent(x["id"]) == parent]

    def _reseat_if_needed(self, seats_config, displays_info, total_screen) -> List[str]:
        from core.device_manager import DeviceManager
        dm = DeviceManager()
        masters, ptrs, kbds = dm.get_xinput_hierarchy()
        if not masters and not ptrs and not kbds:
            return []  # xinput failed: never act on an empty reading
        sig = self._topology_sig(ptrs, kbds)
        now0 = time.time()
        if self._verified_sig is not None and now0 - self._last_refresh > 60 and self.memory.has_subs():
            self._last_refresh = now0          # stand-in clocks need a periodic look (promotion after 3 h)
            self._verified_sig = None
        if sig == self._verified_sig:
            return []

        time.sleep(self.SETTLE_SEC)  # let a plug / replug finish
        masters, ptrs, kbds = dm.get_xinput_hierarchy()
        if self._topology_sig(ptrs, kbds) != sig:
            return []  # still changing; the next tick re-evaluates
        if self._attempts.get(sig, 0) >= 3:
            self._verified_sig = sig  # give up on this exact state instead of looping
            return []

        resolved, assignments, events, spares = self.resolver.resolve(
            seats_config, ptrs, kbds, self._seat_masters(seats_config), self.memory)

        msgs: List[str] = self._handle_events(events)
        acted = deferred = False
        now = time.time()
        disp_map = {d["name"]: d for d in displays_info} if displays_info else None

        for idx, seat in enumerate(resolved):
            sid = seat.get("id", f"seat{idx + 1}")
            is_primary = seat.get("is_primary", False)
            disp_name = seat.get("display")
            for cls, label, name_key, pool in (("pointer", "Mouse", "mouse_name", ptrs),
                                               ("keyboard", "Keyboard", "keyboard_name", kbds)):
                dev = assignments.get((sid, cls))
                saved_name = seat.get(name_key) or ""
                if not dev:
                    if saved_name and (sid, cls) not in self._absent:
                        self._absent.add((sid, cls))
                        msgs.append(f"{label} '{saved_name}' of Seat '{sid}' unplugged - waiting for it "
                                    f"(other seats unaffected)")
                    continue
                if (sid, cls) in self._absent:
                    self._absent.discard((sid, cls))
                    msgs.append(f"{label} '{dev['name']}' of Seat '{sid}' reconnected")

                target = (self.CORE_POINTER_ID if cls == "pointer" else self.CORE_KEYBOARD_ID) if is_primary \
                    else (self.seat_pointers if cls == "pointer" else self.seat_keyboards).get(sid)
                if not isinstance(target, int) or target not in masters:
                    continue

                wrong_sibs = [x for x in self._sibling_nodes(dev["id"], pool) if x.get("attachment") != target]
                if dev.get("attachment") == target and not wrong_sibs:
                    continue
                if now - self._last_reseat.get((sid, cls), 0) < self.RESEAT_COOLDOWN:
                    deferred = True
                    continue

                if dev.get("attachment") != target:
                    self.reattach_device(dev["id"], target)
                for x in wrong_sibs:
                    self.reattach_device(x["id"], target)
                self._last_reseat[(sid, cls)] = now
                acted = True

                if cls == "keyboard" and not is_primary:
                    self.copy_keyboard_layout(target)
                    win_id = self.seat_windows.get(sid)
                    if win_id and not overlay_state.is_active(sid):
                        self.confiner.set_focus(target, win_id)
                if cls == "pointer":
                    if disp_name:
                        self.map_pointer_to_output(dev["id"], disp_name)
                    # Barriers belong to the master and survive a mouse replug: only rebuild a missing one.
                    if not is_primary and target not in self.confiner._barriers:
                        if disp_map is None:
                            disp_map = {d["name"]: d for d in dm.get_displays()[0]}
                        d = disp_map.get(disp_name)
                        if d and d.get("width") and d.get("height"):
                            self.confine_master(target, (d["width"], d["height"], d["x"], d["y"]), total_screen)
                msgs.append(f"{label} '{dev['name']}' (ID {dev['id']}) was on the wrong master -> "
                            f"reseated to Seat '{sid}'")

        # Spares: external devices parked on a seat master that no seat needs go back to the desktop master
        for cls, c in spares:
            key = ("spare", c["id"])
            if now - self._last_reseat.get(key, 0) < self.RESEAT_COOLDOWN:
                deferred = True
                continue
            self.reattach_device(c["id"], self.CORE_POINTER_ID if cls == "pointer" else self.CORE_KEYBOARD_ID)
            self._last_reseat[key] = now
            acted = True
            msgs.append(f"Spare {'mouse' if cls == 'pointer' else 'keyboard'} '{c['name']}' released from its seat "
                        f"(no seat needs it)")

        self.memory.save()
        if acted or deferred:
            self._verified_sig = None                       # re-verify once our own changes settle
            self._attempts[sig] = self._attempts.get(sig, 0) + 1
        else:
            self._verified_sig = sig
            self._attempts.clear()
        return msgs
