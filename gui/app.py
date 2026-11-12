"""
Main Application Window for XInput Pseudo-Seating Control Panel.
GTK3 Application Window with HeaderBar, InfoBar, Status Cards, and Modular Tabs.
Supports Super Keyboard Shortcut (Ctrl+Shift+M+S) overlay on top of kiosk mode.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, GdkX11

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from core import overlay_state
from core.session_manager import session_mgr, get_supervisor_pid
from core.hotkey_manager import OverlayIPCServer, get_seat_screen_geometry
from core.logging_setup import setup_logging
from gui.styles import CSS_STYLES
from gui.tabs import DashboardTab, SeatsTab, DevicesTab, TestTab, ConfigTab

setup_logging()
logger = logging.getLogger("xinput_seating.app")


class XInputMultiseatApp(Gtk.ApplicationWindow):
    """Main window for XInput Pseudo-Seating Control Panel with overlay mode support."""

    def __init__(self, overlay_mode: bool = False, target_seat: str = "seat1", application=None):
        super().__init__(title="XInput Pseudo-Seating Control Panel", application=application)
        self.set_default_size(960, 680)
        self.set_position(Gtk.WindowPosition.CENTER)

        # ── Overlay State ─────────────────────────────────────────────────
        self.is_overlay_active: bool = False
        self.active_overlay_seat: Optional[str] = None
        self._overlay_mode_standalone: bool = overlay_mode
        self._overlay_hb_id = None
        self._overlay_xid = None
        self._hotplug_timer = None
        self._hotplug_busy = False
        self._shutting_down = False
        # Set to True if opening the GUI should also start the kiosks automatically.
        self._auto_start_session = False

        # ── Core Engines (Single Owner: session_mgr) ──────────────────────
        self.device_mgr = session_mgr.dm
        self.seating_engine = session_mgr.engine
        self.kiosk_mgr = session_mgr.kiosk
        self.config_mgr = session_mgr.cfg_mgr

        # When running as client of supervisor, discover existing masters
        if get_supervisor_pid():
            try:
                self.seating_engine.discover_masters(self.config_mgr.load_config().get("seats", []))
            except Exception as exc:
                logger.debug(f"Master discovery notice: {exc}")

        # Discover already running kiosks (if any)
        self.kiosk_mgr.discover_running_kiosks()

        # ── Apply CSS Styles ──────────────────────────────────────────────
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS_STYLES)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # ── HeaderBar ─────────────────────────────────────────────────────
        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_title("XInput Pseudo-Seating Control Panel")
        hb.set_subtitle("Multi-Pointer X (MPX) Dual Kiosk Manager")
        self.set_titlebar(hb)

        btn_wizard = Gtk.Button(label="🪄  Auto-Setup Wizard")
        btn_wizard.connect("clicked", lambda _: self._show_setup_wizard())
        hb.pack_start(btn_wizard)

        btn_header_refresh = Gtk.Button(label="🔄")
        btn_header_refresh.set_tooltip_text("Refresh All Status & Devices")
        btn_header_refresh.connect("clicked", lambda _: self.refresh_all())
        hb.pack_end(btn_header_refresh)

        # Overlay Controls in HeaderBar
        self.btn_hide_overlay = Gtk.Button(label="⬇  Hide Overlay")
        self.btn_hide_overlay.set_tooltip_text("Hide control board and return to kiosk mode (or press Ctrl+Shift+M+S or Esc)")
        self.btn_hide_overlay.get_style_context().add_class("btn-danger")
        self.btn_hide_overlay.connect("clicked", lambda _: self.hide_overlay())
        self.btn_hide_overlay.set_no_show_all(True)
        hb.pack_end(self.btn_hide_overlay)

        self.btn_quit_app = Gtk.Button(label="🛑  Stop & Exit App")
        self.btn_quit_app.get_style_context().add_class("btn-danger")
        self.btn_quit_app.set_tooltip_text("Terminate supervisor and all kiosks, restore desktop input, and close application")
        self.btn_quit_app.connect("clicked", lambda _: self._confirm_and_quit_app())
        hb.pack_end(self.btn_quit_app)

        self.lbl_overlay_badge = Gtk.Label(label="")
        self.lbl_overlay_badge.set_no_show_all(True)
        hb.pack_end(self.lbl_overlay_badge)

        # ── Root Layout & InfoBar ─────────────────────────────────────────
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        self._infobar_timer = None
        self.infobar = Gtk.InfoBar()
        self.infobar.set_show_close_button(True)
        self.infobar.set_no_show_all(True)
        self.infobar_lbl = Gtk.Label(label="")
        self.infobar_lbl.set_line_wrap(True)
        self.infobar.get_content_area().add(self.infobar_lbl)
        self.infobar.connect("response", lambda ib, _: ib.hide())
        root.pack_start(self.infobar, False, False, 0)

        # ── Notebook Navigation ───────────────────────────────────────────
        self.notebook = Gtk.Notebook()
        self.notebook.set_tab_pos(Gtk.PositionType.TOP)
        root.pack_start(self.notebook, True, True, 0)

        # Tab 1: Dashboard
        self.dashboard_tab = DashboardTab(self)
        self.notebook.append_page(self.dashboard_tab, Gtk.Label(label="📊  Dashboard"))

        # Tab 2: Seats
        self.seats_tab = SeatsTab(self)
        self.notebook.append_page(self.seats_tab, Gtk.Label(label="🪑  Seats & Kiosks"))

        # Tab 3: Devices
        self.devices_tab = DevicesTab(self)
        self.notebook.append_page(self.devices_tab, Gtk.Label(label="🖥  Devices"))

        # Tab 4: Live Test
        self.test_tab = TestTab(self)
        self.notebook.append_page(self.test_tab, Gtk.Label(label="🧪  Live Test"))

        # Tab 5: Config
        self.config_tab = ConfigTab(self)
        self.notebook.append_page(self.config_tab, Gtk.Label(label="⚙  Config & Profiles"))

        # ── Global Hotkey & IPC Services ──────────────────────────────────
        self.hotkey_mgr = None

        self.ipc_server = OverlayIPCServer(on_command=self._on_ipc_command)
        self.ipc_server.start()

        # Periodic hotplug monitoring in GUI only when running standalone with no supervisor
        if not get_supervisor_pid():
            GLib.timeout_add_seconds(3, self._periodic_hotplug_check)

        # Connect window events
        self.connect("destroy", self._on_window_destroy)
        self.connect("delete-event", self._on_delete_event)
        self.connect("key-press-event", self._on_key_press)

        # Bind GUI startup with Desktop/Kiosk session startup
        if overlay_mode:
            if not session_mgr.is_session_active():
                GLib.idle_add(self._auto_start_kiosks_on_launch, True, target_seat)
            else:
                GLib.idle_add(self.show_overlay, target_seat)
        elif self._auto_start_session:
            GLib.idle_add(self._auto_start_kiosks_on_launch, False, target_seat)

    def _show_display_dialog_and_retry(self, show_overlay_after: bool = False, target_seat: str = "seat1"):
        from gui.display_dialog import DisplayDialog
        dlg = DisplayDialog(
            mode="error",
            on_success_callback=lambda _: self._auto_start_kiosks_on_launch(show_overlay_after, target_seat),
        )
        dlg.set_transient_for(self)
        dlg.show_all()

    def _auto_start_kiosks_on_launch(self, show_overlay_after: bool = False, target_seat: str = "seat1"):
        """Starts the desktop seating & kiosk session automatically when the GUI starts if not already active."""
        if not session_mgr.is_session_active():
            self.dashboard_tab.append_log("Auto-starting seating and dual Firefox kiosks with GUI startup...")
            def _worker():
                res = session_mgr.start_session(on_event=lambda m: self.dashboard_tab.append_log(m), allow_partial=True)
                if res.ok:
                    if self.hotkey_mgr:
                        GLib.idle_add(self.hotkey_mgr.update_seat_keyboards, self.seating_engine.seat_keyboards)
                    GLib.idle_add(self.show_info, "Pseudo-seating active! Dual Firefox kiosks running.", 0)
                    if show_overlay_after:
                        GLib.idle_add(self.show_overlay, target_seat)
                    else:
                        GLib.idle_add(self.iconify)
                elif res.code == "NEED_DISPLAYS":
                    GLib.idle_add(self._show_display_dialog_and_retry, show_overlay_after, target_seat)
                else:
                    GLib.idle_add(self.show_info, res.message, 1)
                GLib.idle_add(self.refresh_all)
            threading.Thread(target=_worker, daemon=True).start()
        else:
            self._ensure_masters()
            if self.hotkey_mgr and self.seating_engine.seat_keyboards:
                self.hotkey_mgr.update_seat_keyboards(self.seating_engine.seat_keyboards)
            self.refresh_all()
        return False

    # -----------------------------------------------------------------------
    # Global Hotkey & Overlay IPC Handlers
    # -----------------------------------------------------------------------
    def _on_ipc_command(self, cmd_line: str):
        """Dispatches commands arriving via Unix domain socket."""
        parts = cmd_line.strip().split()
        if not parts:
            return
        cmd = parts[0].upper()
        seat_id = parts[1] if len(parts) > 1 else "seat1"

        if cmd == "SHOW":
            GLib.idle_add(self.show_overlay, seat_id)
        elif cmd == "HIDE":
            GLib.idle_add(self.hide_overlay)
        elif cmd == "TOGGLE":
            GLib.idle_add(self.toggle_overlay, seat_id)
        elif cmd in ("STOP", "QUIT", "CLOSE"):
            GLib.idle_add(self._on_ipc_stop)

    def _on_ipc_stop(self):
        self._shutting_down = True
        logger.info("Received IPC STOP/QUIT command. Closing Control Board...")
        self.destroy()
        if Gtk.main_level() > 0:
            Gtk.main_quit()

    def _on_hotkey_triggered(self, seat_id: str, _master_id: int, _slave_id: int):
        """Callback from background XInput2 listener when Ctrl+Shift+M+S is hit."""
        logger.info(f"Hotkey Ctrl+Shift+M+S detected on seat '{seat_id}'! Toggling overlay...")
        GLib.idle_add(self.toggle_overlay, seat_id)

    def _on_xinput_hierarchy_changed(self):
        """Device plugged / unplugged (called from the hotkey listener thread)."""
        GLib.idle_add(self._schedule_hotplug_recheck)

    def _schedule_hotplug_recheck(self):
        """Debounce: a plug event is often followed by several more; check once they stop."""
        if self._hotplug_timer:
            GLib.source_remove(self._hotplug_timer)
        self._hotplug_timer = GLib.timeout_add(900, self._run_hotplug_recheck)
        return False

    def _periodic_hotplug_check(self):
        """Safety net; cheap because the engine returns early when nothing changed."""
        self._run_hotplug_recheck()
        return True

    def _run_hotplug_recheck(self):
        self._hotplug_timer = None
        if self.seating_engine.owns_seating and not self._hotplug_busy:
            self._hotplug_busy = True
            threading.Thread(target=self._hotplug_worker, daemon=True).start()
        return False

    def _hotplug_worker(self):
        """Runs xinput work off the GTK main thread (it used to freeze the UI and the pointers)."""
        try:
            seats = self.config_mgr.load_config().get("seats", [])
            msgs = self.seating_engine.check_and_reseat_hotplugged_devices(seats)
            for msg in msgs:
                logger.info(f"[Hotplug] {msg}")
                self.dashboard_tab.append_log(f"⚡ {msg}")
            if msgs:
                GLib.idle_add(self.show_info, msgs[-1], 0)
                GLib.idle_add(self.seats_tab.refresh)
                GLib.idle_add(self.dashboard_tab.refresh_metrics)
        except Exception as exc:
            logger.debug(f"Hotplug check notice: {exc}")
        finally:
            self._hotplug_busy = False

    def _on_key_press(self, _widget, event):
        """Keyboard shortcut handler inside the GUI window."""
        if event.keyval == Gdk.KEY_Escape and self.is_overlay_active:
            self.hide_overlay()
            return True
        return False

    def _confirm_and_quit_app(self):
        """Asks user confirmation to stop kiosks, supervisor, and close the Control Board."""
        if session_mgr.is_session_active():
            dlg = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.NONE,
                text="Stop All Kiosks & Exit Application?",
            )
            dlg.format_secondary_text(
                "This will terminate all running kiosk browsers, stop the background supervisor, "
                "reattach all input devices to the normal desktop, and close the Control Board."
            )
            dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
            btn_stop = dlg.add_button("🛑  Stop All & Exit", Gtk.ResponseType.OK)
            btn_stop.get_style_context().add_class("btn-danger")
            resp = dlg.run()
            dlg.destroy()
            if resp != Gtk.ResponseType.OK:
                return

        self._stop_and_exit_app()

    def _stop_and_exit_app(self):
        """Unified shutdown: stops supervisor, kiosks, devices, and closes GUI."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Executing unified shutdown from Control Board...")
        session_mgr.stop_session(exit_gui=True)
        self.destroy()
        if Gtk.main_level() > 0:
            Gtk.main_quit()

    def _on_delete_event(self, _widget, _event):
        """
        When kiosks are active, prompt the user whether to hide the overlay,
        stop kiosks and exit completely, or cancel.
        """
        if session_mgr.is_session_active():
            dlg = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text="Kiosk Session Active",
            )
            dlg.format_secondary_text(
                "Dual Firefox kiosks are currently running.\n\n"
                "What would you like to do?"
            )
            dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dlg.add_button("⬇  Hide to Kiosk", 1)
            btn_stop = dlg.add_button("🛑  Stop Kiosks & Exit", 2)
            btn_stop.get_style_context().add_class("btn-danger")

            resp = dlg.run()
            dlg.destroy()

            if resp == 1:
                self.hide_overlay()
                return True
            elif resp == 2:
                self._stop_and_exit_app()
                return False
            else:
                return True  # Cancelled
        return False  # Allow clean shutdown when no kiosks running

    # -----------------------------------------------------------------------
    # Overlay Mode Presentation & Seat Focus
    # -----------------------------------------------------------------------
    def _overlay_heartbeat(self):
        """Keeps the cross-process whitelist fresh while the overlay is open."""
        if not self.is_overlay_active or not self.active_overlay_seat:
            self._overlay_hb_id = None
            return False
        overlay_state.mark_active(self.active_overlay_seat, xid=self._overlay_xid, pid=os.getpid())
        return True

    def show_overlay(self, seat_id: str = "seat1"):
        """Overlays the Control Board on top of kiosk mode with maximum priority."""
        logger.info(f"Presenting Control Board overlay for seat '{seat_id}'...")

        # Switching seats: release the previous one first
        prev = self.active_overlay_seat
        if prev and prev != seat_id:
            self.kiosk_mgr.restore_kiosk(prev)

        self.is_overlay_active = True
        self.active_overlay_seat = seat_id

        self._ensure_masters()
        if self.hotkey_mgr and self.seating_engine.seat_keyboards:
            self.hotkey_mgr.update_seat_keyboards(self.seating_engine.seat_keyboards)

        # 1. Whitelist FIRST (shared file -> also pauses a watchdog in another process)
        self.kiosk_mgr.set_overlay_seat(seat_id)
        if not self._overlay_hb_id:
            self._overlay_hb_id = GLib.timeout_add_seconds(2, self._overlay_heartbeat)

        # 2. Push this seat's kiosk down
        self.kiosk_mgr.demote_kiosk(seat_id)

        cfg = self.config_mgr.load_config()
        seats = cfg.get("seats", [])
        displays, _ = self.device_mgr.get_displays()
        x, y, w, h, d_name = get_seat_screen_geometry(seat_id, seats, displays)

        win_w = min(960, max(600, w - 40))
        win_h = min(680, max(450, h - 50))
        pos_x = x + (w - win_w) // 2
        pos_y = y + (h - win_h) // 2

        self.set_keep_above(True)
        self.resize(win_w, win_h)
        self.move(pos_x, pos_y)
        self.deiconify()
        self.present()
        self.show_all()

        self.btn_hide_overlay.show()
        badge_text = f"  🖥 {seat_id.upper()} OVERLAY ({d_name})  "
        self.lbl_overlay_badge.set_markup(
            f"<span background='#1e3a8a' color='#f8fafc' weight='bold' font_family='sans'>{badge_text}</span>"
        )
        self.lbl_overlay_badge.show()

        # Wait briefly so the window is mapped before applying WM hints / focus
        GLib.timeout_add(150, self._apply_overlay_wm_and_focus, seat_id, pos_x, pos_y, win_w, win_h)

    def _ensure_masters(self):
        """When seating was applied by the launcher/autostart process, learn its master IDs."""
        if not self.seating_engine.seat_pointers:
            try:
                self.seating_engine.discover_masters(self.config_mgr.load_config().get("seats", []))
            except Exception as exc:
                logger.debug(f"Master discovery notice: {exc}")

    def _bind_overlay_input(self, seat_id: str, xid_hex: str):
        self._ensure_masters()
        ptr_id = self.seating_engine.seat_pointers.get(seat_id)
        kbd_id = self.seating_engine.seat_keyboards.get(seat_id)
        if ptr_id:
            self.seating_engine.confiner.set_client_pointer(xid_hex, ptr_id)
        if kbd_id:
            self.seating_engine.confiner.set_focus(kbd_id, xid_hex)

    def _apply_overlay_wm_and_focus(self, seat_id: str, pos_x: int, pos_y: int, win_w: int, win_h: int):
        """Raises the overlay above the kiosk and binds the seat's master pointer/keyboard to it."""
        if not self.is_overlay_active:
            return False
        gdk_win = self.get_window()
        if not gdk_win:
            return False

        try:
            self._overlay_xid = gdk_win.get_xid()
            xid_hex = hex(self._overlay_xid)
            overlay_state.mark_active(seat_id, xid=self._overlay_xid, pid=os.getpid())  # shield: allow this window
            subprocess.run(["wmctrl", "-i", "-r", xid_hex, "-b", "add,above"], capture_output=True)
            subprocess.run(["wmctrl", "-i", "-r", xid_hex, "-e", f"0,{pos_x},{pos_y},{win_w},{win_h}"], capture_output=True)
            subprocess.run(["wmctrl", "-i", "-a", xid_hex], capture_output=True)
            subprocess.run(["xdotool", "windowraise", xid_hex], capture_output=True)
            self._bind_overlay_input(seat_id, xid_hex)

            # Verify stacking shortly after; escalate if the kiosk is still on top
            GLib.timeout_add(350, self._verify_overlay_on_top, seat_id, xid_hex)
        except Exception as exc:
            logger.warning(f"Error applying overlay WM hints: {exc}")

        self.show_info(
            f"⚡ Control Board overlay active on {seat_id.upper()}. Press Ctrl+Shift+M+S or Esc to return to Kiosk.",
            0,
        )
        return False

    def _verify_overlay_on_top(self, seat_id: str, xid_hex: str):
        if not self.is_overlay_active or self.active_overlay_seat != seat_id:
            return False
        try:
            if not self.kiosk_mgr.is_stacked_above_kiosk(xid_hex, seat_id):
                logger.info("Kiosk still stacked over overlay; un-fullscreening kiosk temporarily.")
                self.kiosk_mgr.force_unfullscreen_kiosk(seat_id)
                subprocess.run(["wmctrl", "-i", "-r", xid_hex, "-b", "add,above"], capture_output=True)
                subprocess.run(["wmctrl", "-i", "-a", xid_hex], capture_output=True)
                subprocess.run(["xdotool", "windowraise", xid_hex], capture_output=True)
            self._bind_overlay_input(seat_id, xid_hex)
        except Exception as exc:
            logger.debug(f"Overlay verify notice: {exc}")
        return False

    def hide_overlay(self):
        """Hides the Control Board, restores the kiosk and re-enables the watchdog."""
        if not self.is_overlay_active and not self.get_visible():
            return

        prev_seat = self.active_overlay_seat
        logger.info(f"Dismissing Control Board overlay (was active on '{prev_seat}').")
        self.is_overlay_active = False
        self.active_overlay_seat = None

        self.btn_hide_overlay.hide()
        self.lbl_overlay_badge.hide()
        self.set_keep_above(False)

        if prev_seat:
            self.kiosk_mgr.restore_kiosk(prev_seat)

            win_id = self.kiosk_mgr.window_ids.get(prev_seat)
            if not win_id:
                self.kiosk_mgr.discover_running_kiosks()
                win_id = self.kiosk_mgr.window_ids.get(prev_seat)

            kbd_id = self.seating_engine.seat_keyboards.get(prev_seat)
            ptr_id = self.seating_engine.seat_pointers.get(prev_seat)
            if win_id:
                if ptr_id:
                    self.seating_engine.confiner.set_client_pointer(win_id, ptr_id)
                if kbd_id:
                    self.seating_engine.confiner.set_focus(kbd_id, win_id)

        # Un-whitelist last, so the watchdog resumes only after the kiosk is restored
        self.kiosk_mgr.set_overlay_seat(None)
        if prev_seat:
            overlay_state.clear(prev_seat)

        if self._overlay_mode_standalone:
            self.hide()
        else:
            self.iconify()

    def toggle_overlay(self, seat_id: str = "seat1"):
        """Toggles overlay visibility on the designated seat."""
        if self.is_overlay_active and self.get_visible() and self.active_overlay_seat == seat_id:
            self.hide_overlay()
        else:
            self.show_overlay(seat_id)

    # -----------------------------------------------------------------------
    # InfoBar Notification Helper
    # -----------------------------------------------------------------------
    def show_info(self, message: str, rc: int = 0):
        """Displays a banner notification in the top InfoBar."""
        if not isinstance(message, str):
            return
        msg_type = Gtk.MessageType.INFO if rc == 0 else Gtk.MessageType.ERROR
        self.infobar.set_message_type(msg_type)
        self.infobar_lbl.set_text(message)
        self.infobar.show()
        self.infobar_lbl.show()

        if self._infobar_timer:
            GLib.source_remove(self._infobar_timer)
        self._infobar_timer = GLib.timeout_add_seconds(6, self._hide_infobar)

    def _hide_infobar(self):
        self.infobar.hide()
        self._infobar_timer = None
        return False

    # -----------------------------------------------------------------------
    # Refresh All
    # -----------------------------------------------------------------------
    def refresh_all(self):
        """Refreshes all tabs simultaneously."""
        self.dashboard_tab.refresh_metrics()
        self.seats_tab.refresh()
        self.devices_tab.refresh()
        self.test_tab.refresh()

    # -----------------------------------------------------------------------
    # Setup Wizard Dialog
    # -----------------------------------------------------------------------
    def _show_setup_wizard(self):
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text="Welcome to XInput Pseudo-Seating",
        )
        dlg.format_secondary_text(
            "This program enables independent dual-seat workstations on a single PC using "
            "Multi-Pointer X (MPX) and isolated Firefox kiosk sessions.\n\n"
            "Would you like to run Auto-Detection now to automatically detect your monitors, "
            "mice, and keyboards?"
        )
        dlg.add_buttons(
            "Manual Setup", Gtk.ResponseType.CANCEL,
            "Run Auto-Detection", Gtk.ResponseType.OK,
        )
        resp = dlg.run()
        dlg.destroy()

        if resp == Gtk.ResponseType.OK:
            self.seats_tab._on_auto_detect(None)
            self.refresh_all()

    # -----------------------------------------------------------------------
    # Clean Shutdown
    # -----------------------------------------------------------------------
    def _on_window_destroy(self, _widget):
        logger.info("Window closing...")
        overlay_state.clear()  # never leave a seat whitelisted after the GUI exits
        if hasattr(self, 'hotkey_mgr') and self.hotkey_mgr:
            self.hotkey_mgr.stop()
        if hasattr(self, 'ipc_server') and self.ipc_server:
            self.ipc_server.stop()
        if not self._shutting_down:
            self._shutting_down = True
            if get_supervisor_pid():
                logger.info("Supervisor is running in background; closing GUI without stopping kiosks.")
            else:
                logger.info("No background supervisor; stopping kiosks and resetting input on GUI close.")
                session_mgr.stop_session(exit_gui=False)
        if Gtk.main_level() > 0:
            Gtk.main_quit()
