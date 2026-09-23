"""Operations shared by the CLI and the window.

Settings changes write `pairs.json` (the service picks them up); immediate
commands go to the service over its socket.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from whysync import config, status
from whysync.daemon import request

UNIT = "whysync.service"


class ActionError(Exception):
    """`key` is an i18n key; `params` fill its placeholders."""

    def __init__(self, key: str, **params) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


def service_info() -> dict | None:
    try:
        return request({"cmd": "ping"}, timeout=1.0)
    except (ConnectionError, OSError, ValueError):
        return None


def start_service() -> bool:
    try:
        done = subprocess.run(["systemctl", "--user", "start", UNIT], capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def pair_states() -> dict:
    return status.load().get("pairs", {})


def add_pair(source: str, target: str, max_deletes: int = 50, excludes: list[str] | None = None) -> config.Pair:
    cfg = config.load()
    source = str(Path(source).expanduser().absolute())
    target = str(Path(target).expanduser().absolute())
    if not Path(source).is_dir():
        raise ActionError("pair.source_not_dir", path=source)
    if not Path(target).is_dir():
        raise ActionError("pair.target_not_dir", path=target)
    error = config.validate_pair(source, target, cfg.pairs)
    if error:
        raise ActionError(error)
    pair = config.Pair(
        id=config.new_pair_id(cfg, source), source=source, target=target,
        max_deletes=max_deletes, excludes=list(excludes or []),
    )
    cfg.pairs.append(pair)
    status.set_first_round(pair.id, True)  # before the service can see the pair
    config.save(cfg)
    return pair


def _edit(pair_id: str, change) -> config.Pair:
    cfg = config.load()
    pair = cfg.find(pair_id)
    if pair is None:
        raise ActionError("pair.unknown", id=pair_id)
    change(cfg, pair)
    config.save(cfg)
    return pair


def remove_pair(pair_id: str) -> config.Pair:
    pair = _edit(pair_id, lambda cfg, pair: cfg.pairs.remove(pair))
    status.set_first_round(pair_id, False)
    return pair


def set_paused(pair_id: str, paused: bool) -> config.Pair:
    return _edit(pair_id, lambda _cfg, pair: setattr(pair, "paused", paused))


def _command(message: dict) -> None:
    try:
        reply = request(message)
    except (ConnectionError, OSError, ValueError) as exc:
        raise ActionError("cli.needs_daemon") from exc
    if not reply.get("ok"):
        raise ActionError(reply.get("error") or "bad_request", id=message.get("pair", ""))


def sync_now(pair_id: str) -> None:
    _command({"cmd": "sync", "pair": pair_id})


def approve(pair_id: str, fingerprint: str) -> None:
    _command({"cmd": "approve", "pair": pair_id, "fingerprint": fingerprint})


def adopt(pair_id: str, fingerprint: str) -> None:
    """Keep a held first round's target-only files by copying them into the source."""
    _command({"cmd": "adopt", "pair": pair_id, "fingerprint": fingerprint})
