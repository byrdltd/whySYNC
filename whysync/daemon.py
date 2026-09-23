"""The service: watches settings, runs one worker per pair, serves the control socket.

The CLI / UI write settings straight into `pairs.json`; the service polls the
file every couple of seconds. Immediate commands such as "sync now" and
"approve" arrive over a Unix socket (one JSON request / reply per line).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path

from whysync import __version__, config, notify, paths
from whysync.i18n import init_language
from whysync.status import StatusBoard
from whysync.worker import PairWorker

log = logging.getLogger("whysync")


class AlreadyRunning(RuntimeError):
    pass


def request(message: dict, sock_path: Path | None = None, timeout: float = 5.0) -> dict:
    """One request to the service; ConnectionError when it is not running."""
    sock_path = sock_path or paths.socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(sock_path))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise ConnectionError("daemon not running") from exc
        s.sendall(json.dumps(message).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b"{}")


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            msg = json.loads(self.rfile.readline() or b"{}")
            reply = self.server.daemon_ref.command(msg)
        except ValueError:
            reply = {"ok": False, "error": "bad_request"}
        self.wfile.write(json.dumps(reply).encode() + b"\n")


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Daemon:
    def __init__(
        self,
        config_path: Path | None = None,
        status_path: Path | None = None,
        sock_path: Path | None = None,
        notifier: Callable = notify.send,
        poll_s: float = 30.0,
        reload_s: float = 2.0,
    ) -> None:
        self._config_path = config_path or paths.config_file()
        self._sock_path = sock_path or paths.socket_path()
        self._board = StatusBoard(status_path)
        self._notifier = notifier
        self._poll_s = poll_s
        self._reload_s = reload_s
        self._workers: dict[str, PairWorker] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server: _Server | None = None
        self._config_sig: tuple | None = ()

    # --- life cycle ---

    def run(self) -> None:
        self._bind()
        threading.Thread(target=self._server.serve_forever, daemon=True, name="control").start()
        log.info("whysync %s started", __version__)
        try:
            while not self._stop.is_set():
                self._maybe_reload()
                self._stop.wait(self._reload_s)
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._stop.set()

    def _bind(self) -> None:
        path = self._sock_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            try:
                request({"cmd": "ping"}, path, timeout=1.0)
            except (ConnectionError, OSError, ValueError):
                path.unlink()  # stale socket left by a previous process
            else:
                raise AlreadyRunning()
        self._server = _Server(str(path), _Handler)
        self._server.daemon_ref = self
        os.chmod(path, 0o600)

    def _shutdown(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w.stop()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        try:
            self._sock_path.unlink()
        except FileNotFoundError:
            pass
        log.info("whysync stopped")

    # --- settings ---

    def _signature(self) -> tuple | None:
        try:
            st = self._config_path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _maybe_reload(self) -> None:
        sig = self._signature()
        if sig == self._config_sig:
            return
        self._config_sig = sig
        try:
            cfg = config.load(self._config_path)
        except config.ConfigError:
            log.error("config unreadable, keeping current pairs")
            return
        init_language(cfg.language)
        self.reconcile(cfg.pairs)

    def reconcile(self, pairs: list[config.Pair]) -> None:
        wanted = {p.id: p for p in pairs}
        with self._lock:
            for pid, worker in list(self._workers.items()):
                if wanted.get(pid) != worker.pair:
                    worker.stop()
                    del self._workers[pid]
                    if pid not in wanted:
                        self._board.remove(pid)
            for pid, pair in wanted.items():
                if pid not in self._workers:
                    worker = PairWorker(pair, self._board, self._notifier, poll_s=self._poll_s)
                    self._workers[pid] = worker
                    worker.start()

    # --- commands ---

    def command(self, msg: dict) -> dict:
        cmd = msg.get("cmd")
        if cmd == "ping":
            return {"ok": True, "version": __version__, "pid": os.getpid()}
        if cmd in ("sync", "approve", "adopt"):
            with self._lock:
                worker = self._workers.get(str(msg.get("pair")))
            if worker is None:
                return {"ok": False, "error": "pair.unknown"}
            if cmd == "sync":
                worker.request_sync()
            elif cmd == "approve":
                worker.approve(str(msg.get("fingerprint") or ""))
            else:
                worker.adopt(str(msg.get("fingerprint") or ""))
            return {"ok": True}
        return {"ok": False, "error": "bad_request"}
