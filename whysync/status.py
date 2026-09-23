"""Pair states — written by the service, read by the CLI and UI (`status.json`)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from whysync import __version__, paths

SCHEMA = 1


class StatusBoard:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.status_file()
        self._lock = threading.Lock()
        self._pairs: dict[str, dict] = {}
        self._daemon = {"pid": os.getpid(), "version": __version__, "started_at": time.time()}

    def update(self, pair_id: str, **fields) -> None:
        with self._lock:
            entry = self._pairs.setdefault(pair_id, {})
            if "state" in fields and fields["state"] != entry.get("state"):
                entry["since"] = time.time()
            entry.update(fields)
            self._write()

    def remove(self, pair_id: str) -> None:
        with self._lock:
            if self._pairs.pop(pair_id, None) is not None:
                self._write()

    def get(self, pair_id: str) -> dict:
        with self._lock:
            return dict(self._pairs.get(pair_id, {}))

    def _write(self) -> None:
        data = {"schema": SCHEMA, "daemon": self._daemon, "pairs": self._pairs}
        paths.write_atomic(self._path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load(path: Path | None = None) -> dict:
    path = path or paths.status_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pairs": {}}
    return data if isinstance(data, dict) else {"pairs": {}}


# A pair's first round asks about files that exist only in the target (see
# engine.run_sync). The flag is set when the pair is added and cleared after
# its first finished round, so it outlives service restarts; pairs without it
# are past their first round.

def _first_round_flag(pair_id: str) -> Path:
    return paths.state_dir() / "first-round" / pair_id


def set_first_round(pair_id: str, pending: bool) -> None:
    flag = _first_round_flag(pair_id)
    if pending:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    else:
        flag.unlink(missing_ok=True)


def first_round_pending(pair_id: str) -> bool:
    return _first_round_flag(pair_id).exists()
