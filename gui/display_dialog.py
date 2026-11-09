#!/usr/bin/env python3
"""
Display Error & Disconnect Warning Dialog with Retry Button.

Provides:
  1. Startup Error Mode:
     Shown when only 1 display is detected at launch.
     Blocks app start, explains that dual-seat kiosk requires 2 monitors,
     and provides a [Retry] button to re-ping displays and [Exit/Cancel].
     When a second display is detected on retry, automatically assigns it to a seat,
     and allows launch.
  2. Runtime Disconnect Warning Mode:
     Shown while kiosks are running if a display is suddenly disconnected.
     Notifies the user that a display was lost and seat is paused,
     stays on top of the active kiosk (whitelisted with overlay_state),
     and provides a [Retry] button to re-ping after physical adjustment.
     When device is found, automatically resumes and launches the second screen.
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

# Ensure project root is in sys.path
PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from core import overlay_state
from core.device_manager import DeviceManager

logger = logging.getLogger("xinput_seating.display_dialog")

DIALOG_CSS = b"""
window.display-dialog {
    background-color: #0f172a;
    border: 2px solid #334155;
    border-radius: 12px;
}
.dialog-header {
    padding: 16px;
    border-bottom: 1px solid #1e293b;
}
.dialog-body {
    padding: 20px;
}
.dialog-footer {
    padding: 14px 20px;
    background-color: #0b1120;
    border-top: 1px solid #1e293b;
}
.title-error {
    font-size: 18px;
    font-weight: bold;
    color: #f87171;
}
.title-warning {
    font-size: 18px;
    font-weight: bold;
    color: #fbbf24;
}
.dialog-text {
    font-size: 13px;
    color: #cbd5e1;
}
.status-box {
    background-color: #1e293b;
    border-radius: 8px;
    padding: 10px 14px;
    border-left: 4px solid #38bdf8;
}
.status-text {
    font-size: 12px;
    color: #94a3b8;
    font-family: monospace;
}
.btn-retry {
    background-color: #16a34a;
    color: #ffffff;
    font-weight: bold;
    border-radius: 8px;
    padding: 10px 18px;
    border: none;
}
.btn-retry:hover {
    background-color: #15803d;
}
.btn-cancel {
    background-color: #334155;
    color: #f1f5f9;
    border-radius: 8px;
    padding: 10px 16px;
    border: none;
}
.btn-cancel:hover {
    background-color: #475569;
}
"""


class DisplayDialog(Gtk.Window):
    """
    Display Error / Warning Dialog with Retry Button.
    """

    def __init__(
        self,
        mode: str = "error",  # 'error' (startup single display) or 'warning' (runtime disconnect)
        active_display: str = "",
        disconnected_display: str = "",
        seat_id: str = "seat2",
        on_success_callback=None,
    ):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.mode = mode
        self.active_display = active_display
        self.disconnected_display = disconnected_display
        self.seat_id = seat_id
        self.on_success_callback = on_success_callback
        self.dm = DeviceManager()
        self.success_displays: Optional[List[Dict]] = None

        self._apply_styles()
        self._build_ui()

        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_default_size(560, 360)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(False)

        # Heartbeat for overlay whitelist so KioskShield never pushes this dialog below kiosk
        self._hb_id = None
        self._auto_check_id = None

        self.connect("destroy", self._on_destroy)
        self.connect("realize", self._on_realize)

    def _apply_styles(self):
        self.get_style_context().add_class("display-dialog")
        provider = Gtk.CssProvider()
        provider.load_from_data(DIALOG_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        # ── Header ────────────────────────────────────────────────────────
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        header_box.get_style_context().add_class("dialog-header")

        icon_name = "dialog-error" if self.mode == "error" else "dialog-warning"
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
        header_box.pack_start(icon, False, False, 0)

        vhead = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if self.mode == "error":
            self.set_title("Display Detection Error - Dual Kiosk")
            lbl_title = Gtk.Label(label="Display Detection Error: Only One Display Connected")
            lbl_title.get_style_context().add_class("title-error")
            lbl_title.set_halign(Gtk.Align.START)
            lbl_sub = Gtk.Label(label="Dual-seat kiosk mode requires at least two connected displays.")
            lbl_sub.set_halign(Gtk.Align.START)
            lbl_sub.get_style_context().add_class("dialog-text")
            vhead.pack_start(lbl_title, False, False, 0)
            vhead.pack_start(lbl_sub, False, False, 0)
        else:
            self.set_title("Display Disconnected Warning - Dual Kiosk")
            lbl_title = Gtk.Label(label=f"Warning: Display '{self.disconnected_display or 'Secondary'}' Disconnected")
            lbl_title.get_style_context().add_class("title-warning")
            lbl_title.set_halign(Gtk.Align.START)
            lbl_sub = Gtk.Label(label=f"Seat '{self.seat_id}' is temporarily suspended to prevent cross-seat interference.")
            lbl_sub.set_halign(Gtk.Align.START)
            lbl_sub.get_style_context().add_class("dialog-text")
            vhead.pack_start(lbl_title, False, False, 0)
            vhead.pack_start(lbl_sub, False, False, 0)

        header_box.pack_start(vhead, True, True, 0)
        root.pack_start(header_box, False, False, 0)

        # ── Body Content ──────────────────────────────────────────────────
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body_box.get_style_context().add_class("dialog-body")

        if self.mode == "error":
            body_text = (
                f"Currently connected display: <b>{self.active_display or 'Primary'}</b>\n\n"
                "The application <b>will not start</b> until a second monitor is connected and detected.\n\n"
                "<b>Instructions:</b>\n"
                "1. Connect the second monitor cable (HDMI, DisplayPort, VGA) to the computer.\n"
                "2. Ensure the display is powered ON and set to the correct video input.\n"
                "3. Click <b>Retry Display Detection</b> below to re-ping and launch the dual-seat session."
            )
        else:
            body_text = (
                f"Active display: <b>{self.active_display or 'Seat 1 Display'}</b>\n"
                f"Disconnected display: <b>{self.disconnected_display or 'Secondary Screen'}</b>\n\n"
                f"Seat <b>{self.seat_id}</b>'s browser was closed and its input devices isolated "
                "so they do not interfere with the active seat.\n\n"
                "<b>Instructions:</b>\n"
                "Adjust any physically adjustable aspects (cables, adapters, power), then click "
                "<b>Retry &amp; Launch Second Screen</b> to re-ping and restore the second display."
            )

        lbl_body = Gtk.Label()
        lbl_body.set_markup(body_text)
        lbl_body.set_line_wrap(True)
        lbl_body.set_halign(Gtk.Align.START)
        body_box.pack_start(lbl_body, False, False, 0)

        # Status feedback box
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_box.get_style_context().add_class("status-box")
        self.lbl_status = Gtk.Label(label="Status: Waiting for hardware adjustment. Click Retry to re-ping.")
        self.lbl_status.set_halign(Gtk.Align.START)
        self.lbl_status.get_style_context().add_class("status-text")
        status_box.pack_start(self.lbl_status, True, True, 0)
        body_box.pack_start(status_box, False, False, 0)

        root.pack_start(body_box, True, True, 0)

        # ── Footer / Buttons ──────────────────────────────────────────────
        footer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        footer_box.get_style_context().add_class("dialog-footer")

        cancel_label = "❌  Exit Launcher" if self.mode == "error" else "✖  Dismiss Warning"
        self.btn_cancel = Gtk.Button(label=cancel_label)
        self.btn_cancel.get_style_context().add_class("btn-cancel")
        self.btn_cancel.connect("clicked", self._on_cancel_clicked)
        footer_box.pack_start(self.btn_cancel, False, False, 0)

        retry_label = "🔄  Retry Display Detection" if self.mode == "error" else "🔄  Retry & Launch Second Screen"
        self.btn_retry = Gtk.Button(label=retry_label)
        self.btn_retry.get_style_context().add_class("btn-retry")
        self.btn_retry.connect("clicked", self._on_retry_clicked)
        footer_box.pack_end(self.btn_retry, False, False, 0)

        root.pack_start(footer_box, False, False, 0)

    def _on_realize(self, _widget):
        """Keep on top and whitelist in overlay_state so KioskShield allows it."""
        try:
            gdk_win = self.get_window()
            if gdk_win:
                xid = gdk_win.get_xid()
                self.set_keep_above(True)
                # Register in overlay whitelist
                overlay_state.mark_active("dialog", xid=xid, pid=os.getpid())
                subprocess.run(["wmctrl", "-i", "-r", hex(xid), "-b", "add,above"], capture_output=True)
                subprocess.run(["wmctrl", "-i", "-a", hex(xid)], capture_output=True)
        except Exception as exc:
            logger.debug(f"Window realize hints notice: {exc}")

        # Start periodic whitelist refresh and background auto-check
        self._hb_id = GLib.timeout_add_seconds(2, self._heartbeat)
        self._auto_check_id = GLib.timeout_add_seconds(3, self._auto_check_tick)

    def _heartbeat(self):
        try:
            gdk_win = self.get_window()
            xid = gdk_win.get_xid() if gdk_win else None
            overlay_state.mark_active("dialog", xid=xid, pid=os.getpid())
        except Exception:
            pass
        return True

    def _auto_check_tick(self):
        """Background poll: if user plugged in the monitor, auto-detect it."""
        try:
            active_displays, _ = self.dm.probe_and_activate_displays()
            if len(active_displays) >= 2:
                logger.info("Auto-check detected 2 displays! Triggering success.")
                self._handle_success(active_displays)
                return False
        except Exception:
            pass
        return True

    def _on_retry_clicked(self, _btn):
        """Re-pings displays immediately and evaluates whether 2 displays are present."""
        ts = time.strftime("%H:%M:%S")
        self.btn_retry.set_sensitive(False)
        self.lbl_status.set_text(f"Status [{ts}]: Re-pinging displays via xrandr/DRM...")

        # Process events so label updates immediately
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        def _do_probe():
            active_displays, _ = self.dm.probe_and_activate_displays()
            GLib.idle_add(self._process_probe_result, active_displays)

        import threading
        threading.Thread(target=_do_probe, daemon=True).start()

    def _process_probe_result(self, active_displays: List[Dict]):
        ts = time.strftime("%H:%M:%S")
        self.btn_retry.set_sensitive(True)

        if len(active_displays) >= 2:
            self._handle_success(active_displays)
        else:
            names = ", ".join(d["name"] for d in active_displays) or "none"
            self.lbl_status.set_text(
                f"⚠ [{ts}] Re-ping found {len(active_displays)} display ({names}). Second display not detected yet. "
                "Please check cable/power."
            )

    def _handle_success(self, active_displays: List[Dict]):
        names = ", ".join(d["name"] for d in active_displays)
        self.success_displays = active_displays
        self.lbl_status.set_text(f"✓ Found {len(active_displays)} displays ({names})! Automatically assigning to seat...")

        if self.on_success_callback:
            try:
                self.on_success_callback(active_displays)
            except Exception as exc:
                logger.error(f"Error in on_success_callback: {exc}")

        # Close dialog after brief success notice
        GLib.timeout_add(600, self._close_and_quit, 0)

    def _on_cancel_clicked(self, _btn):
        self._close_and_quit(1 if self.mode == "error" else 0)

    def _close_and_quit(self, exit_code: int = 0):
        self.destroy()
        if Gtk.main_level() > 0:
            Gtk.main_quit()
        return False

    def _on_destroy(self, _widget):
        if self._hb_id:
            GLib.source_remove(self._hb_id)
            self._hb_id = None
        if self._auto_check_id:
            GLib.source_remove(self._auto_check_id)
            self._auto_check_id = None
        overlay_state.clear("dialog")


def run_single_display_error_modal(active_displays: List[Dict]) -> Tuple[bool, Optional[List[Dict]]]:
    """
    Shows modal error dialog when only one display is detected at launch.
    Blocks until user clicks Retry (and 2 displays are found) or Cancel.
    Returns (True, new_active_displays) on success, or (False, None) if cancelled.
    """
    disp_name = active_displays[0]["name"] if active_displays else "Unknown"
    dlg = DisplayDialog(mode="error", active_display=disp_name)
    dlg.present()
    dlg.show_all()
    Gtk.main()

    if dlg.success_displays and len(dlg.success_displays) >= 2:
        return True, dlg.success_displays
    return False, None


def main():
    parser = argparse.ArgumentParser(description="Display Error / Warning Dialog with Retry")
    parser.add_argument("--mode", choices=["error", "warning"], default="error",
                        help="Dialog mode: 'error' (startup single display) or 'warning' (runtime disconnect)")
    parser.add_argument("--active", default="", help="Name of currently active display (e.g. VGA-1)")
    parser.add_argument("--disconnected", default="", help="Name of disconnected display (e.g. DP-1)")
    parser.add_argument("--seat", default="seat2", help="Seat ID affected by disconnect (default: seat2)")
    args = parser.parse_args()

    dm = DeviceManager()
    active_displays = [d for d in dm.get_displays()[0] if d.get("width", 0) > 0]
    active_name = args.active or (active_displays[0]["name"] if active_displays else "")

    dlg = DisplayDialog(
        mode=args.mode,
        active_display=active_name,
        disconnected_display=args.disconnected,
        seat_id=args.seat,
    )
    dlg.connect("destroy", lambda _: Gtk.main_quit())
    dlg.present()
    dlg.show_all()
    Gtk.main()

    if dlg.success_displays and len(dlg.success_displays) >= 2:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
