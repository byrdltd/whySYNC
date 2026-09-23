"""User-visible strings. English is the source catalog; Turkish overlays it.

The CLI, notifications and the UI call ``t(key)``. Journal lines are not
translated.
"""
from __future__ import annotations

import os
from typing import Any

from .en import EN
from .tr import TR

LANG_SYSTEM = "system"
LANG_EN = "en"
LANG_TR = "tr"
SUPPORTED = (LANG_EN, LANG_TR)

_CATALOGS = {LANG_EN: EN, LANG_TR: TR}
_lang = LANG_EN


def detect_system_language() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        raw = (os.environ.get(var) or "").strip()
        if not raw or raw in ("C", "C.UTF-8", "POSIX"):
            continue
        token = raw.split(":")[0].split(".")[0].split("_")[0].lower()
        return LANG_TR if token == LANG_TR else LANG_EN
    return LANG_EN


def resolve_language(pref: str | None) -> str:
    raw = (pref or LANG_SYSTEM).strip().lower()
    return raw if raw in SUPPORTED else detect_system_language()


def set_language(lang: str) -> str:
    global _lang
    _lang = lang if lang in SUPPORTED else LANG_EN
    return _lang


def current_language() -> str:
    return _lang


def init_language(pref: str | None = None) -> str:
    return set_language(resolve_language(pref))


def tn(key: str, count: int, **kwargs: Any) -> str:
    """Like `t`, but uses `<key>.one` for a count of one when the active catalog has it.

    Only the active catalog counts: Turkish has no singular form, and falling
    back to the English `.one` would put English text into a Turkish window.
    """
    catalog = _CATALOGS.get(_lang) or EN
    if count == 1 and f"{key}.one" in catalog:
        key = f"{key}.one"
    return t(key, count=count, **kwargs)


def t(key: str | None, **kwargs: Any) -> str:
    if not key:
        return ""
    catalog = _CATALOGS.get(_lang) or EN
    template = catalog.get(key) or EN.get(key) or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template
