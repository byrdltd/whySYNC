"""Desktop notification (notify-send). Silently skipped when unavailable."""
from __future__ import annotations

import shutil
import subprocess


def send(title: str, body: str, urgency: str = "normal") -> None:
    exe = shutil.which("notify-send")
    if not exe:
        return
    try:
        subprocess.run(
            [exe, "--app-name=whySYNC", f"--urgency={urgency}", "--icon=folder-sync", title, body],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
