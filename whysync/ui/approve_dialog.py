"""Review a held round: which files would go, and what to do with them.

Two kinds of hold look alike but ask different questions:

* over the deletion limit: move these files to trash, or leave the round held;
* a new pair's first round with files only in the target: those may be the
  only copy of something, so keep them (copy into the source) or trash them.
"""
from __future__ import annotations

from collections.abc import Callable

from whysync.i18n import t, tn
from whysync.ui.gi_ready import Adw, Gtk, Pango
from whysync.ui.model import folder_title


class ApproveDialog(Adw.Dialog):
    def __init__(
        self,
        target: str,
        held: dict,
        on_approve: Callable[[str], None],
        on_adopt: Callable[[str], None],
    ) -> None:
        super().__init__(content_width=520, content_height=560)
        self._fingerprint = held.get("fingerprint", "")
        self._on_approve = on_approve
        self._on_adopt = on_adopt
        self.first_round = held.get("kind") == "first_round"
        count = int(held.get("count", 0))
        sample = list(held.get("sample", []))
        name = folder_title(target)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=6, margin_bottom=18, margin_start=18, margin_end=18)
        if self.first_round:
            lead_text = tn("ui.first.body", count, target=name)
            note_text = t("ui.first.note")
        else:
            lead_text = t("ui.approve.body", count=count, target=name, source_files=held.get("source_files", 0))
            note_text = t("cli.held_trash")
        body.append(Gtk.Label(wrap=True, xalign=0.0, label=lead_text))
        note = Gtk.Label(wrap=True, xalign=0.0, label=note_text)
        note.add_css_class("dim-label")
        body.append(note)

        files = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        files.add_css_class("boxed-list")
        for path in sample:
            label = Gtk.Label(label=path, xalign=0.0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                              margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
            label.add_css_class("monospace")
            label.set_tooltip_text(path)
            files.append(label)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(files)
        body.append(scroller)
        if count > len(sample):
            more = Gtk.Label(xalign=0.0, label=t("cli.held_more", more=count - len(sample)))
            more.add_css_class("dim-label")
            body.append(more)

        self.trash_button = Gtk.Button(label=tn("ui.first.trash" if self.first_round else "ui.approve.go", count))
        self.trash_button.add_css_class("destructive-action")
        self.trash_button.connect("clicked", lambda *_: self._approve())
        self.adopt_button = Gtk.Button(label=tn("ui.first.adopt", count), visible=self.first_round)
        self.adopt_button.add_css_class("suggested-action")
        self.adopt_button.connect("clicked", lambda *_: self._adopt())

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(
            title=t("ui.first.title" if self.first_round else "ui.approve.title")))
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        if self.first_round:
            actions = Gtk.Box(spacing=12, halign=Gtk.Align.END, margin_start=18, margin_end=18,
                              margin_top=6, margin_bottom=12)
            actions.append(self.trash_button)
            actions.append(self.adopt_button)
            view.add_bottom_bar(actions)
        else:
            header.pack_end(self.trash_button)
        self.set_child(view)

    def _approve(self) -> None:
        self._on_approve(self._fingerprint)
        self.close()

    def _adopt(self) -> None:
        self._on_adopt(self._fingerprint)
        self.close()
