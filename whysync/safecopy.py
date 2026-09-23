"""Copy a single file into a pair root without ever creating the root or overwriting.

Keeping a target-only file (first round) and restoring from the trash both write
into the *source*. The source may sit on a disk that is unplugged at any
moment, so the same rule as the target applies: a missing root is never
created. Folders below the root are made one level at a time, so if the root
disappears half-way the next `mkdir` fails instead of rebuilding the path on
the system disk.
"""
from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path, PurePosixPath


class Refused(OSError):
    """The copy was not made; `str()` says why."""


def relative(rel: str) -> PurePosixPath:
    """A path inside a root: relative, no `..`, not empty."""
    path = PurePosixPath(rel)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise Refused(errno.EINVAL, "not a path inside the pair", rel)
    return path


def ensure_dirs(root: Path, rel: PurePosixPath) -> Path:
    """Create `root/rel` folder by folder; `root` itself must already exist."""
    if not root.is_dir():
        raise Refused(errno.ENOENT, "root is missing", str(root))
    real_root = root.resolve()
    folder = root
    for part in rel.parts:
        folder = folder / part
        try:
            folder.mkdir()
        except FileExistsError:
            pass
        if folder.is_symlink() or not folder.resolve().is_relative_to(real_root):
            raise Refused(errno.ELOOP, "folder leads outside the pair", str(folder))
    return folder


def _commit(partial: Path, dest: Path) -> None:
    """Give `partial` its final name only if nothing is there yet."""
    try:
        os.link(partial, dest)
    except FileExistsError:
        raise
    except OSError as exc:
        # exFAT / FAT have no hard links; fall back to a checked rename.
        if exc.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.EMLINK, errno.ENOSYS):
            raise
        if dest.exists() or dest.is_symlink():
            raise FileExistsError(errno.EEXIST, "exists", str(dest)) from None
        os.rename(partial, dest)
        return
    partial.unlink()


def copy_new(src: Path, root: Path, rel: str, *, rename: str | None = None) -> Path:
    """Copy `src` to `root/rel` and return where it went.

    Never overwrites. With `rename`, a taken name becomes
    "<stem> (<rename>)<suffix>", "<stem> (<rename> 2)<suffix>", …;
    without it a taken name raises FileExistsError.
    """
    path = relative(rel)
    if src.is_symlink() or not src.is_file():
        raise Refused(errno.EINVAL, "not a regular file", str(src))
    folder = ensure_dirs(root, path.parent)
    dest = folder / path.name
    partial = folder / f".{path.name}.whysync-partial"
    shutil.copy2(src, partial)
    try:
        n = 1
        while True:
            try:
                _commit(partial, dest)
                return dest
            except FileExistsError:
                if rename is None:
                    raise
                n += 1
                label = rename if n == 2 else f"{rename} {n - 1}"
                dest = folder / f"{path.stem} ({label}){path.suffix}"
    finally:
        if partial.exists():
            partial.unlink()
