"""Human-readable sizes and durations, in the active language."""
from __future__ import annotations

from whysync.i18n import current_language, t


def _decimal(text: str) -> str:
    return text.replace(".", ",") if current_language() == "tr" else text


def size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000:
            return _decimal(f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}")
        n /= 1000
    return _decimal(f"{n:.1f} TB")


def duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return t("ui.dur.s", s=seconds)
    minutes = seconds // 60
    if minutes < 60:
        return t("ui.dur.m", m=minutes)
    return t("ui.dur.hm", h=minutes // 60, m=minutes % 60)


def clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds < 3600 else f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def done_summary(last: dict) -> str:
    """"1204 copied · 12 updated · 3 to trash · 3.2 GB · 4 min" style line."""
    parts = []
    for key, label in (("adopted", "summary.adopted"), ("created", "summary.created"),
                       ("updated", "summary.updated"), ("deleted", "summary.deleted")):
        if last.get(key):
            parts.append(t(label, count=last[key]))
    if last.get("bytes"):
        parts.append(size(last["bytes"]))
    if last.get("duration"):
        parts.append(duration(last["duration"]))
    return " · ".join(parts) or t("summary.nothing")
