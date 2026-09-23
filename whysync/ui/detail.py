"""The selected pair, with room to say what is going on.

The sidebar keeps one line per pair; everything that needs space lives here:
the full-width bar, the numbers, the file being copied, both folders, what
the last round did (file by file) and the trash.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from whysync.config import Pair
from whysync.i18n import t, tn
from whysync.ui.gi_ready import Adw, Gtk, Pango
from whysync.ui.model import RowView, last_summary
from whysync.ui.round_view import KINDS, kind_rows
from whysync.ui.sidebar_row import Bar, StatusIcon


@dataclass
class DetailHandlers:
    sync: Callable[[str], None]
    pause: Callable[[str, bool], None]
    review: Callable[[str], None]
    remove: Callable[[str], None]
    trash: Callable[[str], None]
    settings: Callable[[str], None]
    repair: Callable[[str], None]
    open_folder: Callable[[str], None]


def _icon_button(icon: str, callback: Callable[[], None]) -> Gtk.Button:
    btn = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER)
    btn.add_css_class("flat")
    btn.connect("clicked", lambda *_: callback())
    return btn


class PairDetail(Adw.Bin):
    def __init__(self, handlers: DetailHandlers) -> None:
        super().__init__()
        self._h = handlers
        self.pair_id = ""
        self._paused = False
        self._paths = {"source": "", "target": ""}
        self._last_key: tuple | None = None
        self._last_rows: list = []
        self._mismatch_key: tuple | None = None
        self._mismatch_rows: list = []

        self.title = Adw.WindowTitle()
        self.sync = _icon_button("view-refresh-symbolic", lambda: self._h.sync(self.pair_id))
        self.pause = _icon_button("media-playback-pause-symbolic",
                                  lambda: self._h.pause(self.pair_id, not self._paused))
        header = Adw.HeaderBar(title_widget=self.title)
        header.pack_end(self.pause)
        header.pack_end(self.sync)

        page = Adw.PreferencesPage()
        page.add(self._status_group())
        page.add(self._folders_group())
        self.last = Adw.PreferencesGroup()
        page.add(self.last)
        page.add(self._more_group())

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        self.set_child(view)

    # --- building ---

    def _status_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        self.state = Adw.ActionRow(use_markup=False)
        self.state.add_css_class("heading")
        self._state_icon = StatusIcon(pixel_size=24)
        self.state.add_prefix(self._state_icon)
        self.review = Gtk.Button(valign=Gtk.Align.CENTER)
        self.review.add_css_class("suggested-action")
        self.review.connect("clicked", lambda *_: self._h.review(self.pair_id))
        self.state.add_suffix(self.review)
        group.add(self.state)

        self.progress = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12)
        self.bar = Bar()
        self.numbers = Gtk.Label(xalign=0.0, wrap=True)
        self.numbers.add_css_class("caption")
        self.current = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.MIDDLE, selectable=True)
        self.current.add_css_class("caption")
        self.current.add_css_class("monospace")
        for widget in (self.bar, self.numbers, self.current):
            self.progress.append(widget)
        group.add(self.progress)

        # Files whose contents differ from the source (content check).
        self.mismatch = Adw.ExpanderRow(use_markup=False, visible=False)
        self.mismatch.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        self.repair = Gtk.Button(valign=Gtk.Align.CENTER)
        self.repair.add_css_class("suggested-action")
        self.repair.connect("clicked", lambda *_: self._h.repair(self.pair_id))
        self.mismatch.add_suffix(self.repair)
        group.add(self.mismatch)
        return group

    def _folders_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        self.source = self._path_row("folder-symbolic", "source")
        self.target = self._path_row("drive-harddisk-symbolic", "target")
        group.add(self.source)
        group.add(self.target)
        return group

    def _path_row(self, icon: str, which: str) -> Adw.ActionRow:
        row = Adw.ActionRow(use_markup=False)
        row.add_css_class("property")
        row.add_prefix(Gtk.Image(icon_name=icon))
        row.add_suffix(_icon_button("folder-open-symbolic", lambda: self._h.open_folder(self._paths[which])))
        return row

    def _more_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        self.settings = Adw.ActionRow(activatable=True, use_markup=False)
        self.settings.add_prefix(Gtk.Image(icon_name="emblem-system-symbolic"))
        self.settings.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.settings.connect("activated", lambda *_: self._h.settings(self.pair_id))
        self.trash = Adw.ActionRow(activatable=True)
        self.trash.add_prefix(Gtk.Image(icon_name="user-trash-symbolic"))
        self.trash.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.trash.connect("activated", lambda *_: self._h.trash(self.pair_id))
        self.remove_row = Adw.ActionRow()
        self.remove = Gtk.Button(valign=Gtk.Align.CENTER)
        self.remove.add_css_class("destructive-action")
        self.remove.add_css_class("flat")
        self.remove.connect("clicked", lambda *_: self._h.remove(self.pair_id))
        self.remove_row.add_suffix(self.remove)
        group.add(self.settings)
        group.add(self.trash)
        group.add(self.remove_row)
        return group

    # --- state ---

    def show(self, pair: Pair, view: RowView, st: dict) -> None:
        self.pair_id = pair.id
        self._paused = view.paused
        self._paths = {"source": pair.source, "target": pair.target}

        self.title.set_title(view.title)
        self.sync.set_sensitive(view.can_sync)
        self.sync.set_tooltip_text(t("ui.sync_now"))
        self.pause.set_icon_name("media-playback-start-symbolic" if view.paused else "media-playback-pause-symbolic")
        self.pause.set_tooltip_text(t("ui.resume" if view.paused else "ui.pause"))

        self._state_icon.show(view)
        self.state.set_title(view.subtitle)
        self.state.set_subtitle(st.get("error") or "" if view.state == "error" else "")
        self.review.set_visible(view.held_count > 0)
        self.review.set_label(t("ui.review"))

        self.progress.set_visible(view.progress is not None)
        self.bar.show_progress(view.progress)
        self.numbers.set_label(view.detail)
        self.numbers.set_visible(bool(view.detail))
        self.current.set_label(f"↳ {view.current}" if view.current else "")
        self.current.set_tooltip_text(view.current or None)
        self.current.set_visible(bool(view.current))

        for row, which, label in ((self.source, "source", "ui.source"), (self.target, "target", "ui.target")):
            row.set_title(t(label))
            row.set_subtitle(self._paths[which])
        self.settings.set_title(t("ui.settings.title"))
        warn = (t("ui.settings.warn_after", days=pair.stale_days) if pair.stale_days
                else t("ui.settings.warn_off"))
        verify = (t("ui.settings.verify_after", days=pair.verify_days) if pair.verify_days
                  else t("ui.settings.verify_off"))
        self.settings.set_subtitle(t("ui.settings.summary", max_deletes=pair.max_deletes,
                                     trash_days=pair.trash_days, warn=warn) + " · " + verify)
        self.trash.set_title(t("ui.trash.title"))
        self.trash.set_subtitle(t("ui.trash.row_hint"))
        self.remove_row.set_title(t("ui.remove_hint"))
        self.remove.set_label(t("ui.remove"))
        self._show_mismatch(pair.id, st.get("verify") or {})
        self._show_last(pair.id, st.get("last_sync") or {})

    def _show_mismatch(self, pair_id: str, verify: dict) -> None:
        count = int(verify.get("count", 0))
        self.mismatch.set_visible(count > 0)
        self.mismatch.set_title(tn("ui.mismatch.title", count))
        self.mismatch.set_subtitle(t("ui.mismatch.body"))
        self.repair.set_label(t("ui.mismatch.repair"))
        key = (pair_id, verify.get("at"))
        if key == self._mismatch_key:
            return  # keep the list as the user has it
        self._mismatch_key = key
        for row in self._mismatch_rows:
            self.mismatch.remove(row)
        self._mismatch_rows = []
        for path in verify.get("sample", []):
            item = Gtk.Label(label=path, xalign=0.0, ellipsize=Pango.EllipsizeMode.MIDDLE, tooltip_text=path,
                             margin_top=4, margin_bottom=4, margin_start=12, margin_end=12)
            item.add_css_class("monospace")
            item.add_css_class("caption")
            # Our own row: a bare label gets wrapped and could not be removed again.
            row = Gtk.ListBoxRow(child=item, activatable=False, selectable=False)
            self.mismatch.add_row(row)
            self._mismatch_rows.append(row)

    def _show_last(self, pair_id: str, last: dict) -> None:
        self.last.set_title(t("ui.last_change_title"))
        self.last.set_description(last_summary(last) or t("ui.last_change_none"))
        key = (pair_id, last.get("at"))
        if key == self._last_key:
            return  # keep what the user has expanded
        self._last_key = key
        for row in self._last_rows:
            self.last.remove(row)
        self._last_rows = kind_rows(self.last, {k: last.get(k, 0) for k, *_ in KINDS},
                                    {k: last.get(f"{k}_sample", []) for k, *_ in KINDS})
