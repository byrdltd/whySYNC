"""The per-pair trash: browse rounds, put files back.

Every round that deletes or overwrites anything gets its own folder,
`<target>/.whysync-trash/<time>/`, laid out like the pair itself. Nothing
here touches the desktop trash or another pair's trash.

Files go back to the *source*: the source is the truth, so a file restored
into the target would be mirrored away again on the next round. From the
source it is copied to the target as usual. A file that already exists at
its old place is kept, and the restored copy gets the round in its name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from whysync import safecopy
from whysync.config import TRASH_DIRNAME, Pair
from whysync.engine import readiness, stamp_time


class RestoreError(Exception):
    """`key` is an i18n key."""

    def __init__(self, key: str, **params) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


@dataclass(frozen=True)
class TrashFile:
    path: str  # relative to the round (and to the pair root)
    size: int


@dataclass
class TrashRound:
    name: str
    at: float
    files: list[TrashFile] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


@dataclass
class RestoreResult:
    restored: list[tuple[str, str]] = field(default_factory=list)  # (trash path, where it went)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (trash path, error)

    @property
    def renamed(self) -> int:
        return sum(1 for rel, dest in self.restored if not dest.endswith(rel))


def trash_root(pair: Pair) -> Path:
    return Path(pair.target) / TRASH_DIRNAME


def rounds(pair: Pair) -> list[TrashRound]:
    """Newest first. Folders that are not ours are ignored."""
    root = trash_root(pair)
    if not root.is_dir():
        return []
    found: list[TrashRound] = []
    for entry in root.iterdir():
        at = stamp_time(entry.name)
        if at is None or not entry.is_dir():
            continue
        files = [
            TrashFile(path.relative_to(entry).as_posix(), path.stat().st_size)
            for path in sorted(entry.rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]
        if files:
            found.append(TrashRound(entry.name, at, files))
    return sorted(found, key=lambda r: (r.at, r.name), reverse=True)


def _round_dir(pair: Pair, name: str) -> Path:
    if Path(name).name != name or stamp_time(name) is None:
        raise RestoreError("restore.no_round", round=name)
    folder = trash_root(pair) / name
    if not folder.is_dir():
        raise RestoreError("restore.no_round", round=name)
    return folder


def _inside(base: Path, rel: str) -> Path:
    path = (base / safecopy.relative(rel)).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(rel)
    return path


def _prune(folder: Path, stop: Path) -> None:
    """Remove folders left empty, up to and including the round folder."""
    while folder != stop.parent and folder.is_relative_to(stop):
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def restore(pair: Pair, round_name: str, paths: list[str] | None = None) -> RestoreResult:
    """Copy files of a round back into the source; `paths=None` restores all of it."""
    if readiness(pair) in ("reason.source_missing", "reason.source_unreadable"):
        raise RestoreError("restore.source_missing")
    folder = _round_dir(pair, round_name)
    source = Path(pair.source)
    wanted = paths if paths is not None else [f.path for f in next(
        (r for r in rounds(pair) if r.name == round_name), TrashRound(round_name, 0)).files]

    result = RestoreResult()
    for rel in wanted:
        try:
            item = _inside(folder, rel)
            dest = safecopy.copy_new(item, source, rel, rename=f"whySYNC {round_name}")
            item.unlink()
            _prune(item.parent, folder)
            result.restored.append((rel, dest.relative_to(source).as_posix()))
        except (OSError, ValueError) as exc:
            result.failed.append((rel, str(exc)))
    return result
