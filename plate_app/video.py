from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2

from .config import CameraConfig


def parse_video_source(uri: str):
    value = uri.strip()
    if value.isdigit() and not Path(value).exists():
        return int(value)
    return value


class CameraStream:
    """Continuously reads a source and only retains its newest frame."""

    def __init__(self, config: CameraConfig):
        self.config = config
        self._capture = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame = None
        self._sequence = 0
        self._status = "stopped"

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"capture-{self.config.id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._capture is not None:
            self._capture.release()
        with self._lock:
            self._status = "stopped"

    def latest(self):
        with self._lock:
            if self._frame is None:
                return self._sequence, None, self._status
            return self._sequence, self._frame.copy(), self._status

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status

    def _open(self) -> bool:
        source = parse_video_source(self.config.uri)
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            capture.release()
            self._set_status("cannot open")
            return False
        self._capture = capture
        self._set_status("connected")
        return True

    def _run(self) -> None:
        is_file = Path(self.config.uri).is_file()
        reconnect_delay = 1.0
        while not self._stop.is_set():
            if self._capture is None or not self._capture.isOpened():
                if not self._open():
                    self._stop.wait(reconnect_delay)
                    continue

            ok, frame = self._capture.read()
            if not ok:
                self._capture.release()
                self._capture = None
                if is_file and not self.config.loop_video:
                    self._set_status("finished")
                    return
                self._set_status("reconnecting")
                self._stop.wait(reconnect_delay)
                continue

            with self._lock:
                self._frame = frame
                self._sequence += 1
                self._status = "connected"

            if is_file:
                fps = self._capture.get(cv2.CAP_PROP_FPS)
                time.sleep(1.0 / fps if fps and fps > 1 else 0.03)
