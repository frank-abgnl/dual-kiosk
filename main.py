#!/usr/bin/env python3
"""
XInput Pseudo-Seating & Dual Firefox Kiosk Controller.
Entry point supporting both full GTK3 GUI and headless CLI execution.
Features automatic startup recovery after power outages, screen resolution
auto-identification, and connected USB device auto-assignment.
"""

import sys
import os
import argparse
import logging
import signal

# Enforce X11 backend for GDK on Wayland desktops
os.environ.setdefault("GDK_BACKEND", "x11")

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("xinput_seating")


def run_uninstall(args):
    """Full teardown: stop everything, reset XInput, remove configs/launchers."""
    from core.uninstaller import Uninstaller

    if not args.yes and not args.dry_run:
        print("This will:")
        print("  - terminate the control panel, supervisor and all Firefox kiosks")
        print("  - reattach all input devices to Virtual core and remove Seat masters")
        if args.keep_config:
            print("  - keep ~/.config/xinput-seats (--keep-config)")
        else:
            print("  - delete ~/.config/xinput-seats (config + Firefox kiosk profiles)")
        print("  - remove launcher / autostart entries")
        try:
            ans = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("Aborted.")
            return

    Uninstaller(keep_config=args.keep_config, dry_run=args.dry_run).run()


def run_cli(args):
    """Executes non-GUI command-line actions."""
    from core.device_manager import DeviceManager
    from core.xinput_seating import XInputSeatingEngine
    from core.kiosk_manager import KioskManager
    from core.config_manager import ConfigManager
    from core.autostart import (
        is_autostart_enabled,
        enable_autostart,
        disable_autostart,
        toggle_autostart,
        run_startup_reseat_and_launch,
    )

    from core.session_manager import session_mgr
    dev_mgr, engine, kiosk_mgr, cfg_mgr = session_mgr.dm, session_mgr.engine, session_mgr.kiosk, session_mgr.cfg_mgr

    if args.auto_start:
        # Automated boot / power outage recovery sequence
        print("[*] Running automatic boot & reseating sequence...")
        from launcher import launch
        rc = launch(args)
        if rc != 0:
            sys.exit(1)
        return

    if args.enable_autostart:
        ok = enable_autostart()
        if ok:
            print("[✓] Launch on startup successfully enabled (systemd user service & autostart desktop entry).")
        else:
            print("[✗] Failed to enable launch on startup.")
        return

    if args.disable_autostart:
        ok = disable_autostart()
        if ok:
            print("[✓] Launch on startup successfully disabled.")
        else:
            print("[✗] Failed to disable launch on startup.")
        return

    if args.toggle_autostart:
        new_state = toggle_autostart()
        print(f"[✓] Launch on startup is now: {'ENABLED' if new_state else 'DISABLED'}")
        return

    if args.auto:
        print("[*] Running hardware auto-detection (resolutions & USB input devices)...")
        cfg = cfg_mgr.auto_generate_config(dev_mgr)
        print(f"[✓] Auto-configuration saved to {cfg_mgr.config_path}:")
        for s in cfg.get("seats", []):
            print(
                f"    - {s['id']}: Display={s.get('display')} (Resolution: {s.get('resolution', 'unknown')}), "
                f"Mouse={s.get('mouse_name')} (ID {s.get('mouse_id')}), "
                f"Keyboard={s.get('keyboard_name')} (ID {s.get('keyboard_id')})"
            )
        return

    if args.status:
        print("=== Connected Displays & Resolutions ===")
        displays, total = dev_mgr.get_displays()
        for d in displays:
            p_flag = " [Primary]" if d.get("primary") else ""
            print(f"  - {d['name']}: {d['width']}x{d['height']} at offset +{d['x']}+{d['y']}{p_flag}")
        print(f"  Total Canvas: {total['width']}x{total['height']}")

        print("\n=== Connected USB Input Devices ===")
        usb_pointers = dev_mgr.get_usb_pointers()
        usb_keyboards = dev_mgr.get_usb_keyboards()
        if usb_pointers:
            print("  USB Mice / Pointers:")
            for p in usb_pointers:
                print(f"    - ID {p['id']}: {p['name']}")
        else:
            print("  USB Mice / Pointers: (none detected)")

        if usb_keyboards:
            print("  USB Keyboards:")
            for k in usb_keyboards:
                print(f"    - ID {k['id']}: {k['name']}")
        else:
            print("  USB Keyboards: (none detected)")

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

        print("\n=== Launch on Startup Status ===")
        autostart_on = is_autostart_enabled()
        print(f"  Power outage recovery / boot launch: {'ENABLED' if autostart_on else 'DISABLED'}")
        return

    if args.stop:
        print("[*] Stopping session, GUI, Firefox kiosks, and resetting XInput devices...")
        session_mgr.stop_session(exit_gui=True, on_event=lambda m: print(f"[*] {m}"))
        print("[✓] Pseudo-seating stopped and restored to single-seat core.")
        return

    if args.reset_devices:
        print("[*] Resetting XInput devices to Virtual core...")
        engine.reset_all_to_core()
        print("[✓] All devices reset.")
        return

    if args.start:
        print("[*] Starting dual-seat kiosk supervisor...")
        from launcher import launch
        rc = launch(args)
        if rc != 0:
            sys.exit(1)
        return


