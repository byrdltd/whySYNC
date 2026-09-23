"""What the sidebar row and the detail pane show for a pair. No GTK here."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from whysync import status
from whysync.config import Pair
from whysync.fmt import clock, done_summary, duration, size
from whysync.i18n import t, tn

# state → (icon, style class)
_LOOK = {
    "idle": ("object-select-symbolic", "success"),
    "syncing": ("", ""),  # the row shows a spinner instead
    "waiting": ("drive-harddisk-symbolic", "dim-label"),
    "held": ("dialog-warning-symbolic", "warning"),
    "error": ("dialog-error-symbolic", "error"),
    "paused": ("media-playback-pause-symbolic", "dim-label"),
    "offline": ("content-loading-symbolic", "dim-label"),
    "unknown": ("content-loading-symbolic", "dim-label"),
}


@dataclass(frozen=True)
class RowView:
    pair_id: str
    title: str
    subtitle: str
    state: str
    icon: str
    style: str
    paused: bool
    can_sync: bool
    held_count: int
    # files whose contents differ from the source (last content check)
    mismatch: int = 0
    # None: no bar; PULSE: busy with no known end; otherwise 0.0–1.0
    progress: float | None = None
    # the numbers behind the bar, and the file being copied (full relative path)
    detail: str = ""
    current: str = ""


PULSE = -1.0


def syncing_view(progress: dict, now: float) -> tuple[str, float, str, str]:
    """(one-line headline, bar fraction or PULSE, the numbers behind it, file being copied)."""
    phase = progress.get("phase") or "compare"
    elapsed = clock(now - progress.get("since", now))
    if phase != "copy":
        return t(f"ui.sub.{phase}", elapsed=elapsed), PULSE, "", ""
    bytes_total = progress.get("bytes_total", 0)
    bytes_done = min(progress.get("bytes_done", 0), bytes_total)
    files_total = progress.get("files_total", 0)
    rate = progress.get("rate", 0)
    fraction = bytes_done / bytes_total if bytes_total else PULSE

    parts = [t("ui.sub.copy_pct", pct=int(fraction * 100)) if bytes_total else t("ui.sub.copy")]
    if rate and bytes_total > bytes_done:
        parts.append(t("ui.left", time=duration((bytes_total - bytes_done) / rate)))

    numbers = []
    if files_total:
        numbers.append(t("ui.files_of", done=min(progress.get("files_done", 0), files_total), total=files_total))
    if bytes_total:
        numbers.append(f"{size(bytes_done)} / {size(bytes_total)}")
    if rate:
        numbers.append(f"{size(rate)}/{t('ui.per_second')}")
    numbers.append(t("ui.elapsed", time=elapsed))
    return " · ".join(parts), fraction, " · ".join(numbers), progress.get("current", "")


def last_summary(last: dict, now: float | None = None) -> str:
    """"14:02 · 1204 copied · 3.2 GB · 4 min" for the last round that changed something."""
    if not last.get("at"):
        return ""
    return f"{when(last['at'], now)} · {done_summary(last)}"


def when(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return ""
    now = time.time() if now is None else now
    age = max(0.0, now - ts)
    if age < 60:
        return t("ui.when.now")
    if age < 3600:
        return t("ui.when.minutes", n=int(age // 60))
    if time.localtime(ts)[:3] == time.localtime(now)[:3]:
        return time.strftime("%H:%M", time.localtime(ts))
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def folder_title(path: str) -> str:
    return Path(path).name or path


def row_view(pair: Pair, st: dict, service_up: bool, now: float | None = None) -> RowView:
    if pair.paused:
        state = "paused"
    elif not service_up:
        state = "offline"
    else:
        state = st.get("state") or "unknown"

    held = st.get("held") or {}
    progress: float | None = PULSE if state == "syncing" else None
    detail = current = ""
    reason = t(st.get("reason")) if st.get("reason") else ""
    if state == "idle":
        checked = when(st.get("checked_at"), now)
        subtitle = t("ui.sub.idle", when=checked) if checked else t("ui.sub.plain.idle")
        if st.get("verifying"):
            subtitle += " · " + t("ui.sub.verifying")
    elif state == "waiting":
        subtitle = t("ui.sub.waiting", reason=reason) if reason else t("ui.sub.plain.waiting")
    elif state == "held":
        key = "ui.sub.first" if held.get("kind") == "first_round" else "ui.sub.held"
        subtitle = tn(key, held.get("count", 0))
    elif state == "error":
        subtitle = t("ui.sub.error", reason=reason) if reason else t("ui.sub.plain.error")
    elif state == "offline":
        subtitle = t("ui.sub.offline")
    elif state == "syncing" and st.get("progress"):
        subtitle, progress, detail, current = syncing_view(st["progress"], time.time() if now is None else now)
    else:
        subtitle = t(f"ui.sub.plain.{state}")

    icon, style = _LOOK.get(state, _LOOK["unknown"])
    mismatch = int((st.get("verify") or {}).get("count", 0))
    if mismatch and state == "idle":
        subtitle = tn("ui.sub.mismatch", mismatch)
        icon, style = _LOOK["held"]
    if state in ("waiting", "held", "error") and (days := status.stale_days(pair, st, now)):
        subtitle += " · " + tn("ui.sub.stale", days)
        style = "error" if state == "error" else "warning"
    return RowView(
        pair_id=pair.id,
        title=folder_title(pair.source),
        subtitle=subtitle,
        state=state,
        icon=icon,
        style=style,
        paused=pair.paused,
        can_sync=service_up and not pair.paused and state not in ("syncing", "waiting"),
        held_count=int(held.get("count", 0)) if state == "held" else 0,
        mismatch=mismatch,
        progress=progress,
        detail=detail,
        current=current,
    )
