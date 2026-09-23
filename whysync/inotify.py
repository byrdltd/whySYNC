"""Recursive inotify watcher (ctypes, no dependencies).

It does not track which file changed: every round runs rsync over the whole
folder anyway. The watcher only says "something changed" and "the root is
gone". Events missed while a new directory is being added are harmless; the
directory's creation is itself a change and a full round follows.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import struct
from dataclasses import dataclass
from pathlib import Path

IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_UNMOUNT = 0x00002000
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_ISDIR = 0x40000000
IN_NONBLOCK = os.O_NONBLOCK
IN_CLOEXEC = os.O_CLOEXEC

# IN_MODIFY keeps extending the quiet period while a large file is being
# written, so a half-written file is not copied.
WATCH_MASK = (
    IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO
    | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
    | IN_ONLYDIR | IN_DONT_FOLLOW
)
_CHANGE_MASK = (
    IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
)
_HEADER = struct.Struct("iIII")

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.inotify_init1.argtypes = [ctypes.c_int]
_libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
_libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]


class WatchLimitError(OSError):
    """fs.inotify.max_user_watches is exhausted."""


@dataclass
class Batch:
    changed: bool = False
    root_gone: bool = False


class Watcher:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._fd = -1
        self._wds: dict[int, Path] = {}
        self._root_wd = -1

    def start(self) -> None:
        fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        self._fd = fd
        self._root_wd = self._add(self.root)
        if self._root_wd < 0:
            self.close()
            raise FileNotFoundError(errno.ENOENT, "watch root missing", str(self.root))
        self._add_tree(self.root)

    def fileno(self) -> int:
        return self._fd

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
        self._fd = -1
        self._wds.clear()

    def __enter__(self) -> Watcher:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _add(self, path: Path) -> int:
        wd = _libc.inotify_add_watch(self._fd, os.fsencode(path), WATCH_MASK)
        if wd < 0:
            err = ctypes.get_errno()
            if err == errno.ENOSPC:
                raise WatchLimitError(err, "inotify watch limit reached")
            # Directory removed meanwhile / not permitted: skip it.
            if err in (errno.ENOENT, errno.ENOTDIR, errno.EACCES):
                return -1
            raise OSError(err, os.strerror(err), str(path))
        self._wds[wd] = path
        return wd

    def _add_tree(self, top: Path) -> None:
        for dirpath, dirnames, _ in os.walk(top):
            for name in dirnames:
                self._add(Path(dirpath) / name)

    def read(self) -> Batch:
        """Reads pending events; an empty Batch when there are none."""
        batch = Batch()
        while True:
            try:
                buf = os.read(self._fd, 64 * 1024)
            except BlockingIOError:
                return batch
            if not buf:
                return batch
            self._parse(buf, batch)

    def _parse(self, buf: bytes, batch: Batch) -> None:
        offset = 0
        while offset < len(buf):
            wd, mask, _cookie, length = _HEADER.unpack_from(buf, offset)
            offset += _HEADER.size
            raw_name = buf[offset:offset + length].rstrip(b"\0")
            offset += length
            if mask & IN_Q_OVERFLOW:
                batch.changed = True
                continue
            if wd == self._root_wd and mask & (IN_DELETE_SELF | IN_MOVE_SELF | IN_UNMOUNT | IN_IGNORED):
                batch.root_gone = True
                continue
            if mask & IN_IGNORED:
                self._wds.pop(wd, None)
                continue
            if mask & IN_UNMOUNT:
                batch.root_gone = True
                continue
            if mask & _CHANGE_MASK:
                batch.changed = True
            parent = self._wds.get(wd)
            if parent is None or not raw_name:
                continue
            name = os.fsdecode(raw_name)
            if mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO):
                child = parent / name
                if self._add(child) >= 0:
                    self._add_tree(child)
