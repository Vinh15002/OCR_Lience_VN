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
    direction: str = "IN"
    start_delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        self.direction = str(self.direction).strip().upper()
        if self.direction not in {"IN", "OUT"}:
            raise ValueError(f"Camera direction must be IN or OUT, got: {self.direction}")
        self.start_delay_seconds = max(0.0, float(self.start_delay_seconds))


@dataclass
class AppConfig:
    model_path: str = "license_plate_detector.pt"
    roi_width: float = 0.70
    roi_height: float = 0.65
    detection_confidence: float = 0.35
    detection_imgsz: int = 960
    ocr_confidence: float = 0.30
    ocr_recognition_model: str = "PP-OCRv6_medium_rec"
    frame_skip: int = 3
    preview_fps: int = 20
    detection_interval_seconds: float = 0.5
    min_votes: int = 2
    vote_window_seconds: float = 2.5
    duplicate_cooldown_seconds: float = 10.0
    recognized_cache_seconds: float = 30.0
    track_max_missed_frames: int = 2
    max_plates_per_frame: int = 1
    # Gate + parking business rules.
    open_gate_policy: str = "all"  # "all" (paid parking) | "registered_only" (access control)
    gate_open_seconds: float = 4.0
    parking_flat_fee: float = 0.0
    parking_hourly_fee: float = 0.0
    parking_free_minutes: int = 0
    # Barrier hardware backend.
    gate_backend: str = "simulated"  # "simulated" | "tcp" | "serial"
    gate_host: str = ""
    gate_port: int = 8000
    gate_serial_port: str = ""
    gate_baudrate: int = 9600
    gate_command: str = "OPEN"
    # VietQR bank account for fee collection.
    bank_bin: str = ""
    bank_account: str = ""
    bank_account_name: str = ""
    # Operations.
    require_login: bool = False
    auto_start: bool = False
    camera_alert_seconds: float = 6.0
    data_dir: str = "data"
    cameras: list[CameraConfig] = field(default_factory=list)

    def tariff(self):
        from .parking import Tariff

        return Tariff(
            flat_fee=self.parking_flat_fee,
            hourly_fee=self.parking_hourly_fee,
            free_minutes=self.parking_free_minutes,
        )

    def bank(self):
        from .payment import BankAccount

        return BankAccount(
            bank_bin=self.bank_bin,
            account_number=self.bank_account,
            account_name=self.bank_account_name,
        )


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
