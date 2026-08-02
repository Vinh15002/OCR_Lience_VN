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
    parking_daily_cap: float = 0.0      # ceiling on the hourly part per started day
    parking_overnight_fee: float = 0.0  # surcharge for every night the vehicle stays
    parking_night_hour: int = 22        # hour that starts a night
    parking_capacity: int = 0  # number of spaces; 0 = unknown, hides occupancy %
    # Per-vehicle-class overrides of the price list above, e.g.
    # {"CAR": {"flat_fee": 10000, "hourly_fee": 5000, "daily_cap": 60000}}.
    parking_tariffs: dict[str, dict] = field(default_factory=dict)
    default_vehicle_type: str = "MOTORBIKE"
    # Price of a monthly pass per vehicle class.
    monthly_ticket_fees: dict[str, float] = field(
        default_factory=lambda: {"MOTORBIKE": 100000.0, "CAR": 800000.0, "BICYCLE": 50000.0}
    )
    retention_days: int = 0  # delete events/snapshots older than this; 0 = keep forever
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
    # Automatic payment confirmation from a bank feed ("none" | "sepay" | "casso").
    payment_provider: str = "none"
    payment_api_token: str = ""
    payment_poll_seconds: float = 20.0
    # Operations.
    require_login: bool = False
    auto_start: bool = False
    camera_alert_seconds: float = 6.0
    data_dir: str = "data"
    cameras: list[CameraConfig] = field(default_factory=list)

    def tariff(self):
        """Base price list, used for the default vehicle class."""
        from .parking import Tariff

        return Tariff(
            flat_fee=self.parking_flat_fee,
            hourly_fee=self.parking_hourly_fee,
            free_minutes=self.parking_free_minutes,
            daily_cap=self.parking_daily_cap,
            overnight_fee=self.parking_overnight_fee,
            night_hour=self.parking_night_hour,
        )

    def tariff_table(self):
        """Base price list plus the per-vehicle-class overrides."""
        from dataclasses import replace

        from .parking import TariffTable, normalize_vehicle_type

        base = self.tariff()
        fields = {"flat_fee", "hourly_fee", "free_minutes", "daily_cap",
                  "overnight_fee", "night_hour"}
        by_type = {}
        for raw_type, overrides in (self.parking_tariffs or {}).items():
            vehicle_type = normalize_vehicle_type(raw_type, default="")
            if not vehicle_type or not isinstance(overrides, dict):
                continue
            clean = {key: value for key, value in overrides.items() if key in fields}
            by_type[vehicle_type] = replace(base, **clean)
        return TariffTable(default=base, by_type=by_type)

    def monthly_fee(self, vehicle_type: str | None) -> float:
        from .parking import normalize_vehicle_type

        key = normalize_vehicle_type(vehicle_type, default=self.default_vehicle_type)
        fees = self.monthly_ticket_fees or {}
        return float(fees.get(key, fees.get("MOTORBIKE", 0.0)) or 0.0)

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
