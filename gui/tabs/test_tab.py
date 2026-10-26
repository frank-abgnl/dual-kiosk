"""
Dual-Seat Live Test & Diagnostics Tab for XInput Pseudo-Seating Control Panel.
Provides simultaneous dual-seat typing test pads and a live kiosk process monitor.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
import threading


class TestTab(Gtk.Box):
    """Tab 4: Live simultaneous input testing and active process inspection."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.app = app
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # ── Section 1: Simultaneous Dual-Seat Typing Pad ──────────────────
        lbl_test = Gtk.Label()
        lbl_test.set_markup('<b>Simultaneous Multi-Pointer Live Test Pad</b>')
        lbl_test.set_halign(Gtk.Align.START)
        self.pack_start(lbl_test, False, False, 0)

        lbl_desc = Gtk.Label()
        lbl_desc.set_markup(
            '<span size="small" color="#5e5c64">'
            'Click Seat 1 box with Mouse 1 and Seat 2 box with Mouse 2. In MPX seating, both seats can type simultaneously!'
            '</span>'
        )
        lbl_desc.set_halign(Gtk.Align.START)
        self.pack_start(lbl_desc, False, False, 0)

        pads_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        pads_box.set_homogeneous(True)
        self.pack_start(pads_box, False, False, 0)

        # Seat 1 Test Box
        s1_frame = Gtk.Frame(label=" 🪑 Seat 1 Input Pad (Core Master) ")
        pads_box.pack_start(s1_frame, True, True, 0)
        s1_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        s1_box.set_margin_top(6)
        s1_box.set_margin_bottom(6)
        s1_box.set_margin_start(6)
        s1_box.set_margin_end(6)
        s1_frame.add(s1_box)

        self.s1_entry = Gtk.Entry()
        self.s1_entry.set_placeholder_text("Type here using Seat 1 keyboard...")
        s1_box.pack_start(self.s1_entry, False, False, 0)

        s1_sw = Gtk.ScrolledWindow()
        s1_sw.set_min_content_height(100)
        self.s1_tv = Gtk.TextView()
        s1_sw.add(self.s1_tv)
        s1_box.pack_start(s1_sw, True, True, 0)

        # Seat 2 Test Box
        s2_frame = Gtk.Frame(label=" 🪑 Seat 2 Input Pad (Seat2 Master) ")
        pads_box.pack_start(s2_frame, True, True, 0)
        s2_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        s2_box.set_margin_top(6)
        s2_box.set_margin_bottom(6)
        s2_box.set_margin_start(6)
        s2_box.set_margin_end(6)
        s2_frame.add(s2_box)

        self.s2_entry = Gtk.Entry()
        self.s2_entry.set_placeholder_text("Type here using Seat 2 keyboard...")
        s2_box.pack_start(self.s2_entry, False, False, 0)

        s2_sw = Gtk.ScrolledWindow()
        s2_sw.set_min_content_height(100)
        self.s2_tv = Gtk.TextView()
        s2_sw.add(self.s2_tv)
        s2_box.pack_start(s2_sw, True, True, 0)

        # ── Section 2: Active Kiosk Process Monitor ───────────────────────
        lbl_proc = Gtk.Label()
        lbl_proc.set_markup('<b>Active Kiosk Processes &amp; Window Locks</b>')
        lbl_proc.set_halign(Gtk.Align.START)
        self.pack_start(lbl_proc, False, False, 4)

        self.proc_store = Gtk.ListStore(str, str, str, str, str)  # Seat, PID, Window ID, URL, Status
        self.proc_tv = Gtk.TreeView(model=self.proc_store)
        self.proc_tv.set_headers_visible(True)

        for idx, title in enumerate(["Seat", "Process PID", "Window ID", "Kiosk URL", "Status"]):
            r = Gtk.CellRendererText()
            c = Gtk.TreeViewColumn(title, r, text=idx)
            c.set_resizable(True)
            c.set_min_width(90)
            self.proc_tv.append_column(c)

        sw_proc = Gtk.ScrolledWindow()
        sw_proc.set_min_content_height(140)
        sw_proc.add(self.proc_tv)
        self.pack_start(sw_proc, True, True, 0)

        # Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.pack_start(btn_box, False, False, 0)

        btn_refresh = Gtk.Button(label="🔄  Refresh Process Status")
        btn_refresh.connect("clicked", lambda _: self.refresh())
        btn_box.pack_start(btn_refresh, False, False, 0)

        btn_restart_s1 = Gtk.Button(label="🔄 Restart Seat 1 Kiosk")
        btn_restart_s1.connect("clicked", lambda _: self._restart_single("seat1"))
        btn_box.pack_start(btn_restart_s1, False, False, 0)

        btn_restart_s2 = Gtk.Button(label="🔄 Restart Seat 2 Kiosk")
        btn_restart_s2.connect("clicked", lambda _: self._restart_single("seat2"))
        btn_box.pack_start(btn_restart_s2, False, False, 0)

        self.refresh()
        GLib.timeout_add_seconds(3, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        self.refresh()
        return True

    def refresh(self):
        """Fetches status of running kiosks."""
        threading.Thread(target=self._fetch_status_bg, daemon=True).start()

    def _fetch_status_bg(self):
        status = self.app.kiosk_mgr.get_status()
        rows = []
        for seat_id, info in status.items():
            rows.append([
                seat_id.upper(),
                str(info.get("pid") or "Stopped"),
                str(info.get("window_id") or "N/A"),
                info.get("url") or "",
                "🟢 Running (Locked)" if info.get("running") else "🔴 Stopped",
            ])
        if not rows:
            rows.append(["(No kiosks active)", "—", "—", "—", "Offline"])

        GLib.idle_add(self._update_proc_ui, rows)

    def _update_proc_ui(self, rows):
        self.proc_store.clear()
        for r in rows:
            self.proc_store.append(r)

    def _restart_single(self, seat_id: str):
        cfg = self.app.config_mgr.load_config()
        seats = cfg.get("seats", [])
        seat_cfg = next((s for s in seats if s.get("id") == seat_id), {})
        url = seat_cfg.get("kiosk_url", "https://duckduckgo.com")
        mon_idx = 0 if seat_id == "seat1" else 1

        displays, _ = self.app.device_mgr.get_displays()
        geom = None
        if displays and len(displays) > mon_idx:
            d = displays[mon_idx]
            geom = (d["width"], d["height"], d["x"], d["y"])

        self.app.kiosk_mgr.launch_kiosk(seat_id, url, monitor_index=mon_idx, display_geometry=geom, enforce_lock=True)
        self.app.dashboard_tab.append_log(f"Restarted {seat_id} kiosk.")
        self.refresh()
