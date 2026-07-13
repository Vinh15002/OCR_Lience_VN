from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CameraConfig:
    id: str
    name: str
    uri: str
    enabled: bool = True
    loop_video: bool = False


@dataclass
class AppConfig:
    model_path: str = "license_plate_detector.pt"
    roi_width: float = 0.70
    roi_height: float = 0.65
    detection_confidence: float = 0.35
    ocr_confidence: float = 0.30
    ocr_recognition_model: str = "PP-OCRv6_tiny_rec"
    frame_skip: int = 3
    preview_fps: int = 20
    detection_interval_seconds: float = 0.5
    min_votes: int = 2
    vote_window_seconds: float = 2.5
    duplicate_cooldown_seconds: float = 10.0
    recognized_cache_seconds: float = 30.0
    track_max_missed_frames: int = 2
    max_plates_per_frame: int = 1
    data_dir: str = "data"
    cameras: list[CameraConfig] = field(default_factory=list)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    cameras = [CameraConfig(**item) for item in raw.pop("cameras", [])]
    known_fields = AppConfig.__dataclass_fields__
    values = {key: value for key, value in raw.items() if key in known_fields}
    return AppConfig(**values, cameras=cameras)


def save_config(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
