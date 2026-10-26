"""
Main Application Window for XInput Pseudo-Seating Control Panel.
GTK3 Application Window with HeaderBar, InfoBar, Status Cards, and Modular Tabs.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

import logging
from pathlib import Path

from core.device_manager import DeviceManager
from core.xinput_seating import XInputSeatingEngine
from core.kiosk_manager import KioskManager
from core.config_manager import ConfigManager
from gui.styles import CSS_STYLES
from gui.tabs import DashboardTab, SeatsTab, DevicesTab, TestTab, ConfigTab

logger = logging.getLogger("xinput_seating.app")


class XInputMultiseatApp(Gtk.ApplicationWindow):
    """Main window for XInput Pseudo-Seating Control Panel."""

    def __init__(self):
        super().__init__(title="XInput Pseudo-Seating Control Panel")
        self.set_default_size(960, 680)
        self.set_position(Gtk.WindowPosition.CENTER)

        # ── Initialize Core Engines ───────────────────────────────────────
        self.device_mgr = DeviceManager()
        self.seating_engine = XInputSeatingEngine()
        self.kiosk_mgr = KioskManager()
        self.config_mgr = ConfigManager()

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

        # Connect cleanup on destroy
        self.connect("destroy", self._on_window_destroy)

        # First run check
        if not self.config_mgr.config_path.exists():
            GLib.idle_add(self._show_setup_wizard)

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
        logger.info("Window closing. Stopping kiosk watchdog...")
        self.kiosk_mgr.stop_watchdog()
        Gtk.main_quit()
