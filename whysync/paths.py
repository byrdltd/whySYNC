"""XDG directories. Tests point them at tmp through the environment."""
from __future__ import annotations

import os
from pathlib import Path

APP = "whysync"


def _xdg(var: str, fallback: str) -> Path:
    raw = os.environ.get(var) or ""
    base = Path(raw) if raw and os.path.isabs(raw) else Path.home() / fallback
    return base / APP


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def runtime_dir() -> Path:
    raw = os.environ.get("XDG_RUNTIME_DIR") or ""
    if raw and os.path.isabs(raw):
        return Path(raw) / APP
    return state_dir()


def config_file() -> Path:
    return config_dir() / "pairs.json"


def status_file() -> Path:
    return state_dir() / "status.json"


def socket_path() -> Path:
    return runtime_dir() / "control.sock"


def write_atomic(path: Path, text: str) -> None:
    """Readers (UI, CLI) must never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
