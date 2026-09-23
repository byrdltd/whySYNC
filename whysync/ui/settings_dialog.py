"""A pair's settings: deletion limit, trash, the not-synced warning and what to skip.

The folders are not editable here: other folders are a new pair, with a first
round of its own.
"""
from __future__ import annotations

from collections.abc import Callable

from whysync import actions
from whysync.config import Pair
from whysync.i18n import t
from whysync.ui.gi_ready import Adw, Gtk
from whysync.ui.model import folder_title


class SettingsDialog(Adw.Dialog):
    def __init__(self, pair: Pair, on_save: Callable[[dict], bool]) -> None:
        """`on_save(values)` applies them and says whether the dialog may close."""
        super().__init__(content_width=520, follows_content_size=True)
        self._on_save = on_save

        numbers = Adw.PreferencesGroup()
        self.spins = {
            "max_deletes": self._spin(pair, "max_deletes", "ui.add.limit", "ui.add.limit_hint"),
            "trash_days": self._spin(pair, "trash_days", "ui.settings.trash_days", "ui.settings.trash_days_hint"),
            "stale_days": self._spin(pair, "stale_days", "ui.settings.stale_days", "ui.settings.stale_days_hint"),
            "verify_days": self._spin(pair, "verify_days", "ui.settings.verify_days", "ui.settings.verify_days_hint"),
        }
        for row in self.spins.values():
            numbers.add(row)

        skip = Adw.PreferencesGroup(title=t("ui.settings.excludes"), description=t("ui.settings.excludes_hint"))
        self.excludes = Gtk.TextView(monospace=True, top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.excludes.get_buffer().set_text("\n".join(pair.excludes))
        frame = Gtk.Frame(child=Gtk.ScrolledWindow(child=self.excludes, min_content_height=90,
                                                   hscrollbar_policy=Gtk.PolicyType.NEVER))
        frame.add_css_class("card")
        skip.add(frame)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=6, margin_bottom=18, margin_start=18, margin_end=18)
        body.append(numbers)
        body.append(skip)

        save = Gtk.Button(label=t("ui.settings.save"))
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save())
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=t("ui.settings.title"),
                                                            subtitle=folder_title(pair.source)))
        header.pack_end(save)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_child(view)

    @staticmethod
    def _spin(pair: Pair, name: str, title: str, hint: str) -> Adw.SpinRow:
        low, high = actions.LIMITS[name]
        row = Adw.SpinRow.new_with_range(low, high, 1)
        row.set_value(getattr(pair, name))
        row.set_title(t(title))
        row.set_subtitle(t(hint))
        return row

    def values(self) -> dict:
        buffer = self.excludes.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        return {**{name: int(row.get_value()) for name, row in self.spins.items()},
                "excludes": text.splitlines()}

    def save(self) -> None:
        if self._on_save(self.values()):
            self.close()
