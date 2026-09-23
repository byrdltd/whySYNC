"""Main window: the pair list, kept in step with `pairs.json` and the service state."""
from __future__ import annotations

import sys

from whysync import actions, config
from whysync.fmt import done_summary
from whysync.i18n import init_language, t
from whysync.ui.add_dialog import AddPairDialog
from whysync.ui.approve_dialog import ApproveDialog
from whysync.ui.detail import DetailHandlers, PairDetail
from whysync.ui.gi_ready import Adw, Gio, GLib, Gtk
from whysync.ui.model import folder_title, row_view
from whysync.ui.sidebar_row import SidebarRow
from whysync.ui.trash_dialog import TrashDialog

APP_ID = "com.github.byrdltd.whysync"
REFRESH_MS = 1000
# Below this width the two panes become one column that slides to the detail.
NARROW_SP = 620


def prepare_gtk() -> bool:
    """Open the display and drop the GTK dark-theme flag before libadwaita starts.

    KDE writes gtk-application-prefer-dark-theme=true into gtk-4.0/settings.ini.
    libadwaita does not support that flag: the window comes out dark while
    some symbolic icons keep their light-theme colour and vanish (the header's
    "+" was dark on dark). It has to go before Adw initialises; the colour
    scheme then comes from AdwStyleManager, which follows the desktop.
    """
    if not Gtk.init_check():
        return False
    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-application-prefer-dark-theme", False)
    return True


