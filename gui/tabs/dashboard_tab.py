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

            GLib.idle_add(self._update_ui_cards, num_seats, num_masters, num_displays, running_kiosks)
        except Exception as exc:
            pass

    def _update_ui_cards(self, seats, masters, displays, kiosks):
        self.card_seats.set_value(f"{seats} Seats", "good" if seats >= 2 else "warn")
        self.card_masters.set_value(f"{masters} Active", "good" if masters >= 2 else "neutral")
        self.card_displays.set_value(f"{displays} Detected", "good" if displays >= 2 else "warn")
        self.card_kiosks.set_value(f"{kiosks} Running", "good" if kiosks >= 2 else "warn" if kiosks == 1 else "neutral")

    # -----------------------------------------------------------------------
    # Toolbar Actions
    # -----------------------------------------------------------------------
    def _on_start_all(self, _btn):
        self.append_log("Starting pseudo-seating and launching dual Firefox kiosks...")
        self.app.show_info("Applying seat mappings and launching kiosks...", 0)

        def _worker():
            cfg = self.app.config_mgr.load_config()
            seats = cfg.get("seats", [])
            displays, total_screen = self.app.device_mgr.get_displays()

            # 1. Apply XInput seat configuration
            self.append_log("Applying XInput MPX master pairs & device reattachments...")
            results = self.app.seating_engine.apply_seating(seats, displays, (total_screen["width"], total_screen["height"]))
            self.append_log(f"XInput mapping results: {results}")

            # 2. Launch Firefox kiosks
            self.append_log("Launching dual Firefox kiosks with isolated profiles...")
            s1_url = seats[0].get("kiosk_url", "https://duckduckgo.com") if len(seats) > 0 else "https://duckduckgo.com"
            s2_url = seats[1].get("kiosk_url", "https://www.google.com") if len(seats) > 1 else "https://www.google.com"

            ok = self.app.kiosk_mgr.launch_dual_kiosks(
                seat1_url=s1_url,
                seat2_url=s2_url,
                displays_info=displays,
                enforce_lock=True,
            )

            if ok:
                self.append_log("Dual Firefox kiosks launched successfully in kiosk mode.")
                GLib.idle_add(self.app.show_info, "Pseudo-seating active! Dual Firefox kiosks running.", 0)
            else:
                self.append_log("Warning: One or more Firefox kiosks failed to launch.")
                GLib.idle_add(self.app.show_info, "Check logs: Firefox launch issue.", 1)

            GLib.idle_add(self.refresh_metrics)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_stop_all(self, _btn):
        self.append_log("Stopping all kiosks and resetting XInput devices...")
        self.app.show_info("Stopping kiosks and resetting devices...", 0)

        def _worker():
            # 1. Stop kiosks
            self.app.kiosk_mgr.stop_all_kiosks()
            self.append_log("All Firefox kiosk instances terminated.")

            # 2. Reset XInput
            self.app.seating_engine.reset_all_to_core()
            self.append_log("All pointers/keyboards reattached to Virtual core.")

            GLib.idle_add(self.app.show_info, "All seats stopped. System restored to single-seat core.", 0)
            GLib.idle_add(self.refresh_metrics)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_restart_kiosks(self, _btn):
        self.append_log("Restarting Firefox kiosks...")
        def _worker():
            cfg = self.app.config_mgr.load_config()
            seats = cfg.get("seats", [])
            displays, _ = self.app.device_mgr.get_displays()
            s1_url = seats[0].get("kiosk_url", "https://duckduckgo.com") if len(seats) > 0 else "https://duckduckgo.com"
            s2_url = seats[1].get("kiosk_url", "https://www.google.com") if len(seats) > 1 else "https://www.google.com"
            self.app.kiosk_mgr.stop_all_kiosks()
            time.sleep(0.5)
            self.app.kiosk_mgr.launch_dual_kiosks(s1_url, s2_url, displays, enforce_lock=True)
            self.append_log("Dual kiosks restarted.")
            GLib.idle_add(self.refresh_metrics)
        threading.Thread(target=_worker, daemon=True).start()
