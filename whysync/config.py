"""Folder pairs — reading, writing and validating `pairs.json`.

The CLI and the UI write the file; the service notices the change and reads
it. The schema version is stored in the file; unknown fields are ignored.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from whysync import paths

SCHEMA = 1
TRASH_DIRNAME = ".whysync-trash"


class ConfigError(Exception):
    """Settings file unreadable or corrupt. The message is an i18n key."""


@dataclass
class Pair:
    id: str
    source: str
    target: str
    paused: bool = False
    debounce_s: float = 10.0
    # Continuous writes must not postpone a sync forever.
    debounce_cap_s: float = 300.0
    # Catches changes made while the service was off or from another OS.
    full_sync_s: float = 3600.0
    max_deletes: int = 50
    trash_days: int = 30
    # Warn when a pair has not finished a round for this many days (0: never).
    stale_days: int = 7
    # Compare contents every this many days to catch copies that went bad (0: never).
    verify_days: int = 30
    excludes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> Pair:
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        return cls(**data)


@dataclass
class Config:
    pairs: list[Pair] = field(default_factory=list)
    language: str = "system"

    def find(self, pair_id: str) -> Pair | None:
        return next((p for p in self.pairs if p.id == pair_id), None)


def load(path: Path | None = None) -> Config:
    path = path or paths.config_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError) as exc:
        raise ConfigError("config.unreadable") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("pairs", []), list):
        raise ConfigError("config.unreadable")
    try:
        pairs = [Pair.from_dict(p) for p in raw.get("pairs", [])]
    except TypeError as exc:
        raise ConfigError("config.unreadable") from exc
    return Config(pairs=pairs, language=str(raw.get("language") or "system"))


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or paths.config_file()
    data = {
        "schema": SCHEMA,
        "language": cfg.language,
        "pairs": [asdict(p) for p in cfg.pairs],
    }
    paths.write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def validate_pair(source: str, target: str, existing: list[Pair] = ()) -> str | None:
    """Returns an i18n key when the pair is invalid."""
    src, dst = Path(source), Path(target)
    if not src.is_absolute() or not dst.is_absolute():
        return "pair.not_absolute"
    src, dst = src.resolve(), dst.resolve()
    if src == dst:
        return "pair.same"
    # A nested pair feeds itself: with the target inside the source every
    # sync produces a new change event.
    if src.is_relative_to(dst) or dst.is_relative_to(src):
        return "pair.nested"
    for other in existing:
        if Path(other.target).resolve() == dst:
            return "pair.target_taken"
    return None


def _slug(text: str) -> str:
    text = text.replace("ı", "i").replace("İ", "I")
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-") or "pair"


def new_pair_id(cfg: Config, source: str) -> str:
    base = _slug(Path(source).name)
    taken = {p.id for p in cfg.pairs}
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate
