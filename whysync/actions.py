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
# Settings a pair can change after it is added, with the values they accept.
LIMITS = {"max_deletes": (1, 1_000_000), "trash_days": (1, 3650), "stale_days": (0, 3650),
          "verify_days": (0, 3650)}


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


def _check_numbers(numbers: dict[str, int]) -> None:
    for name, value in numbers.items():
        low, high = LIMITS[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ActionError("pair.bad_number", name=name, low=low, high=high)


def _clean_excludes(patterns: list[str]) -> list[str]:
    return [p.strip() for p in patterns if p.strip()]


def add_pair(source: str, target: str, max_deletes: int = 50, excludes: list[str] | None = None) -> config.Pair:
    _check_numbers({"max_deletes": max_deletes})
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
        max_deletes=max_deletes, excludes=_clean_excludes(excludes or []),
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


def update_pair(pair_id: str, *, excludes: list[str] | None = None, **numbers: int) -> config.Pair:
    """Change a pair's limits and exclusions. Its folders never change: other
    folders are a new pair, with a first round of their own."""
    _check_numbers(numbers)
    patterns = None if excludes is None else _clean_excludes(excludes)

    def change(_cfg, pair: config.Pair) -> None:
        for name, value in numbers.items():
            setattr(pair, name, value)
        if patterns is not None:
            pair.excludes = patterns

    return _edit(pair_id, change)


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


def repair(pair_id: str) -> None:
    """Copy again the files whose contents differ from the source (old versions go to trash)."""
    _command({"cmd": "repair", "pair": pair_id})


def adopt(pair_id: str, fingerprint: str) -> None:
    """Keep a held first round's target-only files by copying them into the source."""
    _command({"cmd": "adopt", "pair": pair_id, "fingerprint": fingerprint})