def main():
    parser = argparse.ArgumentParser(
        description="XInput Pseudo-Seating & Dual Firefox Kiosk Controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", action="store_true", help="Start seating mappings and launch dual Firefox kiosks (CLI)")
    parser.add_argument("--stop", action="store_true", help="Stop all kiosks and reset XInput devices to core (CLI)")
    parser.add_argument("--status", action="store_true", help="Show current displays, screen resolutions, USB devices, and kiosk status")
    parser.add_argument("--auto", action="store_true", help="Auto-detect hardware (resolutions & USB devices) and generate seat configuration")
    parser.add_argument("--reset-devices", action="store_true", help="Reset all XInput devices to Virtual core")
    parser.add_argument("--gui", action="store_true", help="Launch full GTK3 Control Panel GUI (default)")
    parser.add_argument("--auto-start", action="store_true", help="Launch kiosks and reseat automatically on boot / power outage recovery")
    parser.add_argument("--enable-autostart", "--install-service", action="store_true", help="Enable automatic launch on startup")
    parser.add_argument("--disable-autostart", action="store_true", help="Disable automatic launch on startup")
    parser.add_argument("--toggle-autostart", action="store_true", help="Toggle automatic launch on startup")
    parser.add_argument("--overlay", action="store_true", help="Launch Control Board GUI in overlay mode on top of kiosk mode")
    parser.add_argument("--seat", default="seat1", help="Target seat for overlay mode (default: seat1)")
    parser.add_argument("--toggle-overlay", action="store_true", help="Toggle Control Board overlay on target seat via IPC or launch")
    parser.add_argument("--restore-keymaps", action="store_true", help="Restore original XKB keymaps (re-enables Super key) and exit")
    parser.add_argument("--uninstall", action="store_true",
                        help="Terminate everything, reset XInput, and remove configs/profiles/launchers")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation prompt (with --uninstall)")
    parser.add_argument("--keep-config", action="store_true",
                        help="Keep ~/.config/xinput-seats when uninstalling")
    parser.add_argument("--dry-run", action="store_true", help="Show what --uninstall would do without changing anything")

    args = parser.parse_args()

    # Restore keymaps option
    if args.restore_keymaps:
        from core.kiosk_shield import restore_all_keymaps
        print("[*] Restoring original XKB keymaps...")
        restore_all_keymaps()
        print("[✓] Original keymaps restored.")
        return

    # Uninstall is handled first without constructing managers
    if args.uninstall:
        run_uninstall(args)
        return

    # If overlay or toggle-overlay requested, try IPC first
    if args.toggle_overlay:
        from core.hotkey_manager import send_overlay_command
        target = args.seat or "seat1"
        if send_overlay_command(f"TOGGLE {target}"):
            print(f"[✓] Sent TOGGLE overlay command to active Control Board GUI for '{target}'.")
            return
        args.overlay = True

    if args.overlay:
        from core.hotkey_manager import send_overlay_command
        target = args.seat or "seat1"
        if send_overlay_command(f"SHOW {target}"):
            print(f"[✓] Sent SHOW overlay command to active Control Board GUI for '{target}'.")
            return

    # If any CLI action flag is given, run CLI
    if (args.start or args.stop or args.status or args.auto or
            args.reset_devices or args.auto_start or args.enable_autostart or
            args.disable_autostart or args.toggle_autostart):
        if args.start or args.auto_start:
            from core.preflight import preflight
            pref = preflight()
            for w in pref.warnings:
                logger.warning(f"Preflight warning: {w}")
            if not pref.ok:
                for err in pref.errors:
                    logger.error(f"Preflight error: {err}")
                sys.exit(1)
        run_cli(args)
        return

    # Default: Run GTK3 GUI
    from core.preflight import preflight
    pref = preflight()
    for w in pref.warnings:
        logger.warning(f"Preflight warning: {w}")
    if not pref.ok:
        for err in pref.errors:
            logger.error(f"Preflight error: {err}")
        sys.exit(1)
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, GLib
        from gui.app import XInputMultiseatApp

        app = XInputMultiseatApp(overlay_mode=args.overlay, target_seat=args.seat or "seat1")
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, lambda *_: GLib.idle_add(app.destroy))
            except Exception:
                pass
        app.show_all()
        Gtk.main()
    except Exception as exc:
        logger.error(f"Failed to launch GTK GUI: {exc}")
        print("\nNote: Running in headless CLI mode fallback (--status):")
        args.status = True
        run_cli(args)


if __name__ == "__main__":
    main()
