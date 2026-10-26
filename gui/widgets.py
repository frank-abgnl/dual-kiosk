"""
Custom Reusable GTK3 Widgets for XInput Pseudo-Seating.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib


class StatusCard(Gtk.Frame):
    """A colored status card showing a metric value and a descriptive label."""

    def __init__(self, label_text: str):
        super().__init__()
        self.get_style_context().add_class("card")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.add(box)

        self._value_lbl = Gtk.Label(label="—")
        self._value_lbl.get_style_context().add_class("card-value")
        self._value_lbl.set_halign(Gtk.Align.CENTER)
        box.pack_start(self._value_lbl, False, False, 0)

        desc = Gtk.Label(label=label_text)
        desc.get_style_context().add_class("card-label")
        desc.set_halign(Gtk.Align.CENTER)
        box.pack_start(desc, False, False, 0)

        self._classes = ["card-good", "card-warn", "card-error"]

    def set_value(self, text, state="good"):
        """Updates the card value and color state: 'good', 'warn', 'error', or 'neutral'."""
        self._value_lbl.set_text(str(text))
        ctx = self.get_style_context()
        for cls in self._classes:
            ctx.remove_class(cls)
        if state in ("good", "warn", "error"):
            ctx.add_class(f"card-{state}")
