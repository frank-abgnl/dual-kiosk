"""
Configuration & Profile Management Tab for XInput Pseudo-Seating Control Panel.
Provides raw JSON configuration editing, schema validation, and profile folder management.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
import json
import subprocess
import threading
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

        self.btn_save = Gtk.Button(label="💾  Save JSON")
        self.btn_save.get_style_context().add_class("btn-primary")
        self.btn_save.connect("clicked", self._on_save)
        tb.pack_start(self.btn_save, False, False, 0)

        self.btn_validate = Gtk.Button(label="✅  Validate Config")
        self.btn_validate.connect("clicked", self._on_validate)
        tb.pack_start(self.btn_validate, False, False, 0)

        self.btn_reload = Gtk.Button(label="🔄  Reload File")
        self.btn_reload.connect("clicked", lambda b: self.reload_file(b))
        tb.pack_start(self.btn_reload, False, False, 0)

        btn_profiles = Gtk.Button(label="📁  Open Profiles Folder")
        btn_profiles.connect("clicked", self._on_open_profiles_dir)
        tb.pack_start(btn_profiles, False, False, 0)

        self.btn_default = Gtk.Button(label="⚙  Reset to Defaults")
        self.btn_default.connect("clicked", self._on_reset_default)
        tb.pack_end(self.btn_default, False, False, 0)

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

    def reload_file(self, btn=None):
        """Loads JSON content into the editor buffer asynchronously."""
        target_btn = btn or getattr(self, "btn_reload", None)
        if target_btn and hasattr(target_btn, "set_sensitive"):
            target_btn.set_sensitive(False)

        def _worker():
            try:
                cfg = self.app.config_mgr.load_config()
                text = json.dumps(cfg, indent=2)
            except Exception as exc:
                text = f"// Error loading config: {exc}"

            def _done():
                self.editor_buf.set_text(text)
                if target_btn and hasattr(target_btn, "set_sensitive"):
                    target_btn.set_sensitive(True)
                return False

            GLib.idle_add(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_save(self, btn=None):
        target_btn = btn or getattr(self, "btn_save", None)
        start = self.editor_buf.get_start_iter()
        end = self.editor_buf.get_end_iter()
        text = self.editor_buf.get_text(start, end, True)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as err:
            self.app.show_info(f"Invalid JSON syntax: {err}", 1)
            return

        if target_btn and hasattr(target_btn, "set_sensitive"):
            target_btn.set_sensitive(False)

        def _worker():
            try:
                ok = self.app.config_mgr.save_config(parsed)
                err = None
            except Exception as exc:
                ok = False
                err = exc

            def _done():
                if target_btn and hasattr(target_btn, "set_sensitive"):
                    target_btn.set_sensitive(True)
                if ok:
                    self.app.show_info("Configuration JSON saved successfully ✓", 0)
                    self.app.dashboard_tab.append_log("Raw config JSON updated and saved.")
                    self.app.seats_tab.refresh()
                    self.app.dashboard_tab.refresh_metrics()
                else:
                    self.app.show_info(f"Failed to write config file! {err if err else ''}", 1)
                return False

            GLib.idle_add(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_validate(self, btn=None):
        target_btn = btn or getattr(self, "btn_validate", None)
        start = self.editor_buf.get_start_iter()
        end = self.editor_buf.get_end_iter()
        text = self.editor_buf.get_text(start, end, True)

        if target_btn and hasattr(target_btn, "set_sensitive"):
            target_btn.set_sensitive(False)

        def _worker():
            try:
                parsed = json.loads(text)
                parse_err = None
            except json.JSONDecodeError as err:
                parsed = None
                parse_err = err

            if parse_err:
                issues = None
            else:
                from core.schema import validate
                issues = validate(parsed)

            def _done():
                if target_btn and hasattr(target_btn, "set_sensitive"):
                    target_btn.set_sensitive(True)
                if parse_err:
                    self.app.show_info(f"JSON Parse Error: {parse_err}", 1)
                elif issues:
                    msg = "Validation issues:\n• " + "\n• ".join(f"[{e.path}] {e.message}" for e in issues)
                    self.app.show_info(msg, 1)
                else:
                    self.app.show_info("Config validation passed successfully ✓", 0)
                return False

            GLib.idle_add(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_open_profiles_dir(self, _btn):
        p_dir = str(self.app.kiosk_mgr.get_profile_path(""))
        try:
            subprocess.Popen(["xdg-open", p_dir], start_new_session=True)
        except Exception as exc:
            self.app.show_info(f"Could not open file manager: {exc}", 1)

    def _on_reset_default(self, btn=None):
        target_btn = btn or getattr(self, "btn_default", None)
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Reset to default configuration?",
        )
        dlg.format_secondary_text("This will backup current settings to config.json.bak and reset to defaults.")
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.YES:
            if target_btn and hasattr(target_btn, "set_sensitive"):
                target_btn.set_sensitive(False)

            def _worker():
                try:
                    self.app.config_mgr.reset_to_defaults()
                    err = None
                except Exception as exc:
                    err = exc

                def _done():
                    if target_btn and hasattr(target_btn, "set_sensitive"):
                        target_btn.set_sensitive(True)
                    if err:
                        self.app.show_info(f"Failed to reset: {err}", 1)
                    else:
                        self.reload_file()
                        self.app.seats_tab.refresh()
                        self.app.show_info("Configuration reset to defaults (backup saved to config.json.bak).", 0)
                    return False

                GLib.idle_add(_done)

            threading.Thread(target=_worker, daemon=True).start()
