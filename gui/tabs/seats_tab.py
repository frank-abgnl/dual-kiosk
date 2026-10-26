"""
Seats Configuration Tab for XInput Pseudo-Seating Control Panel.
Provides configuration cards for Seat 1 and Seat 2 (monitors, mice, keyboards, Firefox profiles).
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
import threading


class SeatCard(Gtk.Frame):
    """Configuration UI for an individual seat."""

    def __init__(self, seat_index: int, is_primary: bool, app):
        super().__init__()
        self.seat_index = seat_index
        self.is_primary = is_primary
        self.app = app
        self.seat_id = f"seat{seat_index + 1}"

        self.get_style_context().add_class("seat-card")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(main_box)

        # Header with title and badge
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        main_box.pack_start(hdr, False, False, 0)

        lbl_title = Gtk.Label()
        role_desc = "Primary (Core Master)" if is_primary else "Secondary (MPX Master)"
        lbl_title.set_markup(f'<b>Seat {seat_index + 1}</b>  <span size="small" color="#77767b">({role_desc})</span>')
        hdr.pack_start(lbl_title, False, False, 0)

        self.status_badge = Gtk.Label(label="Configured")
        self.status_badge.get_style_context().add_class("badge-active")
        hdr.pack_end(self.status_badge, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(8)
        main_box.pack_start(grid, False, False, 0)

        # Row 0: Target Monitor
        lbl_disp = Gtk.Label(label="Target Monitor:")
        lbl_disp.set_halign(Gtk.Align.END)
        grid.attach(lbl_disp, 0, 0, 1, 1)

        self.combo_disp = Gtk.ComboBoxText()
        grid.attach(self.combo_disp, 1, 0, 1, 1)

        # Row 1: Mouse
        lbl_mouse = Gtk.Label(label="Assigned Mouse:")
        lbl_mouse.set_halign(Gtk.Align.END)
        grid.attach(lbl_mouse, 0, 1, 1, 1)

        self.combo_mouse = Gtk.ComboBoxText()
        grid.attach(self.combo_mouse, 1, 1, 1, 1)

        # Row 2: Keyboard
        lbl_kbd = Gtk.Label(label="Assigned Keyboard:")
        lbl_kbd.set_halign(Gtk.Align.END)
        grid.attach(lbl_kbd, 0, 2, 1, 1)

        self.combo_kbd = Gtk.ComboBoxText()
        grid.attach(self.combo_kbd, 1, 2, 1, 1)

        # Row 3: Firefox Profile
        lbl_prof = Gtk.Label(label="Firefox Profile:")
        lbl_prof.set_halign(Gtk.Align.END)
        grid.attach(lbl_prof, 0, 3, 1, 1)

        self.ent_profile = Gtk.Entry()
        self.ent_profile.set_text(self.seat_id)
        grid.attach(self.ent_profile, 1, 3, 1, 1)

        # Row 4: Kiosk Homepage URL
        lbl_url = Gtk.Label(label="Kiosk Home URL:")
        lbl_url.set_halign(Gtk.Align.END)
        grid.attach(lbl_url, 0, 4, 1, 1)

        self.ent_url = Gtk.Entry()
        default_url = "https://duckduckgo.com" if is_primary else "https://www.google.com"
        self.ent_url.set_text(default_url)
        grid.attach(self.ent_url, 1, 4, 1, 1)

        # Row 5: Options
        opts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        grid.attach(opts_box, 1, 5, 1, 1)

        self.chk_lock = Gtk.CheckButton(label="Lock Kiosk Mode (Non-minimizable / Non-closable)")
        self.chk_lock.set_active(True)
        opts_box.pack_start(self.chk_lock, False, False, 0)

        self.chk_respawn = Gtk.CheckButton(label="Auto-Respawn Supervisor")
        self.chk_respawn.set_active(True)
        opts_box.pack_start(self.chk_respawn, False, False, 0)

    def populate_dropdowns(self, displays, pointers, keyboards, current_cfg):
        """Populates combo boxes with current detected hardware and selects active config."""
        # 1. Displays
        self.combo_disp.remove_all()
        selected_disp = current_cfg.get("display", "")
        active_disp_idx = 0
        for idx, d in enumerate(displays):
            self.combo_disp.append_text(f"{d['name']} ({d['geometry']})")
            if d['name'] == selected_disp:
                active_disp_idx = idx
        if displays:
            self.combo_disp.set_active(active_disp_idx)

        # 2. Mice
        self.combo_mouse.remove_all()
        selected_mouse_id = current_cfg.get("mouse_id")
        active_mouse_idx = 0
        for idx, p in enumerate(pointers):
            self.combo_mouse.append_text(f"[ID {p['id']}] {p['name']}")
            if p['id'] == selected_mouse_id:
                active_mouse_idx = idx
        if pointers:
            self.combo_mouse.set_active(active_mouse_idx)

        # 3. Keyboards
        self.combo_kbd.remove_all()
        selected_kbd_id = current_cfg.get("keyboard_id")
        active_kbd_idx = 0
        for idx, k in enumerate(keyboards):
            self.combo_kbd.append_text(f"[ID {k['id']}] {k['name']}")
            if k['id'] == selected_kbd_id:
                active_kbd_idx = idx
        if keyboards:
            self.combo_kbd.set_active(active_kbd_idx)

        # 4. Text entries
        if "profile_name" in current_cfg:
            self.ent_profile.set_text(current_cfg["profile_name"])
        if "kiosk_url" in current_cfg:
            self.ent_url.set_text(current_cfg["kiosk_url"])
        if "lock_kiosk" in current_cfg:
            self.chk_lock.set_active(current_cfg["lock_kiosk"])
        if "auto_respawn" in current_cfg:
            self.chk_respawn.set_active(current_cfg["auto_respawn"])

    def get_configured_data(self, displays, pointers, keyboards) -> dict:
        """Extracts the user configuration from the widgets."""
        disp_idx = self.combo_disp.get_active()
        disp_name = displays[disp_idx]["name"] if disp_idx >= 0 and disp_idx < len(displays) else ""

        mouse_idx = self.combo_mouse.get_active()
        mouse_id = pointers[mouse_idx]["id"] if mouse_idx >= 0 and mouse_idx < len(pointers) else None
        mouse_name = pointers[mouse_idx]["name"] if mouse_idx >= 0 and mouse_idx < len(pointers) else ""

        kbd_idx = self.combo_kbd.get_active()
        kbd_id = keyboards[kbd_idx]["id"] if kbd_idx >= 0 and kbd_idx < len(keyboards) else None
        kbd_name = keyboards[kbd_idx]["name"] if kbd_idx >= 0 and kbd_idx < len(keyboards) else ""

        return {
            "id": self.seat_id,
            "name": f"Seat {self.seat_index + 1} ({'Primary' if self.is_primary else 'Secondary'})",
            "is_primary": self.is_primary,
            "display": disp_name,
            "mouse_id": mouse_id,
            "mouse_name": mouse_name,
            "keyboard_id": kbd_id,
            "keyboard_name": kbd_name,
            "profile_name": self.ent_profile.get_text().strip() or self.seat_id,
            "kiosk_url": self.ent_url.get_text().strip() or "https://duckduckgo.com",
            "lock_kiosk": self.chk_lock.get_active(),
            "auto_respawn": self.chk_respawn.get_active(),
        }


class SeatsTab(Gtk.Box):
    """Tab 2: Per-seat setup and assignment."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app = app
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        self._cached_displays = []
        self._cached_pointers = []
        self._cached_keyboards = []

        # Top Bar
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.pack_start(top, False, False, 0)

        lbl = Gtk.Label()
        lbl.set_markup('<b>Seat Assignment &amp; Configuration</b>')
        top.pack_start(lbl, False, False, 0)

        top.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 4)

        btn_auto = Gtk.Button(label="⚡  Auto-Detect & Assign")
        btn_auto.connect("clicked", self._on_auto_detect)
        top.pack_start(btn_auto, False, False, 0)

        btn_save = Gtk.Button(label="💾  Save Configuration")
        btn_save.get_style_context().add_class("btn-primary")
        btn_save.connect("clicked", self._on_save_config)
        top.pack_start(btn_save, False, False, 0)

        btn_refresh = Gtk.Button(label="🔄  Refresh Hardware")
        btn_refresh.connect("clicked", lambda _: self.refresh())
        top.pack_end(btn_refresh, False, False, 0)

        # Scrolled container for seat cards
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.pack_start(sw, True, True, 0)

        seats_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sw.add(seats_container)

        self.card_seat1 = SeatCard(0, is_primary=True, app=self.app)
        self.card_seat2 = SeatCard(1, is_primary=False, app=self.app)

        seats_container.pack_start(self.card_seat1, False, False, 0)
        seats_container.pack_start(self.card_seat2, False, False, 0)

        self.refresh()

    def refresh(self):
        """Fetches hardware and updates seat card dropdowns."""
        threading.Thread(target=self._fetch_hw_bg, daemon=True).start()

    def _fetch_hw_bg(self):
        displays, _ = self.app.device_mgr.get_displays()
        pointers = self.app.device_mgr.get_usable_pointers()
        keyboards = self.app.device_mgr.get_usable_keyboards()
        cfg = self.app.config_mgr.load_config()
        seats_cfg = cfg.get("seats", [{}, {}])

        self._cached_displays = displays
        self._cached_pointers = pointers
        self._cached_keyboards = keyboards

        GLib.idle_add(self._populate_ui, displays, pointers, keyboards, seats_cfg)

    def _populate_ui(self, displays, pointers, keyboards, seats_cfg):
        s1_cfg = seats_cfg[0] if len(seats_cfg) > 0 else {}
        s2_cfg = seats_cfg[1] if len(seats_cfg) > 1 else {}

        self.card_seat1.populate_dropdowns(displays, pointers, keyboards, s1_cfg)
        self.card_seat2.populate_dropdowns(displays, pointers, keyboards, s2_cfg)

    def _on_auto_detect(self, _btn):
        self.app.show_info("Auto-detecting hardware layout and mapping seats...", 0)
        cfg = self.app.config_mgr.auto_generate_config(self.app.device_mgr)
        self.app.dashboard_tab.append_log("Auto-detection applied to Seat 1 and Seat 2.")
        self.refresh()
        self.app.show_info("Hardware auto-assigned to Seat 1 and Seat 2 ✓", 0)

    def _on_save_config(self, _btn):
        s1_data = self.card_seat1.get_configured_data(
            self._cached_displays, self._cached_pointers, self._cached_keyboards
        )
        s2_data = self.card_seat2.get_configured_data(
            self._cached_displays, self._cached_pointers, self._cached_keyboards
        )

        cfg = self.app.config_mgr.load_config()
        cfg["seats"] = [s1_data, s2_data]

        ok = self.app.config_mgr.save_config(cfg)
        if ok:
            self.app.show_info("Configuration saved successfully ✓", 0)
            self.app.dashboard_tab.append_log("Configuration saved to ~/.config/xinput-seats/config.json")
            self.app.dashboard_tab.refresh_metrics()
        else:
            self.app.show_info("Failed to save configuration!", 1)
