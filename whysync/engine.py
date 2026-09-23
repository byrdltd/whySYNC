"""One sync round: readiness check → dry-run plan → safety gate → rsync.

The safety rules (README, Safety) are enforced here:

* If a root is missing nothing happens; the target root is never created.
* rsync starts with ``cwd`` set to the target root. If the root vanishes in
  between, the process never starts; once started, the mount is busy and
  cannot be detached under it.
* A round that exceeds the deletion limit, deletes anything while the
  source is empty, or (first round of a pair) would delete anything at all
  is held; only an approval carrying the same plan fingerprint lets it
  through.
* The real run is locked to the planned deletion count with
  ``--max-delete``; files deleted between plan and run wait for the next round.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from whysync import safecopy
from whysync.config import TRASH_DIRNAME, Pair

STAMP_FORMAT = "%Y-%m-%dT%H-%M-%S"
_STAMP_LEN = 19
_ITEM_WIDTH = 12  # "%i %n": 11-character code + space
_EXIT_OK = 0
_EXIT_VANISHED = 24
_EXIT_MAX_DELETE = 25


@dataclass
class Plan:
    deletions: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    bytes: int = 0
    source_files: int = 0

    @property
    def creates(self) -> int:
        return len(self.created)

    @property
    def updates(self) -> int:
        return len(self.updated)

    @property
    def file_deletions(self) -> list[str]:
        return [p for p in self.deletions if not p.endswith("/")]

    @property
    def empty(self) -> bool:
        return not (self.deletions or self.creates or self.updates)

    @property
    def fingerprint(self) -> str:
        joined = "\n".join(sorted(self.deletions)).encode("utf-8", "surrogateescape")
        return hashlib.sha256(joined).hexdigest()[:16]


@dataclass
class Outcome:
    # blocked | held | noop | done | partial | error
    kind: str
    reason: str | None = None
    plan: Plan | None = None
    created: int = 0
    updated: int = 0
    deleted: int = 0
    bytes: int = 0
    trash_dir: str | None = None
    detail: str = ""
    # What the real run did, file by file (the plan is what it meant to do).
    done: Plan | None = None


def readiness(pair: Pair) -> str | None:
    """An i18n key when not ready. Never creates a directory."""
    src, dst = Path(pair.source), Path(pair.target)
    if not src.is_dir():
        return "reason.source_missing"
    if not os.access(src, os.R_OK | os.X_OK):
        return "reason.source_unreadable"
    if not dst.is_dir():
        return "reason.target_missing"
    if not os.access(dst, os.W_OK | os.X_OK):
        return "reason.target_unwritable"
    return None


def hold_reason(pair: Pair, plan: Plan, first_round: bool = False) -> str | None:
    """Why this plan must wait for the user (an i18n key), or None to go ahead.

    On a pair's first round every file that exists only in the target holds
    the round: it may be the only copy of something (see `adopt`), not a
    leftover of a deletion in the source.
    """
    deletions = len(plan.file_deletions)
    if not deletions:
        return None
    if first_round:
        return "reason.first_round"
    # An empty source is usually an unmounted disk / empty mount point.
    if deletions > pair.max_deletes or plan.source_files == 0:
        return "reason.held"
    return None


def _command(pair: Pair, *, dry: bool, max_delete: int = 0, backup_dir: str = "") -> list[str]:
    cmd = [
        "rsync", "-rt", "-8", "--modify-window=1", "--delete-delay",
        "--out-format=%i %n", "--stats", f"--exclude=/{TRASH_DIRNAME}/",
    ]
    cmd += [f"--exclude={pattern}" for pattern in pair.excludes]
    if dry:
        cmd.append("--dry-run")
    else:
        cmd += ["--backup", f"--backup-dir={backup_dir}", f"--max-delete={max_delete}", "--info=progress2"]
    cmd += [pair.source.rstrip("/") + "/", "./"]
    return cmd


# --info=progress2, e.g. "  94,371,957  99%   41.88MB/s    0:00:02 (xfr#28, to-chk=1975/2004)"
_PROGRESS = re.compile(r"^\s*([\d,]+)\s+\d+%\s+([\d.]+)([kMGT]?B)/s\s+\S+(?:\s+\(xfr#(\d+),)?")
_RATE_UNIT = {"B": 1, "kB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
_STAT_FILES = re.compile(r"^Number of files: [\d,]+ \(reg: ([\d,]+)")
_STAT_BYTES = re.compile(r"^Total transferred file size: ([\d,]+) bytes")


def parse_output(text: str) -> Plan:
    plan = Plan()
    for line in text.splitlines():
        code, name = line[:_ITEM_WIDTH].rstrip(), line[_ITEM_WIDTH:]
        if code == "*deleting":
            plan.deletions.append(name)
        elif code.startswith(">f"):
            (plan.created if code[2:] == "+" * 9 else plan.updated).append(name)
        elif m := _STAT_FILES.match(line):
            plan.source_files = int(m.group(1).replace(",", ""))
        elif m := _STAT_BYTES.match(line):
            plan.bytes = int(m.group(1).replace(",", ""))
    return plan


def parse_progress(line: str) -> dict | None:
    m = _PROGRESS.match(line)
    if m is None:
        return None
    update = {
        "bytes_done": int(m.group(1).replace(",", "")),
        "rate": int(float(m.group(2)) * _RATE_UNIT[m.group(3)]),
    }
    if m.group(4):
        update["files_done"] = int(m.group(4))
    return update


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _run(
    pair: Pair, cmd: list[str], on_proc: Callable | None, on_progress: Callable | None = None,
) -> tuple[int, str, str]:
    env = {**os.environ, "LC_ALL": "C"}
    # cwd=target root: if the root is gone Popen raises and rsync never starts.
    proc = subprocess.Popen(
        cmd, cwd=pair.target, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if on_proc:
        on_proc(proc)
    errors: list[bytes] = []
    drain = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    drain.start()
    # Item lines end in "\n", progress lines are redrawn with "\r"; read as a
    # stream so progress arrives while rsync is still running.
    lines: list[str] = []
    pending = b""
    while chunk := proc.stdout.read1(65536):
        pending += chunk
        *complete, pending = re.split(rb"[\r\n]", pending)
        for raw in complete:
            line = _decode(raw)
            update = parse_progress(line)
            if update is None:
                lines.append(line)
                # rsync names a file when it starts on it, so the last one
                # named is the one being copied now.
                if on_progress and line.startswith(">f"):
                    on_progress({"current": line[_ITEM_WIDTH:]})
            elif on_progress:
                on_progress(update)
    if pending:
        lines.append(_decode(pending))
    proc.wait()
    drain.join()
    return proc.returncode, "\n".join(lines), _decode(b"".join(errors))


def _tail(text: str, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _new_backup_dir(pair: Pair, now: float) -> str:
    base = time.strftime(STAMP_FORMAT, time.localtime(now))
    name, n = base, 2
    while (Path(pair.target) / TRASH_DIRNAME / name).exists():
        name, n = f"{base}.{n}", n + 1
    return f"{TRASH_DIRNAME}/{name}"


def plan_sync(pair: Pair, on_proc: Callable | None = None) -> Plan | Outcome:
    reason = readiness(pair)
    if reason:
        return Outcome("blocked", reason=reason)
    try:
        code, out, err = _run(pair, _command(pair, dry=True), on_proc)
    except OSError:
        return Outcome("blocked", reason="reason.target_missing")
    if code not in (_EXIT_OK, _EXIT_VANISHED):
        return Outcome("error", reason="reason.rsync_failed", detail=_tail(err))
    return parse_output(out)


def run_sync(
    pair: Pair,
    approved: str | None = None,
    on_proc: Callable | None = None,
    now: float | None = None,
    on_progress: Callable[[dict], None] | None = None,
    first_round: bool = False,
) -> Outcome:
    """`on_progress` gets {"phase": "compare"}, then {"phase": "copy", totals}
    and a stream of byte / file counts while rsync copies. See `hold_reason`
    for when a round waits instead."""
    report = on_progress or (lambda _update: None)
    report({"phase": "compare"})
    plan = plan_sync(pair, on_proc)
    if isinstance(plan, Outcome):
        return plan
    if plan.empty:
        return Outcome("noop", plan=plan)
    if approved != plan.fingerprint and (reason := hold_reason(pair, plan, first_round)):
        return Outcome("held", reason=reason, plan=plan)

    backup_dir = _new_backup_dir(pair, time.time() if now is None else now)
    cmd = _command(pair, dry=False, max_delete=len(plan.deletions), backup_dir=backup_dir)
    report({"phase": "copy", "files_total": plan.creates + plan.updates, "bytes_total": plan.bytes,
            "files_done": 0, "bytes_done": 0, "rate": 0})
    try:
        code, out, err = _run(pair, cmd, on_proc, lambda update: report({"phase": "copy", **update}))
    except OSError:
        return Outcome("blocked", reason="reason.target_missing", plan=plan)
    done = parse_output(out)
    trash = Path(pair.target) / backup_dir
    outcome = Outcome(
        "done", plan=plan,
        created=done.creates, updated=done.updates,
        deleted=len(done.file_deletions), bytes=done.bytes,
        trash_dir=str(trash) if trash.exists() else None, done=done,
    )
    if code == _EXIT_MAX_DELETE:
        outcome.kind, outcome.reason = "partial", "reason.more_deletions"
    elif code not in (_EXIT_OK, _EXIT_VANISHED):
        outcome.kind, outcome.reason, outcome.detail = "error", "reason.rsync_failed", _tail(err)
    return outcome


def adopt(pair: Pair, paths: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Copy what exists only in the target into the source, so mirroring keeps it.

    `paths` are plan entries: files, and folders ending in "/" (kept even
    when empty). Never overwrites: a file that exists in the source by now is
    left alone. Returns (copied, [(path, error)]).
    """
    if readiness(pair):
        return [], [(rel, readiness(pair) or "") for rel in paths]
    source, target = Path(pair.source), Path(pair.target)
    copied: list[str] = []
    failed: list[tuple[str, str]] = []
    for rel in paths:
        try:
            if rel.endswith("/"):
                safecopy.ensure_dirs(source, safecopy.relative(rel.rstrip("/")))
            else:
                safecopy.copy_new(target / rel, source, rel)
            copied.append(rel)
        except FileExistsError:
            continue  # the source has its own file by that name; it wins
        except OSError as exc:
            failed.append((rel, str(exc)))
    return copied, failed


_STAMP_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:\.\d+)?$")


def stamp_time(name: str) -> float | None:
    """When a trash round folder was made, or None if we did not name it."""
    if not _STAMP_NAME.match(name):
        return None
    try:
        return time.mktime(time.strptime(name[:_STAMP_LEN], STAMP_FORMAT))
    except ValueError:
        return None


def purge_trash(pair: Pair, now: float | None = None) -> int:
    """Removes trash folders older than `trash_days`; returns how many."""
    root = Path(pair.target) / TRASH_DIRNAME
    if not root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - pair.trash_days * 86400
    removed = 0
    for entry in root.iterdir():
        stamp = stamp_time(entry.name)
        if stamp is None:
            continue  # not named by us; leave it alone
        if stamp < cutoff and entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed
