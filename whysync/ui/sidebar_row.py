"""A pair in the sidebar: status icon, folder name, one line of state, a thin bar while syncing."""
from __future__ import annotations

from whysync.ui.gi_ready import GLib, Gtk, Pango
from whysync.ui.model import PULSE, RowView

STYLES = ("success", "warning", "error", "dim-label")
PULSE_MS = 120


class StatusIcon(Gtk.Stack):
    """The state icon, or a spinner while syncing."""

    def __init__(self, pixel_size: int = 16) -> None:
        super().__init__(valign=Gtk.Align.CENTER)
        self._icon = Gtk.Image(pixel_size=pixel_size)
        self._spinner = Gtk.Spinner(width_request=pixel_size, height_request=pixel_size)
        self.add_named(self._icon, "icon")
        self.add_named(self._spinner, "spin")

    def show(self, view: RowView) -> None:
        if view.state == "syncing":
            self._spinner.start()
            self.set_visible_child_name("spin")
            return
        self._spinner.stop()
        self._icon.set_from_icon_name(view.icon)
        for style in STYLES:
            self._icon.remove_css_class(style)
        if view.style:
            self._icon.add_css_class(view.style)
        self.set_visible_child_name("icon")


class Bar(Gtk.ProgressBar):
    """A progress bar that animates by itself while the end is unknown."""

    def __init__(self, **kwargs) -> None:
        super().__init__(visible=False, **kwargs)
        self.set_pulse_step(0.08)
        self._pulse_id = 0
        self.connect("unrealize", lambda *_: self._stop())

    def show_progress(self, progress: float | None) -> None:
        self.set_visible(progress is not None)
        if progress == PULSE:
            if not self._pulse_id:
                self._pulse_id = GLib.timeout_add(PULSE_MS, self._pulse)
            return
        self._stop()
        if progress is not None:
            self.set_fraction(progress)

    def _pulse(self) -> bool:
        self.pulse()
        return GLib.SOURCE_CONTINUE

    def _stop(self) -> None:
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = 0


class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, pair_id: str) -> None:
        super().__init__()
        self.pair_id = pair_id
        self._status = StatusIcon()
        self.title = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle.add_css_class("caption")
        self.subtitle.add_css_class("dim-label")
        self.bar = Bar(margin_top=4)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        for widget in (self.title, self.subtitle, self.bar):
            text.append(widget)
        box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6, margin_end=6)
        box.append(self._status)
        box.append(text)
        self.set_child(box)

    def update(self, view: RowView) -> None:
        self._status.show(view)
        self.title.set_label(view.title)
        self.subtitle.set_label(view.subtitle)
        self.set_tooltip_text(view.subtitle)
        self.bar.show_progress(view.progress)
