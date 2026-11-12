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

        # Row 3: Peripheral Locking to Display
        lbl_lock = Gtk.Label(label="Peripheral Lock:")
        lbl_lock.set_halign(Gtk.Align.END)
        grid.attach(lbl_lock, 0, 3, 1, 1)

        lock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        grid.attach(lock_box, 1, 3, 1, 1)

        self.chk_lock_peripherals = Gtk.CheckButton(label="🔒 Lock Peripherals to this Display (Auto-reseat on replug)")
        self.chk_lock_peripherals.set_tooltip_text(
            "When checked, this mouse and keyboard remain permanently bound to this display.\n"
            "If unplugged and plugged back in (even into a different USB port), the setup auto-reseats."
        )
        self.chk_lock_peripherals.set_active(True)
        lock_box.pack_start(self.chk_lock_peripherals, False, False, 0)

        self.lbl_lock_status = Gtk.Label(label="")
        self.lbl_lock_status.set_halign(Gtk.Align.START)
        lock_box.pack_start(self.lbl_lock_status, False, False, 0)

        # Row 4: Firefox Profile
        lbl_prof = Gtk.Label(label="Firefox Profile:")
        lbl_prof.set_halign(Gtk.Align.END)
        grid.attach(lbl_prof, 0, 4, 1, 1)

        self.ent_profile = Gtk.Entry()
        self.ent_profile.set_text(self.seat_id)
        grid.attach(self.ent_profile, 1, 4, 1, 1)

        # Row 5: Kiosk Homepage URL
        lbl_url = Gtk.Label(label="Kiosk Home URL:")
        lbl_url.set_halign(Gtk.Align.END)
        grid.attach(lbl_url, 0, 5, 1, 1)

        self.ent_url = Gtk.Entry()
        default_url = "https://duckduckgo.com" if is_primary else "https://www.google.com"
        self.ent_url.set_text(default_url)
        grid.attach(self.ent_url, 1, 5, 1, 1)

        # Row 6: Options
        opts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        grid.attach(opts_box, 1, 6, 1, 1)

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

        # 2. Mice (match by locked peripheral name first, then ID)
        self.combo_mouse.remove_all()
        selected_mouse_id = current_cfg.get("mouse_id")
        selected_mouse_name = current_cfg.get("mouse_name", "")
        active_mouse_idx = 0
        mouse_found = False

        for idx, p in enumerate(pointers):
            self.combo_mouse.append_text(f"[ID {p['id']}] {p['name']}")
            if selected_mouse_name and p['name'].strip().lower() == selected_mouse_name.strip().lower():
                active_mouse_idx = idx
                mouse_found = True
            elif not mouse_found and p['id'] == selected_mouse_id:
                active_mouse_idx = idx
                mouse_found = True

        if not mouse_found and selected_mouse_name:
            # Device is unplugged, but memory is retained
            self.combo_mouse.append_text(f"⚠️ [Unplugged] {selected_mouse_name} (Locked)")
            active_mouse_idx = len(pointers)

        if pointers or selected_mouse_name:
            self.combo_mouse.set_active(active_mouse_idx)

        # 3. Keyboards (match by locked peripheral name first, then ID)
        self.combo_kbd.remove_all()
        selected_kbd_id = current_cfg.get("keyboard_id")
        selected_kbd_name = current_cfg.get("keyboard_name", "")
        active_kbd_idx = 0
        kbd_found = False

        for idx, k in enumerate(keyboards):
            self.combo_kbd.append_text(f"[ID {k['id']}] {k['name']}")
            if selected_kbd_name and k['name'].strip().lower() == selected_kbd_name.strip().lower():
                active_kbd_idx = idx
                kbd_found = True
            elif not kbd_found and k['id'] == selected_kbd_id:
                active_kbd_idx = idx
                kbd_found = True

        if not kbd_found and selected_kbd_name:
            # Device is unplugged, but memory is retained
            self.combo_kbd.append_text(f"⚠️ [Unplugged] {selected_kbd_name} (Locked)")
            active_kbd_idx = len(keyboards)

        if keyboards or selected_kbd_name:
            self.combo_kbd.set_active(active_kbd_idx)

        # 4. Peripheral Lock state & status badge
        is_locked = current_cfg.get("lock_peripherals", True)
        self.chk_lock_peripherals.set_active(is_locked)
        target_d = selected_disp or (displays[0]["name"] if displays else "Display")
        if is_locked and (selected_mouse_name or selected_kbd_name):
            m_icon = "🟢 Connected" if mouse_found else "🔴 Unplugged (retained)"
            k_icon = "🟢 Connected" if kbd_found else "🔴 Unplugged (retained)"
            self.lbl_lock_status.set_markup(
                f"<span size='small' color='#2563eb'><b>🔒 Locked to {target_d}:</b> Mouse [{m_icon}] | Keyboard [{k_icon}]</span>"
            )
        else:
            self.lbl_lock_status.set_markup("<span size='small' color='#6b7280'>Peripherals will follow default auto-detection.</span>")

        # 5. Text entries
        if "profile_name" in current_cfg:
            self.ent_profile.set_text(current_cfg["profile_name"])
        if "kiosk_url" in current_cfg:
            self.ent_url.set_text(current_cfg["kiosk_url"])
        if "lock_kiosk" in current_cfg:
            self.chk_lock.set_active(current_cfg["lock_kiosk"])
        if "auto_respawn" in current_cfg:
            self.chk_respawn.set_active(current_cfg["auto_respawn"])

    def get_configured_data(self, displays, pointers, keyboards) -> dict:
        """Extracts the user configuration from the widgets, retaining memory of locked peripherals."""
        disp_idx = self.combo_disp.get_active()
        disp_name = displays[disp_idx]["name"] if disp_idx >= 0 and disp_idx < len(displays) else ""

        mouse_idx = self.combo_mouse.get_active()
        if mouse_idx >= 0 and mouse_idx < len(pointers):
            mouse_id = pointers[mouse_idx]["id"]
            mouse_name = pointers[mouse_idx]["name"]
        else:
            # If currently unplugged placeholder was selected, retain the name from text
            combo_txt = self.combo_mouse.get_active_text() or ""
            if "⚠️ [Unplugged]" in combo_txt:
                mouse_name = combo_txt.replace("⚠️ [Unplugged]", "").replace("(Locked)", "").strip()
                mouse_id = None
            else:
                mouse_id = None
                mouse_name = ""

        kbd_idx = self.combo_kbd.get_active()
        if kbd_idx >= 0 and kbd_idx < len(keyboards):
            kbd_id = keyboards[kbd_idx]["id"]
            kbd_name = keyboards[kbd_idx]["name"]
        else:
            combo_txt = self.combo_kbd.get_active_text() or ""
            if "⚠️ [Unplugged]" in combo_txt:
                kbd_name = combo_txt.replace("⚠️ [Unplugged]", "").replace("(Locked)", "").strip()
                kbd_id = None
            else:
                kbd_id = None
                kbd_name = ""

        lock_periph = self.chk_lock_peripherals.get_active()

        return {
            "id": self.seat_id,
            "name": f"Seat {self.seat_index + 1} ({'Primary' if self.is_primary else 'Secondary'})",
            "is_primary": self.is_primary,
            "display": disp_name,
            "mouse_id": mouse_id,
            "mouse_name": mouse_name,
            "keyboard_id": kbd_id,
            "keyboard_name": kbd_name,
            "lock_peripherals": lock_periph,
            "locked_display": disp_name if lock_periph else None,
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
        new_seats = [s1_data, s2_data]

        def _merge_seat(existing: dict, updated: dict) -> dict:
            merged = dict(existing)
            merged.update(updated)

            # Preserve device identities, product IDs, and display geometry
            for field in (
                "mouse_ident",
                "keyboard_ident",
                "mouse_product_id",
                "keyboard_product_id",
                "resolution",
                "width",
                "height",
            ):
                if field in existing and field not in updated:
                    merged[field] = existing[field]
                elif field in existing and updated.get(field) in (None, ""):
                    if "mouse" in field and existing.get("mouse_name") == updated.get("mouse_name"):
                        merged[field] = existing[field]
                    elif "keyboard" in field and existing.get("keyboard_name") == updated.get("keyboard_name"):
                        merged[field] = existing[field]

            return merged

        def mutator(cfg: dict):
            current_seats = cfg.get("seats", [])
            merged_list = []
            for i, new_s in enumerate(new_seats):
                sid = new_s.get("id")
                existing = next((s for s in current_seats if s.get("id") == sid), None)
                if existing is None and i < len(current_seats):
                    existing = current_seats[i]
                if existing:
                    merged_list.append(_merge_seat(existing, new_s))
                else:
                    merged_list.append(dict(new_s))
            cfg["seats"] = merged_list

        try:
            self.app.config_mgr.update(mutator)
            ok = True
        except Exception as exc:
            logger.error(f"Failed to save config: {exc}")
            ok = False

        if ok:
            self.app.show_info("Configuration saved successfully ✓", 0)
            self.app.dashboard_tab.append_log("Configuration saved to ~/.config/xinput-seats/config.json")
            if s1_data.get("lock_peripherals"):
                self.app.dashboard_tab.append_log(f"🔒 Locked Seat 1 peripherals to '{s1_data['display']}': Mouse '{s1_data.get('mouse_name')}', Kbd '{s1_data.get('keyboard_name')}'")
            if s2_data.get("lock_peripherals"):
                self.app.dashboard_tab.append_log(f"🔒 Locked Seat 2 peripherals to '{s2_data['display']}': Mouse '{s2_data.get('mouse_name')}', Kbd '{s2_data.get('keyboard_name')}'")
            self.refresh()
            self.app.dashboard_tab.refresh_metrics()
        else:
            self.app.show_info("Failed to save configuration!", 1)
