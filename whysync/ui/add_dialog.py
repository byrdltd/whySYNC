"""Add-pair wizard: source → target → review. One primary button per step."""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from whysync import actions, config, engine
from whysync.fmt import size
from whysync.i18n import t
from whysync.ui.gi_ready import Adw, Gio, GLib, Gtk
from whysync.ui.model import folder_title
from whysync.ui.round_view import kind_rows

STEPS = ("source", "target", "review")
PREVIEW_ROWS = 100


class AddPairDialog(Adw.Dialog):
    def __init__(self, on_added: Callable[[config.Pair], None]) -> None:
        super().__init__(content_width=520, follows_content_size=True)
        self._on_added = on_added
        self._step = 0
        self._paths: dict[str, str] = {"source": "", "target": ""}
        self._preview_rows: list = []
        self._preview_key: tuple[str, str] | None = None
        self._preview_gen = 0
        self._preview_proc = None
        self._plan = None
        self._held: Adw.ActionRow | None = None
        self.connect("closed", lambda *_: self._stop_preview())

        self._title = Adw.WindowTitle()
        self._back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=t("ui.add.back"))
        self._back.connect("clicked", lambda *_: self._go(self._step - 1))
        self._next = Gtk.Button()
        self._next.add_css_class("suggested-action")
        self._next.connect("clicked", lambda *_: self._advance())
        header = Adw.HeaderBar(title_widget=self._title)
        header.pack_start(self._back)
        header.pack_end(self._next)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vhomogeneous=False)
        self._pick_rows: dict[str, Adw.ActionRow] = {}
        for key in ("source", "target"):
            self._stack.add_named(self._pick_page(key), key)
        self._stack.add_named(self._review_page(), "review")

        self._stack.set_size_request(520, -1)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                      max_content_height=560, child=self._stack)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(scroller)
        self.set_child(view)
        self._go(0)

    # --- pages ---

    def _page(self, hint: str) -> tuple[Gtk.Box, Adw.PreferencesGroup]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        label = Gtk.Label(label=hint, wrap=True, xalign=0.0)
        label.add_css_class("dim-label")
        box.append(label)
        group = Adw.PreferencesGroup()
        box.append(group)
        return box, group

    def _pick_page(self, key: str) -> Gtk.Widget:
        box, group = self._page(t(f"ui.add.{key}_hint"))
        row = Adw.ActionRow(activatable=True, use_markup=False)
        row.add_prefix(Gtk.Image(icon_name="folder-symbolic" if key == "source" else "drive-harddisk-symbolic"))
        row.add_suffix(Gtk.Image(icon_name="folder-open-symbolic"))
        row.connect("activated", lambda *_: self._choose(key))
        group.add(row)
        self._pick_rows[key] = row
        if key == "target":
            self._error = Gtk.Label(wrap=True, xalign=0.0, visible=False)
            self._error.add_css_class("error")
            box.append(self._error)
        return box

    def _review_page(self) -> Gtk.Widget:
        box, group = self._page(t("ui.add.review_hint"))
        self._review_source = Adw.ActionRow(use_markup=False)
        self._review_source.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
        self._review_target = Adw.ActionRow(use_markup=False)
        self._review_target.add_prefix(Gtk.Image(icon_name="drive-harddisk-symbolic"))
        self._limit = Adw.SpinRow.new_with_range(1, 100000, 1)
        self._limit.set_value(50)
        self._limit.set_title(t("ui.add.limit"))
        self._limit.set_subtitle(t("ui.add.limit_hint"))
        for row in (self._review_source, self._review_target):
            row.add_css_class("property")
            group.add(row)
        limits = Adw.PreferencesGroup()
        limits.add(self._limit)
        box.append(limits)
        self._preview = Adw.PreferencesGroup(title=t("ui.preview.title"))
        box.append(self._preview)
        return box

    # --- preview: what the first round will do ---

    def _clear_preview(self) -> None:
        for row in self._preview_rows:
            self._preview.remove(row)
        self._preview_rows = []
        self._held = None
        self._preview.set_description(None)

    def _stop_preview(self) -> None:
        self._preview_gen += 1
        proc = self._preview_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self._preview_proc = None

    def _start_preview(self) -> None:
        key = (self._paths["source"], self._paths["target"])
        if key == self._preview_key or not all(key):
            return
        self._stop_preview()
        self._preview_key = key
        gen = self._preview_gen
        self._clear_preview()
        running = Adw.ActionRow(title=t("ui.preview.running"), subtitle=t("ui.preview.running_hint"))
        spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        spinner.start()
        running.add_suffix(spinner)
        self._preview.add(running)
        self._preview_rows.append(running)
        pair = config.Pair(id="preview", source=key[0], target=key[1])

        def track(proc) -> None:
            self._preview_proc = proc

        def work() -> None:
            result = engine.plan_sync(pair, on_proc=track)
            GLib.idle_add(self._show_preview, gen, result)

        threading.Thread(target=work, daemon=True, name="preview").start()

    def _show_preview(self, gen: int, result) -> bool:
        if gen != self._preview_gen:
            return GLib.SOURCE_REMOVE  # stale: paths changed or the dialog closed
        self._preview_proc = None
        self._clear_preview()
        self._plan = None
        if isinstance(result, engine.Outcome):
            row = Adw.ActionRow(title=t(result.reason), subtitle=result.detail, use_markup=False)
            row.add_css_class("error")
            self._preview.add(row)
            self._preview_rows.append(row)
        elif result.empty:
            row = Adw.ActionRow(title=t("ui.preview.same"))
            row.add_prefix(Gtk.Image(icon_name="object-select-symbolic"))
            self._preview.add(row)
            self._preview_rows.append(row)
        else:
            self._plan = result
            self._preview.set_description(t("ui.preview.summary", size=size(result.bytes)))
            deletions = result.file_deletions
            self._preview_rows += kind_rows(
                self._preview,
                {"created": result.creates, "updated": result.updates, "deleted": len(deletions)},
                {"created": result.created[:PREVIEW_ROWS], "updated": result.updated[:PREVIEW_ROWS],
                 "deleted": deletions[:PREVIEW_ROWS]},
                planned=True,
            )
            # A new pair's first round: the deletion limit does not matter here.
            preview = config.Pair(id="preview", source="", target="")
            if engine.hold_reason(preview, result, first_round=True):
                self._held = Adw.ActionRow(title=t("ui.preview.first"), use_markup=False)
                self._held.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
                self._held.add_css_class("warning")
                self._preview.add(self._held)
                self._preview_rows.append(self._held)
        return GLib.SOURCE_REMOVE

    # --- flow ---

    def _go(self, step: int) -> None:
        self._step = max(0, min(step, len(STEPS) - 1))
        key = STEPS[self._step]
        self._stack.set_visible_child_name(key)
        self._title.set_title(t(f"ui.add.step_{key}"))
        self._title.set_subtitle(t("ui.add.progress", n=self._step + 1, total=len(STEPS)))
        self._back.set_visible(self._step > 0)
        self._next.set_label(t("ui.add.add") if key == "review" else t("ui.add.next"))
        self._refresh()

    def _problem(self) -> str | None:
        source, target = self._paths["source"], self._paths["target"]
        if not (source and target):
            return None
        return config.validate_pair(source, target, config.load().pairs)

    def _refresh(self) -> None:
        for key, row in self._pick_rows.items():
            path = self._paths[key]
            row.set_title(folder_title(path) if path else t("ui.add.choose"))
            row.set_subtitle(path)
        problem = self._problem()
        self._error.set_visible(problem is not None)
        self._error.set_label(t(problem) if problem else "")
        key = STEPS[self._step]
        ready = {
            "source": bool(self._paths["source"]),
            "target": bool(self._paths["target"]) and problem is None,
            "review": problem is None,
        }[key]
        self._next.set_sensitive(ready)
        if key == "review":
            for row, which in ((self._review_source, "source"), (self._review_target, "target")):
                row.set_title(folder_title(self._paths[which]))
                row.set_subtitle(self._paths[which])
            if problem is None:
                self._start_preview()

    def _choose(self, key: str) -> None:
        dialog = Gtk.FileDialog(title=t(f"ui.add.step_{key}"), modal=True)
        current = self._paths[key]
        if current:
            dialog.set_initial_folder(Gio.File.new_for_path(current))

        def done(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return  # cancelled
            if folder is not None and folder.get_path():
                self.set_path(key, folder.get_path())

        dialog.select_folder(self.get_root(), None, done)

    def set_path(self, key: str, path: str) -> None:
        self._paths[key] = str(Path(path))
        self._refresh()

    def _advance(self) -> None:
        if STEPS[self._step] != "review":
            self._go(self._step + 1)
            return
        try:
            pair = actions.add_pair(self._paths["source"], self._paths["target"], int(self._limit.get_value()))
        except actions.ActionError as exc:
            self._go(1)
            self._error.set_label(t(exc.key, **exc.params))
            self._error.set_visible(True)
            return
        self._on_added(pair)
        self.close()
