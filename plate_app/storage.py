from __future__ import annotations

import csv
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import cv2

from .auth import ROLE_ADMIN, ROLE_OPERATOR, User, hash_password, verify_password
from .parking import (
    ALLOW,
    POLICY_ALL,
    RegisteredVehicle,
    Tariff,
    decide_access,
)


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
    access_status: str = "UNKNOWN"
    access_reason: str = ""


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
    fee: float | None = None
    payment_status: str = "UNPAID"
    payment_method: str | None = None
    paid_at: str | None = None
    entry_camera_name: str | None = None
    exit_camera_name: str | None = None


class EventStore:
    def __init__(
        self,
        data_dir: Path,
        policy: str = POLICY_ALL,
        tariff: Tariff | None = None,
    ):
        self.data_dir = data_dir
        self.snapshot_dir = data_dir / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = data_dir / "events.db"
        self._lock = threading.Lock()
        self.policy = policy
        self.tariff = tariff or Tariff()
        self._initialize()

    def configure_rules(self, policy: str, tariff: Tariff) -> None:
        """Update gate policy and price list without reopening the store."""
        self.policy = policy
        self.tariff = tariff

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
            if "access_status" not in columns:
                connection.execute(
                    "ALTER TABLE plate_events ADD COLUMN access_status TEXT NOT NULL DEFAULT 'UNKNOWN'"
                )
            if "access_reason" not in columns:
                connection.execute(
                    "ALTER TABLE plate_events ADD COLUMN access_reason TEXT NOT NULL DEFAULT ''"
                )
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
            visit_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(vehicle_visits)").fetchall()
            }
            if "fee" not in visit_columns:
                connection.execute("ALTER TABLE vehicle_visits ADD COLUMN fee REAL")
            if "payment_status" not in visit_columns:
                connection.execute(
                    "ALTER TABLE vehicle_visits ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'UNPAID'"
                )
            if "payment_method" not in visit_columns:
                connection.execute("ALTER TABLE vehicle_visits ADD COLUMN payment_method TEXT")
            if "paid_at" not in visit_columns:
                connection.execute("ALTER TABLE vehicle_visits ADD COLUMN paid_at TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS registered_vehicles (
                    plate TEXT PRIMARY KEY,
                    owner_name TEXT NOT NULL DEFAULT '',
                    access TEXT NOT NULL DEFAULT 'ALLOW' CHECK(access IN ('ALLOW', 'DENY')),
                    note TEXT NOT NULL DEFAULT '',
                    valid_from TEXT,
                    valid_until TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'operator' CHECK(role IN ('admin', 'operator')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    username TEXT,
                    action TEXT NOT NULL,
                    detail TEXT
                )
                """
            )
            if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                now = datetime.now().astimezone().isoformat(timespec="milliseconds")
                connection.execute(
                    "INSERT INTO users (username, password_hash, role, active, created_at) "
                    "VALUES ('admin', ?, 'admin', 1, ?)",
                    (hash_password("admin"), now),
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
            vehicle = self._find_vehicle(connection, plate)
            decision = decide_access(vehicle, self.policy, detected_at)
            cursor = connection.execute(
                """
                INSERT INTO plate_events
                    (camera_id, camera_name, plate, confidence, detected_at,
                     snapshot_path, direction, access_status, access_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    camera_id, camera_name, plate, confidence, timestamp,
                    str(snapshot), direction, decision.status, decision.reason,
                ),
            )
            event_id = int(cursor.lastrowid)
            visit_id = self._pair_visit(
                connection, event_id, plate, direction, timestamp, decision.status
            )
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
            access_status=decision.status,
            access_reason=decision.reason,
        )

    def _pair_visit(
        self,
        connection: sqlite3.Connection,
        event_id: int,
        plate: str,
        direction: str,
        timestamp: str,
        access_status: str = "UNKNOWN",
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
            fee = self.tariff.fee_for(duration)
            # Registered vehicles (subscribers) and zero-fee visits pass free;
            # a paying guest owes money until the operator collects it.
            payment_status = "EXEMPT" if access_status == ALLOW or fee <= 0 else "UNPAID"
            visit_id = int(open_visit["id"])
            connection.execute(
                """
                UPDATE vehicle_visits
                SET exit_event_id = ?, exit_at = ?, status = 'COMPLETED',
                    duration_seconds = ?, fee = ?, payment_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (event_id, timestamp, duration, fee, payment_status, timestamp, visit_id),
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

    def count_events(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM plate_events").fetchone()[0])

    # --- Registered vehicles (whitelist / blacklist) ---

    @staticmethod
    def _normalize_plate(text: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text).upper())

    @staticmethod
    def _row_to_vehicle(row) -> RegisteredVehicle:
        return RegisteredVehicle(
            plate=row["plate"],
            owner_name=row["owner_name"],
            access=row["access"],
            note=row["note"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            active=bool(row["active"]),
        )

    def _find_vehicle(self, connection: sqlite3.Connection, plate: str) -> RegisteredVehicle | None:
        row = connection.execute(
            "SELECT * FROM registered_vehicles WHERE plate = ?",
            (self._normalize_plate(plate),),
        ).fetchone()
        return self._row_to_vehicle(row) if row is not None else None

    def find_vehicle(self, plate: str) -> RegisteredVehicle | None:
        with self._connect() as connection:
            return self._find_vehicle(connection, plate)

    def list_vehicles(self) -> list[RegisteredVehicle]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM registered_vehicles ORDER BY plate"
            ).fetchall()
        return [self._row_to_vehicle(row) for row in rows]

    def upsert_vehicle(self, vehicle: RegisteredVehicle) -> str:
        plate = self._normalize_plate(vehicle.plate)
        if not plate:
            raise ValueError("Plate cannot be empty")
        access = str(vehicle.access).strip().upper()
        if access not in {"ALLOW", "DENY"}:
            raise ValueError(f"Invalid access: {vehicle.access}")
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO registered_vehicles
                    (plate, owner_name, access, note, valid_from, valid_until,
                     active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plate) DO UPDATE SET
                    owner_name = excluded.owner_name,
                    access = excluded.access,
                    note = excluded.note,
                    valid_from = excluded.valid_from,
                    valid_until = excluded.valid_until,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    plate, vehicle.owner_name, access, vehicle.note,
                    vehicle.valid_from or None, vehicle.valid_until or None,
                    int(vehicle.active), now, now,
                ),
            )
        return plate

    def remove_vehicle(self, plate: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM registered_vehicles WHERE plate = ?",
                (self._normalize_plate(plate),),
            )
            return cursor.rowcount

    # --- Users, roles, audit trail ---

    def create_user(self, username: str, password: str, role: str = ROLE_OPERATOR) -> None:
        username = username.strip()
        if not username:
            raise ValueError("Tên đăng nhập không được trống")
        if not password:
            raise ValueError("Mật khẩu không được trống")
        role = role if role in (ROLE_ADMIN, ROLE_OPERATOR) else ROLE_OPERATOR
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (username, password_hash, role, active, created_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    role = excluded.role
                """,
                (username, hash_password(password), role, now),
            )

    def verify_login(self, username: str, password: str) -> User | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT username, password_hash, role, active FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        if row is None or not row["active"]:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return User(username=row["username"], role=row["role"], active=bool(row["active"]))

    def list_users(self) -> list[User]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT username, role, active FROM users ORDER BY username"
            ).fetchall()
        return [User(username=r["username"], role=r["role"], active=bool(r["active"])) for r in rows]

    def set_user_active(self, username: str, active: bool) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE users SET active = ? WHERE username = ?",
                (int(active), username.strip()),
            )

    def delete_user(self, username: str) -> int:
        username = username.strip()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT role FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row is None:
                return 0
            if row["role"] == ROLE_ADMIN:
                admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
                ).fetchone()[0]
                if admins <= 1:
                    raise ValueError("Phải còn ít nhất một tài khoản admin")
            cursor = connection.execute("DELETE FROM users WHERE username = ?", (username,))
            return cursor.rowcount

    def write_audit(self, username: str | None, action: str, detail: str = "") -> None:
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_log (ts, username, action, detail) VALUES (?, ?, ?, ?)",
                (now, username, action, detail),
            )

    def list_audit(self, limit: int = 200) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT ts, username, action, detail FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Reporting & maintenance ---

    def revenue_by_day(self, limit: int = 30) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT substr(exit_at, 1, 10) AS day,
                       SUM(CASE WHEN payment_status = 'PAID' THEN fee ELSE 0 END) AS paid_total,
                       SUM(CASE WHEN payment_status = 'PAID' THEN 1 ELSE 0 END) AS paid_count,
                       SUM(CASE WHEN payment_status = 'UNPAID' THEN fee ELSE 0 END) AS unpaid_total,
                       SUM(CASE WHEN payment_status = 'UNPAID' THEN 1 ELSE 0 END) AS unpaid_count
                FROM vehicle_visits
                WHERE status = 'COMPLETED' AND exit_at IS NOT NULL
                GROUP BY day
                ORDER BY day DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def backup_database(self) -> Path:
        backup_dir = self.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = backup_dir / f"events_{stamp}.db"
        with self._lock:
            source = sqlite3.connect(self.database_path)
            try:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
        return destination

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

    def mark_paid(self, visit_id: int, method: str = "CASH") -> int:
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE vehicle_visits
                SET payment_status = 'PAID', payment_method = ?, paid_at = ?, updated_at = ?
                WHERE id = ? AND status = 'COMPLETED'
                """,
                (method, now, now, visit_id),
            )
            return cursor.rowcount

    def revenue_summary(self, since: str | None = None) -> dict[str, float]:
        query = (
            "SELECT payment_status, COALESCE(fee, 0) AS fee "
            "FROM vehicle_visits WHERE status = 'COMPLETED'"
        )
        parameters: tuple = ()
        if since:
            query += " AND exit_at >= ?"
            parameters = (since,)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        summary = {"paid_total": 0.0, "paid_count": 0, "unpaid_total": 0.0, "unpaid_count": 0}
        for row in rows:
            if row["payment_status"] == "PAID":
                summary["paid_total"] += row["fee"]
                summary["paid_count"] += 1
            elif row["payment_status"] == "UNPAID":
                summary["unpaid_total"] += row["fee"]
                summary["unpaid_count"] += 1
        return summary

    def export_csv(self, output_path: Path) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plate_events ORDER BY id"
            ).fetchall()
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "id", "camera_id", "camera_name", "plate", "confidence",
                    "detected_at", "snapshot_path", "direction", "visit_id",
                    "access_status", "access_reason",
                ]
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
                    "exit_camera", "status", "duration_seconds", "fee",
                    "payment_status", "paid_at",
                ]
            )
            for visit in visits:
                writer.writerow(
                    [
                        visit.id, visit.plate, visit.entry_at, visit.entry_camera_name,
                        visit.exit_at, visit.exit_camera_name, visit.status,
                        visit.duration_seconds, visit.fee, visit.payment_status,
                        visit.paid_at,
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
