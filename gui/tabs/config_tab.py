"""
Configuration & Profile Management Tab for XInput Pseudo-Seating Control Panel.
Provides raw JSON configuration editing, schema validation, and profile folder management.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
import json
import subprocess
from pathlib import Path


class ConfigTab(Gtk.Box):
    """Tab 5: Configuration file editor and profile tools."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app = app
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # Toolbar
        tb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.pack_start(tb, False, False, 0)

        btn_save = Gtk.Button(label="💾  Save JSON")
        btn_save.get_style_context().add_class("btn-primary")
        btn_save.connect("clicked", self._on_save)
        tb.pack_start(btn_save, False, False, 0)

        btn_validate = Gtk.Button(label="✅  Validate Config")
        btn_validate.connect("clicked", self._on_validate)
        tb.pack_start(btn_validate, False, False, 0)

        btn_reload = Gtk.Button(label="🔄  Reload File")
        btn_reload.connect("clicked", lambda _: self.reload_file())
        tb.pack_start(btn_reload, False, False, 0)

        btn_profiles = Gtk.Button(label="📁  Open Profiles Folder")
        btn_profiles.connect("clicked", self._on_open_profiles_dir)
        tb.pack_start(btn_profiles, False, False, 0)

        btn_default = Gtk.Button(label="⚙  Reset to Defaults")
        btn_default.connect("clicked", self._on_reset_default)
        tb.pack_end(btn_default, False, False, 0)

        # Path label
        self.path_lbl = Gtk.Label()
        self.path_lbl.set_markup(f'Config Path: <tt>{self.app.config_mgr.config_path}</tt>')
        self.path_lbl.set_halign(Gtk.Align.START)
        self.pack_start(self.path_lbl, False, False, 0)

        # Scrolled TextView editor
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.pack_start(sw, True, True, 0)

        self.editor_buf = Gtk.TextBuffer()
        self.editor_view = Gtk.TextView(buffer=self.editor_buf)
        self.editor_view.set_monospace(True)
        self.editor_view.set_left_margin(10)
        self.editor_view.set_right_margin(10)
        sw.add(self.editor_view)

        self.reload_file()

    def reload_file(self):
        """Loads JSON content into the editor buffer."""
        try:
            cfg = self.app.config_mgr.load_config()
            text = json.dumps(cfg, indent=2)
            self.editor_buf.set_text(text)
        except Exception as exc:
            self.editor_buf.set_text(f"// Error loading config: {exc}")

    def _on_save(self, _btn):
        start = self.editor_buf.get_start_iter()
        end = self.editor_buf.get_end_iter()
        text = self.editor_buf.get_text(start, end, True)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as err:
            self.app.show_info(f"Invalid JSON syntax: {err}", 1)
            return

        ok = self.app.config_mgr.save_config(parsed)
        if ok:
            self.app.show_info("Configuration JSON saved successfully ✓", 0)
            self.app.dashboard_tab.append_log("Raw config JSON updated and saved.")
            self.app.seats_tab.refresh()
            self.app.dashboard_tab.refresh_metrics()
        else:
            self.app.show_info("Failed to write config file!", 1)

    def _on_validate(self, _btn):
        start = self.editor_buf.get_start_iter()
        end = self.editor_buf.get_end_iter()
        text = self.editor_buf.get_text(start, end, True)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as err:
            self.app.show_info(f"JSON Parse Error: {err}", 1)
            return

        issues = self.app.config_mgr.validate_config(parsed)
        if issues:
            msg = "Validation warnings:\n• " + "\n• ".join(issues)
            self.app.show_info(msg, 1)
        else:
            self.app.show_info("Config validation passed successfully ✓", 0)

    def _on_open_profiles_dir(self, _btn):
        p_dir = str(self.app.kiosk_mgr.get_profile_path(""))
        try:
            subprocess.Popen(["xdg-open", p_dir], start_new_session=True)
        except Exception as exc:
            self.app.show_info(f"Could not open file manager: {exc}", 1)

    def _on_reset_default(self, _btn):
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Reset to default configuration?",
        )
        dlg.format_secondary_text("This will overwrite existing seat assignments with factory defaults.")
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.YES:
            default_cfg = self.app.config_mgr.get_default_config()
            self.app.config_mgr.save_config(default_cfg)
            self.reload_file()
            self.app.seats_tab.refresh()
            self.app.show_info("Configuration reset to defaults.", 0)
