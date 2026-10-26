#!/usr/bin/env python3
"""
XInput Pseudo-Seating & Dual Firefox Kiosk Controller.
Entry point supporting both full GTK3 GUI and headless CLI execution.
"""

import sys
import os
import argparse
import logging

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("xinput_seating")


def run_cli(args):
    """Executes non-GUI command-line actions."""
    from core.device_manager import DeviceManager
    from core.xinput_seating import XInputSeatingEngine
    from core.kiosk_manager import KioskManager
    from core.config_manager import ConfigManager

    dev_mgr = DeviceManager()
    engine = XInputSeatingEngine()
    kiosk_mgr = KioskManager()
    cfg_mgr = ConfigManager()

    if args.auto:
        print("[*] Running hardware auto-detection...")
        cfg = cfg_mgr.auto_generate_config(dev_mgr)
        print(f"[✓] Auto-configuration saved to {cfg_mgr.config_path}:")
        for s in cfg.get("seats", []):
            print(f"    - {s['id']}: Display={s['display']}, Mouse={s['mouse_name']} (ID {s['mouse_id']}), Keyboard={s['keyboard_name']} (ID {s['keyboard_id']})")
        return

    if args.status:
        print("=== Displays ===")
        displays, total = dev_mgr.get_displays()
        for d in displays:
            print(f"  - {d['name']}: {d['geometry']} (Primary: {d['primary']})")
        print(f"  Total Canvas: {total['width']}x{total['height']}")

        print("\n=== XInput Masters ===")
        masters = engine.get_existing_masters()
        for name, data in masters.items():
            print(f"  - {name}: Pointer={data['pointer_id']}, Keyboard={data['keyboard_id']}")

        print("\n=== Running Kiosks ===")
        k_status = kiosk_mgr.get_status()
        if not k_status:
            print("  (None running)")
        for seat, st in k_status.items():
            print(f"  - {seat}: PID={st.get('pid')}, WinID={st.get('window_id')}, Running={st.get('running')}")
        return

    if args.stop:
        print("[*] Stopping all Firefox kiosks...")
        kiosk_mgr.stop_all_kiosks()
        print("[*] Resetting all XInput devices to Virtual core...")
        engine.reset_all_to_core()
        print("[✓] Pseudo-seating stopped and restored to single-seat core.")
        return

    if args.reset_devices:
        print("[*] Resetting XInput devices to Virtual core...")
        engine.reset_all_to_core()
        print("[✓] All devices reset.")
        return

    if args.start:
        print("[*] Loading seating configuration...")
        cfg = cfg_mgr.load_config()
        seats = cfg.get("seats", [])
        displays, total = dev_mgr.get_displays()

        print("[*] Applying XInput MPX master pairs and device reattachments...")
        results = engine.apply_seating(seats, displays, (total["width"], total["height"]))
        print(f"    Mapping results: {results}")

        print("[*] Launching dual Firefox kiosks (separate profiles, locked mode)...")
        s1_url = seats[0].get("kiosk_url", "https://duckduckgo.com") if len(seats) > 0 else "https://duckduckgo.com"
        s2_url = seats[1].get("kiosk_url", "https://www.google.com") if len(seats) > 1 else "https://www.google.com"

        ok = kiosk_mgr.launch_dual_kiosks(s1_url, s2_url, displays, enforce_lock=True)
        if ok:
            print("[✓] Dual Firefox kiosks launched successfully!")
            print("    Press Ctrl+C to stop kiosks and reset seating.")
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] Stopping kiosks and resetting devices...")
                kiosk_mgr.stop_all_kiosks()
                engine.reset_all_to_core()
                print("[✓] Cleanup complete.")
        else:
            print("[✗] Error launching one or more kiosks. Check system logs.")
        return


def main():
    parser = argparse.ArgumentParser(
        description="XInput Pseudo-Seating & Dual Firefox Kiosk Controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", action="store_true", help="Start seating mappings and launch dual Firefox kiosks (CLI)")
    parser.add_argument("--stop", action="store_true", help="Stop all kiosks and reset XInput devices to core (CLI)")
    parser.add_argument("--status", action="store_true", help="Show current displays, XInput masters, and kiosk status")
    parser.add_argument("--auto", action="store_true", help="Auto-detect hardware and generate seat configuration")
    parser.add_argument("--reset-devices", action="store_true", help="Reset all XInput devices to Virtual core")
    parser.add_argument("--gui", action="store_true", help="Launch full GTK3 Control Panel GUI (default)")

    args = parser.parse_args()

    # If any CLI action flag is given, run CLI
    if args.start or args.stop or args.status or args.auto or args.reset_devices:
        run_cli(args)
        return

    # Default: Run GTK3 GUI
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        from gui.app import XInputMultiseatApp

        app = XInputMultiseatApp()
        app.show_all()
        Gtk.main()
    except Exception as exc:
        logger.error(f"Failed to launch GTK GUI: {exc}")
        print("\nNote: Running in headless CLI mode fallback (--status):")
        args.status = True
        run_cli(args)


if __name__ == "__main__":
    main()
