from __future__ import annotations

import csv
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

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
    direction: str = "IN"
    visit_id: int | None = None


@dataclass(frozen=True)
class VehicleVisit:
    id: int
    plate: str
    entry_event_id: int | None
    entry_at: str | None
    exit_event_id: int | None
    exit_at: str | None
    status: str
    duration_seconds: int | None
    created_at: str
    updated_at: str
    entry_camera_name: str | None = None
    exit_camera_name: str | None = None


class EventStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.snapshot_dir = data_dir / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = data_dir / "events.db"
        self._lock = threading.Lock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(plate_events)").fetchall()
            }
            if "direction" not in columns:
                connection.execute(
                    "ALTER TABLE plate_events ADD COLUMN direction TEXT NOT NULL DEFAULT 'IN'"
                )
            if "visit_id" not in columns:
                connection.execute("ALTER TABLE plate_events ADD COLUMN visit_id INTEGER")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cameras (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('IN', 'OUT')),
                    uri TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    start_delay_seconds REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            camera_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(cameras)").fetchall()
            }
            if "start_delay_seconds" not in camera_columns:
                connection.execute(
                    "ALTER TABLE cameras ADD COLUMN start_delay_seconds REAL NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vehicle_visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plate TEXT NOT NULL,
                    entry_event_id INTEGER,
                    entry_at TEXT,
                    exit_event_id INTEGER,
                    exit_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('INSIDE', 'COMPLETED', 'REVIEW')),
                    duration_seconds INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(entry_event_id) REFERENCES plate_events(id),
                    FOREIGN KEY(exit_event_id) REFERENCES plate_events(id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plate_events_plate_time "
                "ON plate_events(plate, detected_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_vehicle_visits_plate_time "
                "ON vehicle_visits(plate, entry_at, exit_at)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_visits_one_inside "
                "ON vehicle_visits(plate) WHERE status = 'INSIDE'"
            )

    @staticmethod
    def _normalize_direction(direction: str) -> str:
        value = str(direction).strip().upper()
        if value not in {"IN", "OUT"}:
            raise ValueError(f"Invalid camera direction: {direction}")
        return value

    def sync_cameras(self, cameras) -> None:
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        values = [
            (
                camera.id,
                camera.name,
                self._normalize_direction(camera.direction),
                camera.uri,
                int(camera.enabled),
                camera.start_delay_seconds,
                now,
            )
            for camera in cameras
        ]
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE cameras SET enabled = 0, updated_at = ?",
                (now,),
            )
            connection.executemany(
                """
                INSERT INTO cameras
                    (id, name, direction, uri, enabled, start_delay_seconds, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    direction = excluded.direction,
                    uri = excluded.uri,
                    enabled = excluded.enabled,
                    start_delay_seconds = excluded.start_delay_seconds,
                    updated_at = excluded.updated_at
                """,
                values,
            )

    def record(
        self,
        camera_id: str,
        camera_name: str,
        plate: str,
        confidence: float,
        frame,
        detected_at: datetime | None = None,
        direction: str = "IN",
    ) -> PlateEvent:
        detected_at = detected_at or datetime.now().astimezone()
        direction = self._normalize_direction(direction)
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
                    (camera_id, camera_name, plate, confidence, detected_at, snapshot_path, direction)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (camera_id, camera_name, plate, confidence, timestamp, str(snapshot), direction),
            )
            event_id = int(cursor.lastrowid)
            visit_id = self._pair_visit(connection, event_id, plate, direction, timestamp)
        return PlateEvent(
            id=event_id,
            camera_id=camera_id,
            camera_name=camera_name,
            plate=plate,
            confidence=confidence,
            detected_at=timestamp,
            snapshot_path=str(snapshot),
            direction=direction,
            visit_id=visit_id,
        )

    def _pair_visit(
        self,
        connection: sqlite3.Connection,
        event_id: int,
        plate: str,
        direction: str,
        timestamp: str,
    ) -> int:
        open_visit = connection.execute(
            """
            SELECT id, entry_at FROM vehicle_visits
            WHERE plate = ? AND status = 'INSIDE'
            ORDER BY id DESC LIMIT 1
            """,
            (plate,),
        ).fetchone()
        if direction == "IN":
            if open_visit is None:
                cursor = connection.execute(
                    """
                    INSERT INTO vehicle_visits
                        (plate, entry_event_id, entry_at, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'INSIDE', ?, ?)
                    """,
                    (plate, event_id, timestamp, timestamp, timestamp),
                )
                visit_id = int(cursor.lastrowid)
            else:
                visit_id = int(open_visit["id"])
        elif open_visit is not None:
            entry_at = datetime.fromisoformat(open_visit["entry_at"])
            exit_at = datetime.fromisoformat(timestamp)
            duration = max(0, round((exit_at - entry_at).total_seconds()))
            visit_id = int(open_visit["id"])
            connection.execute(
                """
                UPDATE vehicle_visits
                SET exit_event_id = ?, exit_at = ?, status = 'COMPLETED',
                    duration_seconds = ?, updated_at = ?
                WHERE id = ?
                """,
                (event_id, timestamp, duration, timestamp, visit_id),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO vehicle_visits
                    (plate, exit_event_id, exit_at, status, created_at, updated_at)
                VALUES (?, ?, ?, 'REVIEW', ?, ?)
                """,
                (plate, event_id, timestamp, timestamp, timestamp),
            )
            visit_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE plate_events SET visit_id = ? WHERE id = ?",
            (visit_id, event_id),
        )
        return visit_id

    def latest(self, limit: int = 100) -> list[PlateEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plate_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [PlateEvent(**dict(row)) for row in rows]

    def latest_visits(self, limit: int = 100, status: str | None = None) -> list[VehicleVisit]:
        where = "WHERE visits.status = ?" if status else ""
        parameters = (status, limit) if status else (limit,)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT visits.*,
                       entry_event.camera_name AS entry_camera_name,
                       exit_event.camera_name AS exit_camera_name
                FROM vehicle_visits AS visits
                LEFT JOIN plate_events AS entry_event ON entry_event.id = visits.entry_event_id
                LEFT JOIN plate_events AS exit_event ON exit_event.id = visits.exit_event_id
                {where}
                ORDER BY visits.id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [VehicleVisit(**dict(row)) for row in rows]

    def export_csv(self, output_path: Path) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plate_events ORDER BY id"
            ).fetchall()
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["id", "camera_id", "camera_name", "plate", "confidence", "detected_at", "snapshot_path", "direction", "visit_id"]
            )
            for row in rows:
                writer.writerow(tuple(row))
        return len(rows)

    def export_visits_csv(self, output_path: Path) -> int:
        visits = self.latest_visits(limit=1_000_000)
        visits.reverse()
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "id", "plate", "entry_at", "entry_camera", "exit_at",
                    "exit_camera", "status", "duration_seconds",
                ]
            )
            for visit in visits:
                writer.writerow(
                    [
                        visit.id, visit.plate, visit.entry_at, visit.entry_camera_name,
                        visit.exit_at, visit.exit_camera_name, visit.status,
                        visit.duration_seconds,
                    ]
                )
        return len(visits)

    def clear_saved_plates(self) -> tuple[int, int, int]:
        """Deletes recognition history and snapshots, but keeps camera settings."""
        with self._lock, self._connect() as connection:
            event_count = int(
                connection.execute("SELECT COUNT(*) FROM plate_events").fetchone()[0]
            )
            visit_count = int(
                connection.execute("SELECT COUNT(*) FROM vehicle_visits").fetchone()[0]
            )
            connection.execute("DELETE FROM vehicle_visits")
            connection.execute("DELETE FROM plate_events")
            connection.execute(
                "DELETE FROM sqlite_sequence WHERE name IN ('plate_events', 'vehicle_visits')"
            )

        snapshot_count = 0
        for path in self.snapshot_dir.iterdir():
            if path.is_file():
                path.unlink()
                snapshot_count += 1
        return event_count, visit_count, snapshot_count
