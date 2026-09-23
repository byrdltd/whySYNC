"""A pair's trash, round by round, with one-click restore into the source."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import PurePosixPath

from whysync import trash
from whysync.config import Pair
from whysync.engine import readiness
from whysync.fmt import size
from whysync.i18n import t, tn
from whysync.ui.gi_ready import Adw, GLib, Gtk
from whysync.ui.model import folder_title

ROWS_PER_ROUND = 100


class TrashDialog(Adw.Dialog):
    def __init__(self, pair: Pair, toast: Callable[[str], None]) -> None:
        super().__init__(content_width=560, content_height=600)
        self._pair = pair
        self._toast = toast
        self._busy = False

        self._banner = Adw.Banner(title=t("ui.trash.source_missing"))
        self._page = Adw.PreferencesPage()
        self._empty = Adw.StatusPage(icon_name="user-trash-symbolic", title=t("ui.trash.empty"),
                                     description=t("ui.trash.empty_body"))
        self._stack = Gtk.Stack()
        self._stack.add_named(self._page, "list")
        self._stack.add_named(self._empty, "empty")

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=t("ui.trash.title"),
                                                            subtitle=folder_title(pair.source)))
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self._banner)
        view.set_content(self._stack)
        self.set_child(view)
        self.reload()

    def _can_restore(self) -> bool:
        return readiness(self._pair) not in ("reason.source_missing", "reason.source_unreadable")

    def reload(self) -> None:
        for group in getattr(self, "_groups", []):
            self._page.remove(group)
        self._groups: list[Adw.PreferencesGroup] = []
        can_restore = self._can_restore() and not self._busy
        self._banner.set_revealed(not self._can_restore())

        found = trash.rounds(self._pair)
        self._stack.set_visible_child_name("list" if found else "empty")
        for rnd in found:
            group = Adw.PreferencesGroup(
                title=time.strftime("%Y-%m-%d %H:%M", time.localtime(rnd.at)),
                description=tn("ui.trash.round_info", len(rnd.files), size=size(rnd.size)),
            )
            restore_all = Gtk.Button(label=t("ui.trash.restore_all"), valign=Gtk.Align.CENTER,
                                     sensitive=can_restore)
            restore_all.add_css_class("flat")
            restore_all.connect("clicked", lambda *_, name=rnd.name: self._restore(name, None))
            group.set_header_suffix(restore_all)
            for item in rnd.files[:ROWS_PER_ROUND]:
                group.add(self._file_row(rnd.name, item, can_restore))
            if len(rnd.files) > ROWS_PER_ROUND:
                more = Adw.ActionRow(title=t("cli.held_more", more=len(rnd.files) - ROWS_PER_ROUND))
                more.add_css_class("dim-label")
                group.add(more)
            self._page.add(group)
            self._groups.append(group)

    def _file_row(self, round_name: str, item: trash.TrashFile, can_restore: bool) -> Adw.ActionRow:
        path = PurePosixPath(item.path)
        folder = str(path.parent) if str(path.parent) != "." else ""
        row = Adw.ActionRow(title=path.name, subtitle=" · ".join(p for p in (folder, size(item.size)) if p),
                            use_markup=False)
        btn = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                         tooltip_text=t("ui.trash.restore"), sensitive=can_restore)
        btn.add_css_class("flat")
        btn.connect("clicked", lambda *_: self._restore(round_name, [item.path]))
        row.add_suffix(btn)
        return row

    def _restore(self, round_name: str, paths: list[str] | None) -> None:
        if self._busy:
            return
        self._busy = True
        self.reload()

        def work() -> None:
            try:
                result = trash.restore(self._pair, round_name, paths)
                message = tn("ui.trash.restored", len(result.restored))
                if result.renamed:
                    message += " · " + t("ui.trash.kept_both", count=result.renamed)
                if result.failed:
                    message += " · " + t("ui.trash.failed", count=len(result.failed))
            except trash.RestoreError as exc:
                message = t(exc.key, **exc.params)
            GLib.idle_add(done, message)

        def done(message: str) -> bool:
            self._busy = False
            self._toast(message)
            self.reload()
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True, name="restore").start()