class MainWindow(Adw.ApplicationWindow):
    """Pairs on the left, the selected pair on the right; one column when narrow."""

    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, default_width=900, default_height=620, title="whySYNC")
        self._rows: dict[str, SidebarRow] = {}
        self._pairs: dict[str, config.Pair] = {}
        self._states: dict = {}
        self._service_up = False
        self._selected = ""
        self._config_error_shown = False
        # last_sync["at"] per pair, to announce rounds that finish while the window is open
        self._seen_rounds: dict[str, float] = {}

        self._title = Adw.WindowTitle(title="whySYNC")
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=t("ui.add.title"))
        add.connect("clicked", lambda *_: self.open_add())
        header = Adw.HeaderBar(title_widget=self._title)
        header.pack_start(add)

        self._banner = Adw.Banner(title=t("ui.banner.offline"), button_label=t("ui.banner.start"))
        self._banner.connect("button-clicked", lambda *_: self._start_service())

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("navigation-sidebar")
        self._list.connect("row-activated", lambda _l, row: self.select(row.pair_id))
        sidebar_view = Adw.ToolbarView(content=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                                  child=self._list))
        sidebar_view.add_top_bar(header)
        sidebar_view.add_top_bar(self._banner)

        self._detail = PairDetail(DetailHandlers(
            sync=self._sync, pause=self._pause, review=self._review,
            remove=self._remove, trash=self._trash, open_folder=self._open_folder,
        ))
        empty_add = Gtk.Button(label=t("ui.add.title"), halign=Gtk.Align.CENTER)
        empty_add.add_css_class("suggested-action")
        empty_add.connect("clicked", lambda *_: self.open_add())
        empty = Adw.StatusPage(icon_name="drive-harddisk-symbolic", title=t("ui.empty.title"),
                               description=t("ui.empty.body"), child=empty_add)
        empty_view = Adw.ToolbarView(content=empty)
        empty_view.add_top_bar(Adw.HeaderBar(show_title=False))
        self._content = Gtk.Stack()
        self._content.add_named(empty_view, "empty")
        self._content.add_named(self._detail, "detail")

        self._split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="whySYNC", child=sidebar_view),
            content=Adw.NavigationPage(title="whySYNC", child=self._content),
            min_sidebar_width=240, max_sidebar_width=320,
        )
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {NARROW_SP}sp"))
        narrow.add_setter(self._split, "collapsed", True)
        self.add_breakpoint(narrow)
        self._toasts = Adw.ToastOverlay(child=self._split)
        self.set_content(self._toasts)

        self.refresh()
        GLib.timeout_add(REFRESH_MS, self._tick)

    # --- state ---

    def _tick(self) -> bool:
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def select(self, pid: str) -> None:
        self._selected = pid
        row = self._rows.get(pid)
        if row is not None and self._list.get_selected_row() is not row:
            self._list.select_row(row)
        self._show_detail()
        self._split.set_show_content(True)

    def refresh(self) -> None:
        try:
            cfg = config.load()
        except config.ConfigError as exc:
            if not self._config_error_shown:
                self.toast(t(str(exc)))
                self._config_error_shown = True
            return
        self._config_error_shown = False
        info = actions.service_info()
        self._service_up = info is not None
        self._states = actions.pair_states() if info else {}
        self._title.set_subtitle(t("ui.service.up") if info else t("ui.service.down"))
        self._banner.set_revealed(info is None)

        wanted = [p.id for p in cfg.pairs]
        for pid in list(self._rows):
            if pid not in wanted:
                self._list.remove(self._rows.pop(pid))
        for index, pair in enumerate(cfg.pairs):
            row = self._rows.get(pair.id)
            if row is None:
                row = SidebarRow(pair.id)
                self._rows[pair.id] = row
                self._list.insert(row, index)
            row.update(self._view(pair))
        self._pairs = {p.id: p for p in cfg.pairs}
        if self._selected not in self._pairs:
            self._selected = cfg.pairs[0].id if cfg.pairs else ""
            if self._selected:
                self._list.select_row(self._rows[self._selected])
        self._show_detail()
        self._announce_finished_rounds()

    def _view(self, pair: config.Pair):
        return row_view(pair, self._states.get(pair.id, {}), self._service_up)

    def _show_detail(self) -> None:
        pair = self._pairs.get(self._selected)
        if pair is None:
            self._content.set_visible_child_name("empty")
            return
        self._content.set_visible_child_name("detail")
        self._detail.show(pair, self._view(pair), self._states.get(pair.id, {}))

    def _announce_finished_rounds(self) -> None:
        for pid, pair in self._pairs.items():
            last = self._states.get(pid, {}).get("last_sync") or {}
            at = last.get("at")
            if not at:
                continue
            seen = self._seen_rounds.get(pid)
            self._seen_rounds[pid] = at
            if seen is not None and at != seen:
                self.toast(t("ui.toast.done", name=folder_title(pair.source), summary=done_summary(last)))

    def toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=text, timeout=3, use_markup=False))

    def _run(self, action, done_key: str | None = None, **params) -> None:
        try:
            action()
        except actions.ActionError as exc:
            self.toast(t(exc.key, **exc.params))
            return
        if done_key:
            self.toast(t(done_key, **params))
        self.refresh()

    # --- actions ---

    def open_add(self) -> AddPairDialog:
        dialog = AddPairDialog(on_added=self._added)
        dialog.present(self)
        return dialog

    def _added(self, pair: config.Pair) -> None:
        # Show the new pair: its first round may stop to ask, and the detail
        # pane would otherwise keep showing whichever pair was selected before.
        self.toast(t("ui.added", name=folder_title(pair.source)))
        self.refresh()
        if pair.id in self._rows:
            self.select(pair.id)

    def _start_service(self) -> None:
        if not actions.start_service():
            self.toast(t("ui.banner.start_failed"))
        GLib.timeout_add(700, lambda: (self.refresh(), GLib.SOURCE_REMOVE)[1])

    def _sync(self, pid: str) -> None:
        self._run(lambda: actions.sync_now(pid), "ui.sync_requested")

    def _pause(self, pid: str, paused: bool) -> None:
        self._run(lambda: actions.set_paused(pid, paused))

    def _review(self, pid: str) -> ApproveDialog | None:
        held = self._states.get(pid, {}).get("held")
        pair = self._pairs.get(pid)
        if not held or pair is None:
            return None
        dialog = ApproveDialog(
            pair.target, held,
            on_approve=lambda fp: self._run(lambda: actions.approve(pid, fp), "ui.approved"),
            on_adopt=lambda fp: self._run(lambda: actions.adopt(pid, fp), "ui.adopted"),
        )
        dialog.present(self)
        return dialog

    def _remove(self, pid: str) -> Adw.AlertDialog | None:
        pair = self._pairs.get(pid)
        if pair is None:
            return None
        dialog = Adw.AlertDialog(heading=t("ui.remove_confirm.title", name=folder_title(pair.source)),
                                 body=t("ui.remove_confirm.body"))
        dialog.add_response("cancel", t("ui.cancel"))
        dialog.add_response("remove", t("ui.remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def chosen(_dlg, response: str) -> None:
            if response == "remove":
                self._run(lambda: actions.remove_pair(pid), "ui.removed", name=folder_title(pair.source))

        dialog.connect("response", chosen)
        dialog.present(self)
        return dialog

    def _trash(self, pid: str) -> TrashDialog | None:
        pair = self._pairs.get(pid)
        if pair is None:
            return None
        dialog = TrashDialog(pair, self.toast)
        dialog.present(self)
        return dialog

    def _open_folder(self, path: str) -> None:
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self, None, None)


class WhySyncApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("activate", self._activate)

    def _activate(self, app: Adw.Application) -> None:
        window = self.get_active_window() or MainWindow(app)
        window.present()


def run(argv: list[str] | None = None) -> int:
    try:
        init_language(config.load().language)
    except config.ConfigError:
        init_language(None)
    if not prepare_gtk():
        print("whysync: no display", file=sys.stderr)
        return 1
    GLib.set_application_name("whySYNC")
    Gtk.Window.set_default_icon_name(APP_ID)
    return WhySyncApp().run([sys.argv[0], *(argv or [])])

