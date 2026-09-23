"""Life cycle of one folder pair (a thread).

    wait (no root) ──roots ready──▶ watch + first round
         ▲                            │ event → quiet period → round
         └── root gone / no target ◀──┘ hourly full round

When settings change the service stops the pair and starts a new one; a
running worker never changes its own settings.
"""
from __future__ import annotations

import logging
import os
import queue
import select
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from whysync import engine, notify, status
from whysync.config import Pair
from whysync.fmt import done_summary
from whysync.i18n import t
from whysync.inotify import Watcher, WatchLimitError
from whysync.status import StatusBoard

log = logging.getLogger("whysync")

HELD_SAMPLE = 200
RETRY_PARTIAL_S = 5.0
RETRY_ERROR_S = 60.0
PROGRESS_WRITE_S = 0.5
LAST_SAMPLE = 50
# Live mirroring makes many small rounds; only long ones deserve a desktop notification.
NOTIFY_LONG_ROUND_S = 60.0


class PairWorker(threading.Thread):
    def __init__(
        self,
        pair: Pair,
        board: StatusBoard,
        notifier: Callable[[str, str], None] = notify.send,
        poll_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(daemon=True, name=f"pair-{pair.id}")
        self.pair = pair
        self._board = board
        self._notifier = notifier
        self._poll_s = poll_s
        self._clock = clock
        self._cmds: queue.SimpleQueue = queue.SimpleQueue()
        self._wake_r, self._wake_w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        self._stopping = False
        self._proc: subprocess.Popen | None = None
        self._approved: str | None = None
        self._force = False
        self._last_notice: tuple | None = None
        self._held_plan: engine.Plan | None = None
        self._adopt: str | None = None
        self._adopted = 0
        self._progress: dict = {}
        self._progress_written = 0.0

    # --- external API (called from other threads) ---

    def request_sync(self) -> None:
        self._send("sync")

    def approve(self, fingerprint: str) -> None:
        self._send(("approve", fingerprint))

    def adopt(self, fingerprint: str) -> None:
        """Copy the held round's target-only files into the source, then sync."""
        self._send(("adopt", fingerprint))

    def stop(self, timeout: float = 15.0) -> None:
        self._stopping = True
        self._send("stop")
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self.join(timeout)

    def _send(self, cmd) -> None:
        self._cmds.put(cmd)
        try:
            os.write(self._wake_w, b"x")
        except (BlockingIOError, OSError):
            pass

    # --- thread ---

    def run(self) -> None:
        try:
            self._loop()
        except Exception:  # noqa: BLE001 — keep the service up and the state visible
            log.exception("[%s] worker crashed", self.pair.id)
            self._set("error", reason="reason.internal")
        finally:
            os.close(self._wake_r)
            os.close(self._wake_w)

    def _loop(self) -> None:
        while not self._stopping:
            if self.pair.paused:
                self._set("paused", reason=None)
                self._wait([], None)
                continue
            reason = engine.readiness(self.pair)
            if reason:
                self._set("waiting", reason=reason)
                self._wait([], self._poll_s)
                continue
            # Adding a watch per directory walks the whole tree; on a cold
            # cache that takes minutes, so say so instead of looking idle.
            self._set("syncing", reason=None, progress={"phase": "scan", "since": time.time()})
            try:
                watcher = Watcher(Path(self.pair.source))
                watcher.start()
            except FileNotFoundError:
                self._wait([], 1.0)
                continue
            except WatchLimitError:
                self._set("error", reason="reason.watch_limit")
                self._wait([], self._poll_s * 10)
                continue
            with watcher:
                self._watch(watcher)

    def _watch(self, watcher: Watcher) -> None:
        outcome = self._sync()
        if outcome.kind == "blocked":
            return
        next_full = self._clock() + self.pair.full_sync_s
        pending_since: float | None = None
        last_event = 0.0
        retry_at = self._retry_at(outcome)

        while not self._stopping:
            now = self._clock()
            deadlines = [next_full]
            if pending_since is not None:
                deadlines.append(min(last_event + self.pair.debounce_s, pending_since + self.pair.debounce_cap_s))
            if retry_at is not None:
                deadlines.append(retry_at)
            ready = self._wait([watcher], max(0.0, min(deadlines) - now))
            now = self._clock()

            if ready:
                batch = watcher.read()
                if batch.root_gone:
                    log.info("[%s] source went away", self.pair.id)
                    return
                if batch.changed:
                    pending_since = pending_since if pending_since is not None else now
                    last_event = now
            if self._stopping:
                return

            quiet = pending_since is not None and (
                now >= last_event + self.pair.debounce_s or now >= pending_since + self.pair.debounce_cap_s
            )
            due = self._force or quiet or now >= next_full or (retry_at is not None and now >= retry_at)
            if not due:
                continue
            self._force = False
            pending_since, retry_at = None, None
            outcome = self._sync()
            if outcome.kind == "blocked":
                return
            next_full = self._clock() + self.pair.full_sync_s
            retry_at = self._retry_at(outcome)

    def _retry_at(self, outcome: engine.Outcome) -> float | None:
        if outcome.kind == "partial":
            return self._clock() + RETRY_PARTIAL_S
        if outcome.kind == "error":
            return self._clock() + RETRY_ERROR_S
        return None

    def _wait(self, watchers: list[Watcher], timeout: float | None) -> list[Watcher]:
        fds = [self._wake_r] + [w.fileno() for w in watchers]
        try:
            readable, _, _ = select.select(fds, [], [], timeout)
        except InterruptedError:
            readable = []
        if self._wake_r in readable:
            try:
                while os.read(self._wake_r, 4096):
                    pass
            except BlockingIOError:
                pass
        self._drain_commands()
        return [w for w in watchers if w.fileno() in readable]

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self._cmds.get_nowait()
            except queue.Empty:
                return
            if cmd == "stop":
                self._stopping = True
            elif cmd == "sync":
                self._force = True
            elif isinstance(cmd, tuple) and cmd[0] == "approve":
                self._approved = cmd[1]
                self._force = True
            elif isinstance(cmd, tuple) and cmd[0] == "adopt":
                self._adopt = cmd[1]
                self._force = True

    # --- one round ---

    def _sync(self) -> engine.Outcome:
        self._progress: dict = {}
        self._progress_written = 0.0
        started = time.time()
        self._adopt_held_files()
        first = status.first_round_pending(self.pair.id)
        outcome = engine.run_sync(self.pair, approved=self._approved, on_proc=self._track,
                                  on_progress=self._on_progress, first_round=first)
        self._proc = None
        if self._stopping:
            # Paused, removed or shutting down: rsync was stopped on purpose,
            # so its exit code 20 is not a failed sync worth an error or a notification.
            log.info("[%s] stopped during a round", self.pair.id)
            return outcome
        if outcome.kind != "held":
            self._approved = None
        now = time.time()
        pid = self.pair.id

        if outcome.kind in ("done", "partial", "noop"):
            fields = {"checked_at": now, "held": None, "error": None}
            if outcome.kind != "noop":
                done = outcome.done or engine.Plan()
                fields["last_sync"] = {
                    "at": now, "created": outcome.created, "updated": outcome.updated,
                    "deleted": outcome.deleted, "bytes": outcome.bytes,
                    "trash_dir": outcome.trash_dir, "duration": now - started,
                    "created_sample": done.created[:LAST_SAMPLE],
                    "updated_sample": done.updated[:LAST_SAMPLE],
                    "deleted_sample": done.file_deletions[:LAST_SAMPLE],
                    "adopted": self._adopted,
                }
                self._adopted = 0
                log.info("[%s] synced: +%d ~%d -%d", pid, outcome.created, outcome.updated, outcome.deleted)
                engine.purge_trash(self.pair)
                if now - started >= NOTIFY_LONG_ROUND_S:
                    self._notifier(t("notify.done_title", name=Path(self.pair.source).name),
                                   done_summary(fields["last_sync"]), "normal")
            self._set("idle", reason=outcome.reason, **fields)
            self._last_notice = None
            self._held_plan = None
            if first:
                status.set_first_round(pid, False)
        elif outcome.kind == "held":
            plan = outcome.plan
            deletions = plan.file_deletions
            asks_keep = outcome.reason == "reason.first_round"
            self._held_plan = plan
            self._set("held", reason=outcome.reason, held={
                "kind": "first_round" if asks_keep else "limit",
                "fingerprint": plan.fingerprint,
                "count": len(deletions),
                "source_files": plan.source_files,
                "sample": deletions[:HELD_SAMPLE],
            })
            log.warning("[%s] held (%s): %d files only in the target", pid,
                        "first round" if asks_keep else "limit", len(deletions))
            name = Path(self.pair.source).name
            title, body = (("notify.first_title", "notify.first_body") if asks_keep
                           else ("notify.held_title", "notify.held_body"))
            self._notice(("held", plan.fingerprint), t(title, name=name),
                         t(body, pair=pid, name=name, count=len(deletions)), "critical")
        elif outcome.kind == "error":
            self._set("error", reason=outcome.reason, error=outcome.detail)
            log.error("[%s] %s: %s", pid, outcome.reason, outcome.detail)
            self._notice(("error", outcome.reason), t("notify.error_title"),
                         t("notify.error_body", pair=pid, reason=t(outcome.reason)), "normal")
        else:
            self._set("waiting", reason=outcome.reason)
        return outcome

    def _adopt_held_files(self) -> None:
        wanted, self._adopt = self._adopt, None
        plan = self._held_plan
        if wanted is None or plan is None or plan.fingerprint != wanted:
            return  # nothing asked, or the list changed since it was shown
        self._on_progress({"phase": "adopt"})
        copied, failed = engine.adopt(self.pair, plan.deletions)
        self._adopted += len(copied)
        log.info("[%s] adopted %d target-only files into the source (%d failed)", self.pair.id,
                 len(copied), len(failed))
        for rel, error in failed[:20]:
            log.warning("[%s] could not adopt %s: %s", self.pair.id, rel, error)

    def _track(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        if self._stopping:
            proc.terminate()

    def _on_progress(self, update: dict) -> None:
        phase_changed = update.get("phase") != self._progress.get("phase")
        if phase_changed:
            self._progress = {"since": time.time()}
        self._progress.update(update)
        now = time.monotonic()
        if phase_changed or now - self._progress_written >= PROGRESS_WRITE_S:
            self._progress_written = now
            self._set("syncing", reason=None, progress=dict(self._progress))

    def _set(self, state: str, **fields) -> None:
        if state != "syncing":
            fields.setdefault("progress", None)
        self._board.update(self.pair.id, state=state, **fields)

    def _notice(self, key: tuple, title: str, body: str, urgency: str) -> None:
        # Do not re-notify the same hold / error on every round.
        if key == self._last_notice:
            return
        self._last_notice = key
        self._notifier(title, body, urgency)
