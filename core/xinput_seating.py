#!/usr/bin/env python3
"""
XInput MPX Seating Engine.
Handles master pointer/keyboard creation, reattachment of slave devices,
and coordinate transformation matrix calculations to confine mice to monitors.
"""

import subprocess
import re
import logging
from typing import Dict, List, Optional, Tuple

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
        pass

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
        ptr_name = f"{seat_name} pointer"
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
    # Screen Confinement & Coordinate Transformation Matrix
    # -----------------------------------------------------------------------
    def map_pointer_to_output(
        self,
        pointer_id: int,
        output_name: str,
        geometry: Optional[Tuple[int, int, int, int]] = None,
        total_screen: Optional[Tuple[int, int]] = None,
    ) -> bool:
        """
        Maps a pointer device to a specific monitor output.
        Uses `xinput map-to-output` and calculates Coordinate Transformation Matrix.
        geometry: (width, height, x_offset, y_offset)
        total_screen: (total_width, total_height)
        """
        # 1. Native map-to-output
        cmd = ["xinput", "map-to-output", str(pointer_id), output_name]
        logger.info(f"Mapping pointer {pointer_id} to output '{output_name}'")
        _, err, rc = run_command(cmd)

        # 2. Precision Coordinate Transformation Matrix if geometry is known
        if geometry and total_screen:
            w, h, x, y = geometry
            total_w, total_h = total_screen
            if total_w > 0 and total_h > 0:
                sx = round(w / total_w, 6)
                sy = round(h / total_h, 6)
                ox = round(x / total_w, 6)
                oy = round(y / total_h, 6)

                matrix_str = f"{sx} 0 {ox} 0 {sy} oy 0 0 1".replace("oy", str(oy))
                matrix_args = [
                    "xinput", "set-prop", str(pointer_id),
                    "--type=float", "Coordinate Transformation Matrix",
                    str(sx), "0", str(ox),
                    "0", str(sy), str(oy),
                    "0", "0", "1"
                ]
                logger.info(f"Applying transformation matrix to pointer {pointer_id}: {matrix_str}")
                _, m_err, m_rc = run_command(matrix_args)
                if m_rc != 0:
                    logger.warning(f"Coordinate Transformation Matrix warning: {m_err}")

        return rc == 0

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
        1. Reattaches all slave pointers to Virtual core pointer (id=2).
        2. Reattaches all slave keyboards to Virtual core keyboard (id=3).
        3. Resets coordinate transformation matrices.
        4. Removes all custom master pairs.
        """
        logger.info("Resetting all XInput devices to Virtual core...")
        from core.device_manager import DeviceManager
        dm = DeviceManager()
        _, pointers, keyboards = dm.get_xinput_hierarchy()

        # Reattach pointers
        for p in pointers:
            if not p["is_xtest"]:
                self.reattach_device(p["id"], self.CORE_POINTER_ID)
                self.reset_pointer_matrix(p["id"])

        # Reattach keyboards
        for k in keyboards:
            if not k["is_xtest"]:
                self.reattach_device(k["id"], self.CORE_KEYBOARD_ID)

        # Remove custom masters
        masters = self.get_existing_masters()
        for name, data in masters.items():
            if name != "Virtual core":
                self.remove_master_pair(name)

        logger.info("XInput reset complete.")
        return True

    # -----------------------------------------------------------------------
    # Apply Full Seating Configuration
    # -----------------------------------------------------------------------
    def apply_seating(
        self,
        seats_config: List[Dict],
        displays_info: Optional[List[Dict]] = None,
        total_screen: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, bool]:
        """
        Applies a multi-seat configuration:
        - Seat 1 is assigned to Virtual core pointer (2) & keyboard (3).
        - Seat 2 creates/uses master pair 'Seat2' and attaches assigned devices.
        - Maps mouse pointers to monitors with boundary confinement.
        """
        results = {}
        disp_map = {d["name"]: d for d in displays_info} if displays_info else {}

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
                # Secondary seat: create master pair
                master_name = f"Seat_{seat_id.upper()}"
                ptr_id, kbd_id = self.create_master_pair(master_name)
                master_ptr = ptr_id or f"{master_name} pointer"
                master_kbd = kbd_id or f"{master_name} keyboard"

            # Reattach mouse
            if mouse_id:
                ok_m = self.reattach_device(mouse_id, master_ptr)
                # Map to output if display provided
                if display_name and display_name in disp_map:
                    d_info = disp_map[display_name]
                    geom = (d_info["width"], d_info["height"], d_info["x"], d_info["y"])
                    self.map_pointer_to_output(mouse_id, display_name, geom, total_screen)
                elif display_name:
                    self.map_pointer_to_output(mouse_id, display_name)
                results[f"{seat_id}_mouse"] = ok_m

            # Reattach keyboard
            if keyboard_id:
                ok_k = self.reattach_device(keyboard_id, master_kbd)
                results[f"{seat_id}_keyboard"] = ok_k

        return results
