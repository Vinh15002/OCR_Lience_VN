"""Entry point used by the packaged (PyInstaller) build.

Runs before the heavy ML stack is imported so it can pin the working directory,
give the windowed build somewhere to write stdout/stderr, and seed the bundled
PaddleOCR weights into the PaddleX cache (the target machine may be offline).
"""

from __future__ import annotations

import os
import shutil
import sys
import traceback
from pathlib import Path

# Recognition models shipped inside the bundle, copied into the PaddleX cache on
# first launch so `TextRecognition` never has to reach the network.
BUNDLED_MODELS_DIRNAME = "paddlex_models"


def app_dir() -> Path:
    """Folder that holds the .exe (or the repo root when run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Folder that holds the read-only files PyInstaller unpacked."""
    return Path(getattr(sys, "_MEIPASS", str(app_dir())))


def redirect_streams(log_path: Path) -> None:
    """A windowed build has no console: stdout/stderr are None and any library
    print() would crash. Point both at a log file instead."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def seed_paddlex_models() -> None:
    source = bundle_dir() / BUNDLED_MODELS_DIRNAME
    if not source.is_dir():
        return
    cache = Path(os.environ.get("PADDLE_PDX_CACHE_HOME", Path.home() / ".paddlex"))
    target_root = cache / "official_models"
    for model in source.iterdir():
        if not model.is_dir():
            continue
        target = target_root / model.name
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(model, target)
        except OSError as exc:  # a failed copy only means a slower first run
            print(f"[launcher] could not seed model {model.name}: {exc}")


def report_crash(error: BaseException) -> None:
    traceback.print_exception(type(error), error, error.__traceback__)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "OCR Plate",
            f"Ứng dụng gặp lỗi và phải đóng:\n\n{error}\n\n"
            f"Chi tiết trong: {app_dir() / 'logs' / 'app.log'}",
        )
        root.destroy()
    except Exception:
        pass


def self_test(base: Path) -> int:
    """Load the detector and the OCR model once and report the result.

    Useful on a fresh machine: it exercises everything the packaged app needs
    (Paddle DLLs, torch, bundled weights) without touching a camera.
    """
    import numpy as np

    report = base / "logs" / "selftest.log"
    lines: list[str] = [f"working dir: {base}"]
    try:
        from plate_app.config import load_config

        config = load_config(base / "config.json")
        lines.append(f"config loaded, detector: {config.model_path}")

        from ultralytics import YOLO

        detector = YOLO(config.model_path)
        blank = np.zeros((640, 640, 3), dtype=np.uint8)
        detector.predict(blank, imgsz=config.detection_imgsz, verbose=False)
        lines.append("detector OK")

        from paddleocr import TextRecognition

        recognizer = TextRecognition(model_name=config.ocr_recognition_model)
        recognizer.predict(np.zeros((64, 192, 3), dtype=np.uint8))
        lines.append(f"OCR OK ({config.ocr_recognition_model})")
        lines.append("SELFTEST PASSED")
        status = 0
    except Exception as exc:  # noqa: BLE001 - the point is to report any failure
        lines.append(f"SELFTEST FAILED: {type(exc).__name__}: {exc}")
        lines.append(traceback.format_exc())
        status = 1

    text = "\n".join(lines)
    print(text)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8")
    return status


def main() -> int:
    base = app_dir()
    os.chdir(base)  # config.json, model, data/ are all resolved relative to here
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))  # so the source tree also runs unfrozen
    redirect_streams(base / "logs" / "app.log")
    seed_paddlex_models()
    if "--self-test" in sys.argv[1:] or os.environ.get("OCR_PLATE_SELFTEST") == "1":
        return self_test(base)
    try:
        from plate_app.ui import main as run_app

        run_app()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:  # noqa: BLE001 - last line of defence in a GUI app
        report_crash(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
