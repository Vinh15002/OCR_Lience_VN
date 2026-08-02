"""Open exported files with the operating system's associated application."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def open_with_default_app(path: Path | str) -> None:
    """Open *path* as if the user had double-clicked it in File Explorer."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Không tìm thấy file: {resolved}")

    if sys.platform.startswith("win"):
        os.startfile(str(resolved))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":  # pragma: no cover - Windows is the deployment target
        subprocess.Popen(["open", str(resolved)])
    else:  # pragma: no cover - Windows is the deployment target
        subprocess.Popen(["xdg-open", str(resolved)])
