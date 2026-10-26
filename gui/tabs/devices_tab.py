"""
Devices Tab for XInput Pseudo-Seating Control Panel.
Discovers and lists connected Displays, XInput Pointers, Keyboards, and Physical sysfs devices.
Includes an interactive device identifier.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk
import threading


class DeviceFinderDialog(Gtk.Dialog):
    """Interactive dialog that listens for input events to immediately identify physical devices."""

    def __init__(self, parent, target_type="any"):
        super().__init__(
            title="Interactive Device Finder",
            transient_for=parent,
            modal=True,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        self.set_default_size(450, 220)
        self.target_type = target_type
        self.detected_device = None

        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.btn_select = self.add_button("Select Device", Gtk.ResponseType.OK)
        self.btn_select.set_sensitive(False)

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(14)
        box.set_margin_end(14)

        icon_lbl = Gtk.Label()
        icon_lbl.set_markup('<span size="xx-large">🎯</span>')
        box.pack_start(icon_lbl, False, False, 0)

        self.prompt_lbl = Gtk.Label()
        self.prompt_lbl.set_markup('<b>Move the mouse or press any key on the device you want to identify...</b>')
        self.prompt_lbl.set_line_wrap(True)
        self.prompt_lbl.set_halign(Gtk.Align.CENTER)
        box.pack_start(self.prompt_lbl, False, False, 0)

        self.result_lbl = Gtk.Label(label="Waiting for input event...")
        self.result_lbl.get_style_context().add_class("card")
        box.pack_start(self.result_lbl, False, False, 0)

        # Connect event listeners
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("key-press-event", self._on_key_event)
        self.connect("motion-notify-event", self._on_motion_event)
        self.connect("button-press-event", self._on_button_event)

        self.show_all()

    def _on_key_event(self, _widget, event):
        dev = event.get_source_device() or event.get_device()
        dev_name = dev.get_name() if dev else "Unknown Keyboard"
        self._set_detected(dev_name, "Keyboard")
        return False

    def _on_motion_event(self, _widget, event):
        dev = event.get_source_device() or event.get_device()
        dev_name = dev.get_name() if dev else "Unknown Pointer"
        self._set_detected(dev_name, "Pointer / Mouse")
        return False

    def _on_button_event(self, _widget, event):
        dev = event.get_source_device() or event.get_device()
        dev_name = dev.get_name() if dev else "Unknown Pointer"
        self._set_detected(dev_name, "Mouse Button")
        return False

    def _set_detected(self, name, event_type):
        self.detected_device = name
        self.result_lbl.set_markup(f'Detected: <b>{name}</b>  <span color="#26a269">({event_type})</span>')
        self.btn_select.set_sensitive(True)


class DevicesTab(Gtk.Box):
    """Tab 3: Hardware discovery for displays and XInput devices."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app = app
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # Top Bar
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.pack_start(top, False, False, 0)

        lbl = Gtk.Label()
        lbl.set_markup('<b>Hardware Discovery &amp; Diagnostics</b>')
        top.pack_start(lbl, False, False, 0)

        top.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 4)

        btn_refresh = Gtk.Button(label="🔄  Refresh All Devices")
        btn_refresh.connect("clicked", lambda _: self.refresh())
        top.pack_start(btn_refresh, False, False, 0)

        btn_finder = Gtk.Button(label="🎯  Interactive Device Finder")
        btn_finder.connect("clicked", self._on_interactive_finder)
        top.pack_start(btn_finder, False, False, 0)

        btn_reset = Gtk.Button(label="↩  Reset All to Virtual Core")
        btn_reset.connect("clicked", self._on_reset_all)
        top.pack_end(btn_reset, False, False, 0)

        # Notebook for sections
        nb = Gtk.Notebook()
        self.pack_start(nb, True, True, 0)

        # ── 1. Displays ───────────────────────────────────────────────────
        disp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        disp_box.set_margin_top(6)
        disp_box.set_margin_start(6)
        disp_box.set_margin_end(6)

        self.disp_store = Gtk.ListStore(str, str, str, str, str)  # Connector, Resolution, Position, Primary, Active
        self.disp_tv = self._make_tv(
            self.disp_store,
            ["Connector", "Resolution", "Position", "Primary", "Active Status"]
        )
        sw_disp = Gtk.ScrolledWindow()
        sw_disp.add(self.disp_tv)
        disp_box.pack_start(sw_disp, True, True, 0)

        nb.append_page(disp_box, Gtk.Label(label="📺 Displays"))

        # ── 2. Mice / Pointers ────────────────────────────────────────────
        ptr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ptr_box.set_margin_top(6)
        ptr_box.set_margin_start(6)
        ptr_box.set_margin_end(6)

        self.ptr_store = Gtk.ListStore(str, str, str, str)  # ID, Name, Master Attachment, Type
        self.ptr_tv = self._make_tv(
            self.ptr_store,
            ["ID", "Device Name", "Master Attachment", "Type"]
        )
        sw_ptr = Gtk.ScrolledWindow()
        sw_ptr.add(self.ptr_tv)
        ptr_box.pack_start(sw_ptr, True, True, 0)

        ptr_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ptr_box.pack_start(ptr_actions, False, False, 0)

        btn_ptr_core = Gtk.Button(label="Attach to Virtual Core")
        btn_ptr_core.connect("clicked", self._on_attach_ptr_core)
        ptr_actions.pack_start(btn_ptr_core, False, False, 0)

        btn_ptr_s2 = Gtk.Button(label="Attach to Seat 2 Master")
        btn_ptr_s2.connect("clicked", self._on_attach_ptr_s2)
        ptr_actions.pack_start(btn_ptr_s2, False, False, 0)

        nb.append_page(ptr_box, Gtk.Label(label="🖱 Pointers & Mice"))

        # ── 3. Keyboards ──────────────────────────────────────────────────
        kbd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        kbd_box.set_margin_top(6)
        kbd_box.set_margin_start(6)
        kbd_box.set_margin_end(6)

        self.kbd_store = Gtk.ListStore(str, str, str, str)  # ID, Name, Master Attachment, Type
        self.kbd_tv = self._make_tv(
            self.kbd_store,
            ["ID", "Device Name", "Master Attachment", "Type"]
        )
        sw_kbd = Gtk.ScrolledWindow()
        sw_kbd.add(self.kbd_tv)
        kbd_box.pack_start(sw_kbd, True, True, 0)

        kbd_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        kbd_box.pack_start(kbd_actions, False, False, 0)

        btn_kbd_core = Gtk.Button(label="Attach to Virtual Core")
        btn_kbd_core.connect("clicked", self._on_attach_kbd_core)
        kbd_actions.pack_start(btn_kbd_core, False, False, 0)

        btn_kbd_s2 = Gtk.Button(label="Attach to Seat 2 Master")
        btn_kbd_s2.connect("clicked", self._on_attach_kbd_s2)
        kbd_actions.pack_start(btn_kbd_s2, False, False, 0)

        nb.append_page(kbd_box, Gtk.Label(label="⌨ Keyboards"))

        # ── 4. Sysfs Input Devices ────────────────────────────────────────
        sys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sys_box.set_margin_top(6)
        sys_box.set_margin_start(6)
        sys_box.set_margin_end(6)

        self.sys_store = Gtk.ListStore(str, str, str, str)  # Sys Name, Hardware Name, Type, Path
        self.sys_tv = self._make_tv(
            self.sys_store,
            ["Kernel Node", "Hardware Name", "Device Type", "Sysfs Path"]
        )
        sw_sys = Gtk.ScrolledWindow()
        sw_sys.add(self.sys_tv)
        sys_box.pack_start(sw_sys, True, True, 0)

        nb.append_page(sys_box, Gtk.Label(label="🔌 Physical Hardware"))

        self.refresh()

    def _make_tv(self, store, titles):
        tv = Gtk.TreeView(model=store)
        tv.set_headers_visible(True)
        for idx, title in enumerate(titles):
            r = Gtk.CellRendererText()
            c = Gtk.TreeViewColumn(title, r, text=idx)
            c.set_resizable(True)
            c.set_min_width(90)
            tv.append_column(c)
        return tv

    def refresh(self):
        """Fetches all device data asynchronously."""
        threading.Thread(target=self._fetch_all_bg, daemon=True).start()

    def _fetch_all_bg(self):
        displays, _ = self.app.device_mgr.get_displays()
        masters, ptrs, kbds = self.app.device_mgr.get_xinput_hierarchy()
        phys_devs = self.app.device_mgr.get_physical_input_devices()

        GLib.idle_add(self._populate_all_ui, displays, ptrs, kbds, phys_devs, masters)

    def _populate_all_ui(self, displays, ptrs, kbds, phys_devs, masters):
        # 1. Displays
        self.disp_store.clear()
        for d in displays:
            self.disp_store.append([
                d["name"],
                f"{d['width']}x{d['height']}",
                f"+{d['x']}+{d['y']}",
                "Yes (Primary)" if d.get("primary") else "No",
                "Connected" if d.get("connected") else "Disconnected",
            ])

        # 2. Pointers
        self.ptr_store.clear()
        for p in ptrs:
            master_desc = f"Master ID {p['attachment']}"
            if p['attachment'] == 2:
                master_desc = "Virtual core pointer (2)"
            elif p['attachment'] in masters:
                master_desc = f"{masters[p['attachment']]['name']} ({p['attachment']})"

            self.ptr_store.append([
                str(p["id"]),
                p["name"],
                master_desc,
                "XTEST" if p["is_xtest"] else "Slave Pointer",
            ])

        # 3. Keyboards
        self.kbd_store.clear()
        for k in kbds:
            master_desc = f"Master ID {k['attachment']}"
            if k['attachment'] == 3:
                master_desc = "Virtual core keyboard (3)"
            elif k['attachment'] in masters:
                master_desc = f"{masters[k['attachment']]['name']} ({k['attachment']})"

            self.kbd_store.append([
                str(k["id"]),
                k["name"],
                master_desc,
                "XTEST" if k["is_xtest"] else "Slave Keyboard",
            ])

        # 4. Physical Devices
        self.sys_store.clear()
        for s in phys_devs:
            self.sys_store.append([
                s["sys_name"],
                s["name"],
                s["type"],
                s["phys"] or s["path"],
            ])

    # ── Actions ───────────────────────────────────────────────────────────
    def _get_selected_id(self, tv):
        sel = tv.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return None
        return int(model[it][0])

    def _on_attach_ptr_core(self, _btn):
        dev_id = self._get_selected_id(self.ptr_tv)
        if not dev_id:
            self.app.show_info("Select a pointer first.", 1)
            return
        self.app.seating_engine.reattach_device(dev_id, 2)
        self.app.dashboard_tab.append_log(f"Pointer {dev_id} reattached to Virtual core pointer.")
        self.refresh()

    def _on_attach_ptr_s2(self, _btn):
        dev_id = self._get_selected_id(self.ptr_tv)
        if not dev_id:
            self.app.show_info("Select a pointer first.", 1)
            return
        ptr_id, _ = self.app.seating_engine.create_master_pair("Seat2")
        if ptr_id:
            self.app.seating_engine.reattach_device(dev_id, ptr_id)
            self.app.dashboard_tab.append_log(f"Pointer {dev_id} reattached to Seat2 pointer ({ptr_id}).")
            self.refresh()

    def _on_attach_kbd_core(self, _btn):
        dev_id = self._get_selected_id(self.kbd_tv)
        if not dev_id:
            self.app.show_info("Select a keyboard first.", 1)
            return
        self.app.seating_engine.reattach_device(dev_id, 3)
        self.app.dashboard_tab.append_log(f"Keyboard {dev_id} reattached to Virtual core keyboard.")
        self.refresh()

    def _on_attach_kbd_s2(self, _btn):
        dev_id = self._get_selected_id(self.kbd_tv)
        if not dev_id:
            self.app.show_info("Select a keyboard first.", 1)
            return
        _, kbd_id = self.app.seating_engine.create_master_pair("Seat2")
        if kbd_id:
            self.app.seating_engine.reattach_device(dev_id, kbd_id)
            self.app.dashboard_tab.append_log(f"Keyboard {dev_id} reattached to Seat2 keyboard ({kbd_id}).")
            self.refresh()

    def _on_reset_all(self, _btn):
        self.app.seating_engine.reset_all_to_core()
        self.app.show_info("All devices reattached to core masters.", 0)
        self.app.dashboard_tab.append_log("All devices reset to Virtual core.")
        self.refresh()

    def _on_interactive_finder(self, _btn):
        dlg = DeviceFinderDialog(self.app)
        resp = dlg.run()
        detected = dlg.detected_device
        dlg.destroy()

        if resp == Gtk.ResponseType.OK and detected:
            self.app.show_info(f"Identified device: {detected}", 0)
            self.app.dashboard_tab.append_log(f"Interactive Finder identified: {detected}")
