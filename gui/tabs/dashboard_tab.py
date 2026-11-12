"""
Dashboard Tab for XInput Pseudo-Seating Control Panel.
Shows real-time status cards, quick action buttons, and live activity log.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
import threading
import time
from gui.widgets import StatusCard


class DashboardTab(Gtk.Box):
    """Tab 1: Status cards, control toolbar, and live seating event logs."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.app = app
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # ── Status Cards ──────────────────────────────────────────────────
        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cards_box.set_homogeneous(True)
        self.pack_start(cards_box, False, False, 0)

        self.card_seats = StatusCard("Configured Seats")
        self.card_masters = StatusCard("XInput Master Pairs")
        self.card_displays = StatusCard("Connected Monitors")
        self.card_kiosks = StatusCard("Kiosk Browsers")

        for c in (self.card_seats, self.card_masters, self.card_displays, self.card_kiosks):
            cards_box.pack_start(c, True, True, 0)

        # ── Action Toolbar ────────────────────────────────────────────────
        tb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.pack_start(tb, False, False, 4)

        btn_start = Gtk.Button(label="🟢  Start Seating & Kiosks")
        btn_start.get_style_context().add_class("btn-success")
        btn_start.connect("clicked", self._on_start_all)
        tb.pack_start(btn_start, False, False, 0)

        btn_stop = Gtk.Button(label="🔴  Stop & Reset All")
        btn_stop.get_style_context().add_class("btn-danger")
        btn_stop.connect("clicked", self._on_stop_all)
        tb.pack_start(btn_stop, False, False, 0)

        btn_refresh = Gtk.Button(label="🔄  Refresh")
        btn_refresh.connect("clicked", lambda _: self.refresh_metrics())
        tb.pack_start(btn_refresh, False, False, 0)

        btn_kiosk = Gtk.Button(label="🌐  Restart Kiosks")
        btn_kiosk.connect("clicked", self._on_restart_kiosks)
        tb.pack_start(btn_kiosk, False, False, 0)

        # Launch on Startup Toggle (Power Outage Recovery)
        from core.autostart import is_autostart_enabled, enable_autostart, disable_autostart
        self.btn_autostart = Gtk.ToggleButton(label="⚡  Launch on Startup")
        self.btn_autostart.set_tooltip_text("Automatically reseat displays and USB inputs and launch kiosks on system power recovery")
        self._updating_autostart_ui = False
        self.btn_autostart.connect("toggled", self._on_toggle_autostart_btn)
        tb.pack_start(self.btn_autostart, False, False, 0)

        btn_clear = Gtk.Button(label="🧹  Clear Log")
        btn_clear.connect("clicked", lambda _: self.log_buf.set_text(""))
        tb.pack_end(btn_clear, False, False, 0)

        # ── Live Activity Log View ────────────────────────────────────────
        lbl_log = Gtk.Label()
        lbl_log.set_markup('<b>Activity &amp; Kiosk Supervisor Log</b>')
        lbl_log.set_halign(Gtk.Align.START)
        self.pack_start(lbl_log, False, False, 0)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(250)
        self.pack_start(sw, True, True, 0)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_cursor_visible(False)
        self.log_view.get_style_context().add_class("log-view")
        self.log_buf = self.log_view.get_buffer()
        sw.add(self.log_view)

        # Log initial event
        self.append_log("System initialized. Ready.")
        self.append_log("Shortcut: Press Ctrl+Shift+M+S in kiosk mode to overlay control panel on your screen.")

        # Auto-refresh loop
        self.refresh_metrics()
        GLib.timeout_add_seconds(3, self._auto_refresh_tick)

    # -----------------------------------------------------------------------
    # Logging Helper
    # -----------------------------------------------------------------------
    def append_log(self, message: str):
        """Thread-safe logging to the dashboard text buffer."""
        def _append():
            ts = time.strftime("%H:%M:%S")
            end_iter = self.log_buf.get_end_iter()
            self.log_buf.insert(end_iter, f"[{ts}] {message}\n")
            end_iter = self.log_buf.get_end_iter()
            self.log_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 1.0)
        GLib.idle_add(_append)

    # -----------------------------------------------------------------------
    # Periodic Refresh
    # -----------------------------------------------------------------------
    def _auto_refresh_tick(self):
        self.refresh_metrics()
        return True

    def refresh_metrics(self):
        """Asynchronously updates the dashboard metrics."""
        threading.Thread(target=self._fetch_metrics_bg, daemon=True).start()

    def _fetch_metrics_bg(self):
        try:
            # Displays
            displays, _ = self.app.device_mgr.get_displays()
            num_displays = len(displays)

            # Master pairs
            masters = self.app.seating_engine.get_existing_masters()
            num_masters = len(masters)

            # Kiosks
            kiosk_status = self.app.kiosk_mgr.get_status()
            running_kiosks = sum(1 for s in kiosk_status.values() if s.get("running"))

            # Configured seats
            cfg = self.app.config_mgr.load_config()
            num_seats = len(cfg.get("seats", []))

            # Autostart status
            from core.autostart import is_autostart_enabled
            autostart_on = is_autostart_enabled()

            GLib.idle_add(self._update_ui_cards, num_seats, num_masters, num_displays, running_kiosks, autostart_on)
        except Exception:
            pass

    def _update_ui_cards(self, seats, masters, displays, kiosks, autostart_on=False):
        self.card_seats.set_value(f"{seats} Seats", "good" if seats >= 2 else "warn")
        self.card_masters.set_value(f"{masters} Active", "good" if masters >= 2 else "neutral")
        self.card_displays.set_value(f"{displays} Detected", "good" if displays >= 2 else "warn")
        self.card_kiosks.set_value(f"{kiosks} Running", "good" if kiosks >= 2 else "warn" if kiosks == 1 else "neutral")

        # Update autostart button without triggering toggle signal
        self._updating_autostart_ui = True
        self.btn_autostart.set_active(autostart_on)
        if autostart_on:
            self.btn_autostart.set_label("⚡  Startup Launch: ON")
            self.btn_autostart.get_style_context().remove_class("btn-danger")
            self.btn_autostart.get_style_context().add_class("btn-success")
        else:
            self.btn_autostart.set_label("⚡  Startup Launch: OFF")
            self.btn_autostart.get_style_context().remove_class("btn-success")
        self._updating_autostart_ui = False

    def _on_toggle_autostart_btn(self, btn):
        if self._updating_autostart_ui:
            return
        from core.autostart import enable_autostart, disable_autostart
        new_state = btn.get_active()
        if new_state:
            ok = enable_autostart()
            if ok:
                self.append_log("Launch on startup enabled (Power Outage Recovery mode).")
                self.app.show_info("Startup launch enabled ✓ System will auto-reseat and launch on boot.", 0)
                btn.set_label("⚡  Startup Launch: ON")
                btn.get_style_context().add_class("btn-success")
            else:
                self.append_log("Failed to enable launch on startup.")
                self.app.show_info("Failed to enable launch on startup.", 1)
                self._updating_autostart_ui = True
                btn.set_active(False)
                self._updating_autostart_ui = False
        else:
            ok = disable_autostart()
            if ok:
                self.append_log("Launch on startup disabled.")
                self.app.show_info("Startup launch disabled.", 0)
                btn.set_label("⚡  Startup Launch: OFF")
                btn.get_style_context().remove_class("btn-success")
            else:
                self.append_log("Failed to disable launch on startup.")
                self.app.show_info("Failed to disable launch on startup.", 1)
                self._updating_autostart_ui = True
                btn.set_active(True)
                self._updating_autostart_ui = False

    def _show_display_dialog_and_retry(self):
        from gui.display_dialog import DisplayDialog
        dlg = DisplayDialog(
            mode="error",
            on_success_callback=lambda _: self._on_start_all(None),
        )
        dlg.set_transient_for(self.app)
        dlg.show_all()

    # -----------------------------------------------------------------------
    # Toolbar Actions
    # -----------------------------------------------------------------------
    def _on_start_all(self, _btn):
        self.append_log("Starting pseudo-seating and launching dual Firefox kiosks...")
        self.app.show_info("Checking display hardware and applying seat mappings...", 0)

        def _worker():
            from core.session_manager import session_mgr, StartResult
            res = session_mgr.start_session(on_event=lambda m: self.append_log(m), allow_partial=True)
            if res.ok:
                if self.app.hotkey_mgr:
                    GLib.idle_add(self.app.hotkey_mgr.update_seat_keyboards, self.app.seating_engine.seat_keyboards)
                self.append_log("Dual Firefox kiosks launched successfully.")
                self.append_log("⚡ Super Shortcut active: Press Ctrl + Shift + M + S on any seat to overlay control panel on your screen.")
                GLib.idle_add(self.app.show_info, "Pseudo-seating active! Dual Firefox kiosks running (Ctrl+Shift+M+S to overlay).", 0)
            elif res.code == StartResult.NEED_DISPLAYS:
                self.append_log(f"Start notice: {res.message}")
                GLib.idle_add(self._show_display_dialog_and_retry)
            else:
                self.append_log(f"Start notice: {res.message}")
                GLib.idle_add(self.app.show_info, res.message, 1)

            GLib.idle_add(self.refresh_metrics)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_stop_all(self, _btn):
        self.append_log("Stopping all kiosks, background supervisors, and resetting XInput devices...")
        self.app.show_info("Stopping kiosks and resetting devices...", 0)

        def _worker():
            from core.session_manager import session_mgr
            session_mgr.stop_session(exit_gui=False, on_event=lambda m: self.append_log(m))
            GLib.idle_add(self.app.show_info, "All seats stopped. System restored to single-seat core.", 0)
            GLib.idle_add(self.refresh_metrics)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_restart_kiosks(self, _btn):
        self.append_log("Restarting Firefox kiosks...")
        def _worker():
            from core.session_manager import session_mgr
            session_mgr.restart_session(on_event=lambda m: self.append_log(m), allow_partial=True)
            self.append_log("Dual kiosks restarted.")
            GLib.idle_add(self.refresh_metrics)
        threading.Thread(target=_worker, daemon=True).start()
