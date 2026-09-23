"""Compact file lists by kind: what a round copied / updated / sent to trash.

The same block serves the wizard's preview (what the first round will do)
and the detail pane's last round (what it did).
"""
from __future__ import annotations

from whysync.i18n import t, tn
from whysync.ui.gi_ready import Adw, Gtk, Pango

# (kind, icon, what a round did, what a round will do)
KINDS = (
    ("created", "document-new-symbolic", "ui.kind.created", "ui.kind.planned.created"),
    ("updated", "view-refresh-symbolic", "ui.kind.updated", "ui.kind.planned.updated"),
    ("deleted", "user-trash-symbolic", "ui.kind.deleted", "ui.kind.planned.target_only"),
)


def kind_rows(group: Adw.PreferencesGroup, counts: dict[str, int], samples: dict[str, list[str]],
              *, planned: bool = False) -> list:
    """One expander per non-empty kind; returns the rows so callers can remove them.

    `planned` words the titles for what a round will do rather than what it did.
    """
    rows = []
    for key, icon, done_label, planned_label in KINDS:
        count = counts.get(key, 0)
        if not count:
            continue
        label = planned_label if planned else done_label
        row = Adw.ExpanderRow(title=tn(label, count), use_markup=False)
        row.add_prefix(Gtk.Image(icon_name=icon))
        shown = samples.get(key, [])
        for path in shown:
            item = Gtk.Label(label=path, xalign=0.0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                             margin_top=4, margin_bottom=4, margin_start=12, margin_end=12, tooltip_text=path)
            item.add_css_class("monospace")
            item.add_css_class("caption")
            row.add_row(item)
        if count > len(shown):
            more = Gtk.Label(label=t("cli.held_more", more=count - len(shown)), xalign=0.0,
                             margin_top=4, margin_bottom=6, margin_start=12)
            more.add_css_class("dim-label")
            row.add_row(more)
        group.add(row)
        rows.append(row)
    return rows

