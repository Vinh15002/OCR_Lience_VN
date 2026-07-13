from __future__ import annotations

import csv
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2


@dataclass(frozen=True)
class PlateEvent:
    id: int
    camera_id: str
    camera_name: str
    plate: str
    confidence: float
    detected_at: str
    snapshot_path: str


class EventStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.snapshot_dir = data_dir / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = data_dir / "events.db"
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plate_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    camera_name TEXT NOT NULL,
                    plate TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    detected_at TEXT NOT NULL,
                    snapshot_path TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plate_events_plate_time "
                "ON plate_events(plate, detected_at)"
            )

    def record(
        self,
        camera_id: str,
        camera_name: str,
        plate: str,
        confidence: float,
        frame,
        detected_at: datetime | None = None,
    ) -> PlateEvent:
        detected_at = detected_at or datetime.now().astimezone()
        safe_time = detected_at.strftime("%Y%m%d_%H%M%S_%f")
        safe_camera = "".join(c if c.isalnum() or c in "-_" else "_" for c in camera_id)
        snapshot = self.snapshot_dir / f"{safe_time}_{safe_camera}_{plate}.jpg"
        if not cv2.imwrite(str(snapshot), frame):
            snapshot = Path("")

        timestamp = detected_at.isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO plate_events
                    (camera_id, camera_name, plate, confidence, detected_at, snapshot_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (camera_id, camera_name, plate, confidence, timestamp, str(snapshot)),
            )
            event_id = int(cursor.lastrowid)
        return PlateEvent(
            id=event_id,
            camera_id=camera_id,
            camera_name=camera_name,
            plate=plate,
            confidence=confidence,
            detected_at=timestamp,
            snapshot_path=str(snapshot),
        )

    def latest(self, limit: int = 100) -> list[PlateEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plate_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [PlateEvent(**dict(row)) for row in rows]

    def export_csv(self, output_path: Path) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plate_events ORDER BY id"
            ).fetchall()
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["id", "camera_id", "camera_name", "plate", "confidence", "detected_at", "snapshot_path"]
            )
            for row in rows:
                writer.writerow(tuple(row))
        return len(rows)
