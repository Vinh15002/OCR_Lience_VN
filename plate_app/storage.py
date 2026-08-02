from __future__ import annotations

import calendar
import csv
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

import cv2

from .auth import ROLE_ADMIN, ROLE_OPERATOR, User, hash_password, verify_password
from .parking import (
    ALLOW,
    MOTORBIKE,
    POLICY_ALL,
    RegisteredVehicle,
    Tariff,
    TariffTable,
    decide_access,
    normalize_vehicle_type,
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
    source: str = "AUTO"  # AUTO (camera) | MANUAL (typed in by the operator)


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
    vehicle_type: str = MOTORBIKE
    review_flag: str = ""       # why this visit needs a human look ("" = clean)
    collected_by: str | None = None
    shift_id: int | None = None
    note: str = ""
    payment_reference: str = ""
    entry_camera_name: str | None = None
    exit_camera_name: str | None = None


class EventStore:
    def __init__(
        self,
        data_dir: Path,
        policy: str = POLICY_ALL,
        tariff: Tariff | None = None,
        tariff_table: TariffTable | None = None,
        default_vehicle_type: str = MOTORBIKE,
    ):
        self.data_dir = data_dir
        self.snapshot_dir = data_dir / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = data_dir / "events.db"
        self._lock = threading.Lock()
        self.policy = policy
        self.tariff = tariff or Tariff()
        self.tariff_table = tariff_table or TariffTable(default=self.tariff)
        self.default_vehicle_type = normalize_vehicle_type(default_vehicle_type)
        # Thresholds that decide when a completed visit is flagged for a human.
        self.low_confidence = 0.6
        self.min_visit_seconds = 30
        self._initialize()

    def configure_rules(
        self,
        policy: str,
        tariff: Tariff,
        tariff_table: TariffTable | None = None,
        default_vehicle_type: str | None = None,
    ) -> None:
        """Update gate policy and price list without reopening the store."""
        self.policy = policy
        self.tariff = tariff
        self.tariff_table = tariff_table or TariffTable(default=tariff)
        if default_vehicle_type:
            self.default_vehicle_type = normalize_vehicle_type(default_vehicle_type)

    def open_connection(self):
        """Read access for the reporting layer (see `plate_app.analytics`)."""
        return self._connect()

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
            if "source" not in columns:
                connection.execute(
                    "ALTER TABLE plate_events ADD COLUMN source TEXT NOT NULL DEFAULT 'AUTO'"
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
            if "vehicle_type" not in visit_columns:
                connection.execute(
                    "ALTER TABLE vehicle_visits ADD COLUMN vehicle_type TEXT NOT NULL "
                    "DEFAULT 'MOTORBIKE'"
                )
            if "review_flag" not in visit_columns:
                connection.execute(
                    "ALTER TABLE vehicle_visits ADD COLUMN review_flag TEXT NOT NULL DEFAULT ''"
                )
            if "collected_by" not in visit_columns:
                connection.execute("ALTER TABLE vehicle_visits ADD COLUMN collected_by TEXT")
            if "shift_id" not in visit_columns:
                connection.execute("ALTER TABLE vehicle_visits ADD COLUMN shift_id INTEGER")
            if "note" not in visit_columns:
                connection.execute(
                    "ALTER TABLE vehicle_visits ADD COLUMN note TEXT NOT NULL DEFAULT ''"
                )
            if "payment_reference" not in visit_columns:
                connection.execute(
                    "ALTER TABLE vehicle_visits ADD COLUMN payment_reference TEXT NOT NULL "
                    "DEFAULT ''"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS app_state ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
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
            vehicle_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(registered_vehicles)"
                ).fetchall()
            }
            if vehicle_columns and "vehicle_type" not in vehicle_columns:
                connection.execute(
                    "ALTER TABLE registered_vehicles ADD COLUMN vehicle_type TEXT NOT NULL "
                    "DEFAULT 'MOTORBIKE'"
                )
            if vehicle_columns and "phone" not in vehicle_columns:
                connection.execute(
                    "ALTER TABLE registered_vehicles ADD COLUMN phone TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    opening_cash REAL NOT NULL DEFAULT 0,
                    counted_cash REAL,
                    expected_cash REAL,
                    note TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plate TEXT NOT NULL,
                    vehicle_type TEXT NOT NULL DEFAULT 'MOTORBIKE',
                    months INTEGER NOT NULL DEFAULT 1,
                    amount REAL NOT NULL DEFAULT 0,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT NOT NULL,
                    paid_at TEXT NOT NULL,
                    created_by TEXT,
                    shift_id INTEGER
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscriptions_plate "
                "ON subscriptions(plate, paid_at)"
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
        source: str = "AUTO",
        vehicle_type: str | None = None,
    ) -> PlateEvent:
        detected_at = detected_at or datetime.now().astimezone()
        direction = self._normalize_direction(direction)
        snapshot = Path("")
        if frame is not None:
            safe_time = detected_at.strftime("%Y%m%d_%H%M%S_%f")
            safe_camera = "".join(c if c.isalnum() or c in "-_" else "_" for c in camera_id)
            candidate = self.snapshot_dir / f"{safe_time}_{safe_camera}_{plate}.jpg"
            if cv2.imwrite(str(candidate), frame):
                snapshot = candidate

        snapshot_path = str(snapshot) if snapshot.name else ""
        timestamp = detected_at.isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            vehicle = self._find_vehicle(connection, plate)
            decision = decide_access(vehicle, self.policy, detected_at)
            # A registered vehicle knows its own class; otherwise fall back to
            # the class the operator picked for this site.
            resolved_type = normalize_vehicle_type(
                vehicle_type or (vehicle.vehicle_type if vehicle else None),
                default=self.default_vehicle_type,
            )
            cursor = connection.execute(
                """
                INSERT INTO plate_events
                    (camera_id, camera_name, plate, confidence, detected_at,
                     snapshot_path, direction, access_status, access_reason, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    camera_id, camera_name, plate, confidence, timestamp,
                    snapshot_path, direction, decision.status, decision.reason,
                    "MANUAL" if source == "MANUAL" else "AUTO",
                ),
            )
            event_id = int(cursor.lastrowid)
            visit_id = self._pair_visit(
                connection, event_id, plate, direction, timestamp, decision.status,
                vehicle_type=resolved_type, confidence=confidence,
            )
        return PlateEvent(
            id=event_id,
            camera_id=camera_id,
            camera_name=camera_name,
            plate=plate,
            confidence=confidence,
            detected_at=timestamp,
            snapshot_path=snapshot_path,
            direction=direction,
            visit_id=visit_id,
            access_status=decision.status,
            access_reason=decision.reason,
            source="MANUAL" if source == "MANUAL" else "AUTO",
        )

    def record_manual(
        self,
        plate: str,
        direction: str,
        username: str | None = None,
        vehicle_type: str | None = None,
        camera_name: str = "Thủ công",
        note: str = "",
    ) -> PlateEvent:
        """Log an entry/exit typed in by the operator.

        Needed whenever the camera missed the vehicle: a lost ticket, a plate
        the OCR cannot read, or a vehicle waved through by hand.
        """
        plate = self._normalize_plate(plate)
        if not plate:
            raise ValueError("Biển số không được trống")
        event = self.record(
            camera_id="manual",
            camera_name=camera_name,
            plate=plate,
            confidence=1.0,
            frame=None,
            direction=direction,
            source="MANUAL",
            vehicle_type=vehicle_type,
        )
        if event.visit_id:
            self._flag_visit(event.visit_id, "manual", note=note)
        self.write_audit(
            username, f"MANUAL_{event.direction}", f"{plate} {note}".strip()
        )
        return event

    def _pair_visit(
        self,
        connection: sqlite3.Connection,
        event_id: int,
        plate: str,
        direction: str,
        timestamp: str,
        access_status: str = "UNKNOWN",
        vehicle_type: str | None = None,
        confidence: float = 1.0,
    ) -> int:
        vehicle_type = normalize_vehicle_type(vehicle_type, default=self.default_vehicle_type)
        open_visit = connection.execute(
            """
            SELECT id, entry_at, vehicle_type FROM vehicle_visits
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
                        (plate, entry_event_id, entry_at, status, vehicle_type,
                         created_at, updated_at)
                    VALUES (?, ?, ?, 'INSIDE', ?, ?, ?)
                    """,
                    (plate, event_id, timestamp, vehicle_type, timestamp, timestamp),
                )
                visit_id = int(cursor.lastrowid)
            else:
                visit_id = int(open_visit["id"])
        elif open_visit is not None:
            entry_at = datetime.fromisoformat(open_visit["entry_at"])
            exit_at = datetime.fromisoformat(timestamp)
            duration = max(0, round((exit_at - entry_at).total_seconds()))
            visit_type = normalize_vehicle_type(
                open_visit["vehicle_type"], default=vehicle_type
            )
            fee = self.tariff_table.fee_for_period(visit_type, entry_at, exit_at, duration)
            # Registered vehicles (subscribers) and zero-fee visits pass free;
            # a paying guest owes money until the operator collects it.
            payment_status = "EXEMPT" if access_status == ALLOW or fee <= 0 else "UNPAID"
            visit_id = int(open_visit["id"])
            connection.execute(
                """
                UPDATE vehicle_visits
                SET exit_event_id = ?, exit_at = ?, status = 'COMPLETED',
                    duration_seconds = ?, fee = ?, payment_status = ?,
                    review_flag = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    event_id, timestamp, duration, fee, payment_status,
                    self._exit_review_flag(connection, open_visit["id"], duration, confidence),
                    timestamp, visit_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO vehicle_visits
                    (plate, exit_event_id, exit_at, status, vehicle_type, review_flag,
                     created_at, updated_at)
                VALUES (?, ?, ?, 'REVIEW', ?, 'no_entry', ?, ?)
                """,
                (plate, event_id, timestamp, vehicle_type, timestamp, timestamp),
            )
            visit_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE plate_events SET visit_id = ? WHERE id = ?",
            (visit_id, event_id),
        )
        return visit_id

    # --- Reconciliation (đối soát vào/ra) ---

    def _exit_review_flag(
        self,
        connection: sqlite3.Connection,
        visit_id: int,
        duration: int,
        exit_confidence: float,
    ) -> str:
        """Reasons a completed visit deserves a look at the two snapshots.

        The plates already match (that is how the visit was paired), so what is
        left to catch is a weak read on either end or a stay too short to be a
        real one — the shapes a swapped vehicle usually takes.
        """
        reasons: list[str] = []
        row = connection.execute(
            """
            SELECT entry_event.confidence AS entry_confidence,
                   entry_event.source AS entry_source,
                   visits.review_flag AS review_flag
            FROM vehicle_visits AS visits
            LEFT JOIN plate_events AS entry_event ON entry_event.id = visits.entry_event_id
            WHERE visits.id = ?
            """,
            (visit_id,),
        ).fetchone()
        entry_confidence = float(row["entry_confidence"] or 1.0) if row else 1.0
        if min(entry_confidence, float(exit_confidence)) < self.low_confidence:
            reasons.append("low_confidence")
        if duration < self.min_visit_seconds:
            reasons.append("short_stay")
        if row and row["entry_source"] == "MANUAL":
            reasons.append("manual")
        previous = (row["review_flag"] if row else "") or ""
        for reason in previous.split(","):
            if reason and reason not in reasons:
                reasons.append(reason)
        return ",".join(reasons)

    def _flag_visit(self, visit_id: int, reason: str, note: str = "") -> None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT review_flag, note FROM vehicle_visits WHERE id = ?", (visit_id,)
            ).fetchone()
            if row is None:
                return
            flags = [item for item in (row["review_flag"] or "").split(",") if item]
            if reason and reason not in flags:
                flags.append(reason)
            note_text = " ".join(filter(None, [(row["note"] or "").strip(), note.strip()]))
            connection.execute(
                "UPDATE vehicle_visits SET review_flag = ?, note = ? WHERE id = ?",
                (",".join(flags), note_text, visit_id),
            )

    def clear_visit_flag(self, visit_id: int, username: str | None = None) -> int:
        """Operator confirmed the vehicle is the right one."""
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE vehicle_visits SET review_flag = '', updated_at = ? WHERE id = ?",
                (now, visit_id),
            )
        if cursor.rowcount:
            self.write_audit(username, "VISIT_VERIFIED", str(visit_id))
        return cursor.rowcount

    def visit_detail(self, visit_id: int) -> dict | None:
        """The visit plus both snapshots, for the entry/exit comparison window."""
        with self._connect() as connection:
            visit = connection.execute(
                "SELECT * FROM vehicle_visits WHERE id = ?", (visit_id,)
            ).fetchone()
            if visit is None:
                return None
            events = {}
            for key, column in (("entry", "entry_event_id"), ("exit", "exit_event_id")):
                event_id = visit[column]
                if not event_id:
                    events[key] = None
                    continue
                row = connection.execute(
                    "SELECT * FROM plate_events WHERE id = ?", (event_id,)
                ).fetchone()
                events[key] = dict(row) if row is not None else None
        detail = dict(visit)
        detail["entry_event"] = events["entry"]
        detail["exit_event"] = events["exit"]
        return detail

    def update_visit_plate(
        self, visit_id: int, new_plate: str, username: str | None = None
    ) -> str:
        """Correct a misread plate on a visit and both of its events."""
        plate = self._normalize_plate(new_plate)
        if not plate:
            raise ValueError("Biển số không được trống")
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT plate, status FROM vehicle_visits WHERE id = ?", (visit_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Không tìm thấy lượt {visit_id}")
            if plate != row["plate"] and row["status"] == "INSIDE":
                clash = connection.execute(
                    "SELECT id FROM vehicle_visits WHERE plate = ? AND status = 'INSIDE'",
                    (plate,),
                ).fetchone()
                if clash is not None:
                    raise ValueError(f"Biển {plate} đang có lượt trong bãi")
            connection.execute(
                "UPDATE vehicle_visits SET plate = ?, updated_at = ? WHERE id = ?",
                (plate, now, visit_id),
            )
            connection.execute(
                "UPDATE plate_events SET plate = ? WHERE visit_id = ?", (plate, visit_id)
            )
            old_plate = row["plate"]
        self.write_audit(username, "PLATE_CORRECTED", f"{old_plate} -> {plate}")
        return plate

    def set_visit_vehicle_type(
        self, visit_id: int, vehicle_type: str, username: str | None = None
    ) -> float | None:
        """Reclassify a visit; recompute the fee when it has already left."""
        vehicle_type = normalize_vehicle_type(vehicle_type, default=self.default_vehicle_type)
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM vehicle_visits WHERE id = ?", (visit_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Không tìm thấy lượt {visit_id}")
            fee = row["fee"]
            if row["status"] == "COMPLETED" and row["payment_status"] != "PAID":
                entry_at = datetime.fromisoformat(row["entry_at"]) if row["entry_at"] else None
                exit_at = datetime.fromisoformat(row["exit_at"]) if row["exit_at"] else None
                fee = self.tariff_table.fee_for_period(
                    vehicle_type, entry_at, exit_at, row["duration_seconds"]
                )
                payment_status = "EXEMPT" if fee <= 0 else row["payment_status"]
                connection.execute(
                    "UPDATE vehicle_visits SET vehicle_type = ?, fee = ?, payment_status = ?, "
                    "updated_at = ? WHERE id = ?",
                    (vehicle_type, fee, payment_status, now, visit_id),
                )
            else:
                connection.execute(
                    "UPDATE vehicle_visits SET vehicle_type = ?, updated_at = ? WHERE id = ?",
                    (vehicle_type, now, visit_id),
                )
        self.write_audit(username, "VISIT_RETYPED", f"{visit_id} {vehicle_type}")
        return fee

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
            vehicle_type=normalize_vehicle_type(row["vehicle_type"]),
            phone=row["phone"] or "",
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
                     active, vehicle_type, phone, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plate) DO UPDATE SET
                    owner_name = excluded.owner_name,
                    access = excluded.access,
                    note = excluded.note,
                    valid_from = excluded.valid_from,
                    valid_until = excluded.valid_until,
                    active = excluded.active,
                    vehicle_type = excluded.vehicle_type,
                    phone = excluded.phone,
                    updated_at = excluded.updated_at
                """,
                (
                    plate, vehicle.owner_name, access, vehicle.note,
                    vehicle.valid_from or None, vehicle.valid_until or None,
                    int(vehicle.active), normalize_vehicle_type(vehicle.vehicle_type),
                    vehicle.phone, now, now,
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

    def mark_paid(
        self,
        visit_id: int,
        method: str = "CASH",
        username: str | None = None,
        shift_id: int | None = None,
        reference: str = "",
    ) -> int:
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE vehicle_visits
                SET payment_status = 'PAID', payment_method = ?, paid_at = ?,
                    collected_by = ?, shift_id = ?, payment_reference = ?, updated_at = ?
                WHERE id = ? AND status = 'COMPLETED' AND payment_status != 'PAID'
                """,
                (method, now, username, shift_id, reference, now, visit_id),
            )
            return cursor.rowcount

    def pending_payments(self, limit: int = 200) -> dict[int, float]:
        """Visits waiting for money: visit id -> amount owed."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, COALESCE(fee, 0) AS fee FROM vehicle_visits
                WHERE status = 'COMPLETED' AND payment_status = 'UNPAID' AND COALESCE(fee, 0) > 0
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {int(row["id"]): float(row["fee"]) for row in rows}

    def get_state(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row is not None else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

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

    # --- Monthly passes (vé tháng) ---

    @staticmethod
    def _add_months(start: date, months: int) -> date:
        """One month later means the same day-of-month, clamped to month length."""
        month_index = start.month - 1 + max(1, int(months))
        year = start.year + month_index // 12
        month = month_index % 12 + 1
        day = min(start.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def add_subscription(
        self,
        plate: str,
        months: int = 1,
        amount: float = 0.0,
        vehicle_type: str | None = None,
        owner_name: str = "",
        phone: str = "",
        note: str = "",
        username: str | None = None,
        shift_id: int | None = None,
        today: date | None = None,
    ) -> RegisteredVehicle:
        """Sell or renew a monthly pass; extends the whitelist entry in place."""
        plate = self._normalize_plate(plate)
        if not plate:
            raise ValueError("Biển số không được trống")
        months = max(1, int(months))
        today = today or date.today()
        existing = self.find_vehicle(plate)
        vehicle_type = normalize_vehicle_type(
            vehicle_type or (existing.vehicle_type if existing else None),
            default=self.default_vehicle_type,
        )
        # Renewing early stacks onto the remaining days instead of losing them.
        start = today
        if existing and existing.valid_until:
            current_end = date.fromisoformat(existing.valid_until[:10])
            if current_end >= today:
                start = current_end + timedelta(days=1)
        valid_until = self._add_months(start, months) - timedelta(days=1)
        vehicle = RegisteredVehicle(
            plate=plate,
            owner_name=owner_name or (existing.owner_name if existing else ""),
            access=ALLOW,
            note=note or (existing.note if existing else ""),
            valid_from=(existing.valid_from if existing and existing.valid_from else today.isoformat()),
            valid_until=valid_until.isoformat(),
            active=True,
            vehicle_type=vehicle_type,
            phone=phone or (existing.phone if existing else ""),
        )
        self.upsert_vehicle(vehicle)
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO subscriptions
                    (plate, vehicle_type, months, amount, valid_from, valid_until,
                     paid_at, created_by, shift_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plate, vehicle_type, months, float(amount), start.isoformat(),
                    valid_until.isoformat(), now, username, shift_id,
                ),
            )
        self.write_audit(username, "SUBSCRIPTION", f"{plate} {months}m {round(float(amount))}")
        return vehicle

    def list_subscriptions(self, limit: int = 200) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM subscriptions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def subscription_revenue(self, since: str | None = None) -> dict[str, float]:
        query = "SELECT COUNT(*) AS tickets, COALESCE(SUM(amount), 0) AS total FROM subscriptions"
        parameters: tuple = ()
        if since:
            query += " WHERE paid_at >= ?"
            parameters = (since,)
        with self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return {"tickets": int(row["tickets"]), "total": float(row["total"])}

    def expiring_vehicles(self, days: int = 7, today: date | None = None) -> list[RegisteredVehicle]:
        """Passes that expire within `days` (negative days_left = already expired)."""
        today = today or date.today()
        limit = (today + timedelta(days=max(0, int(days)))).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM registered_vehicles
                WHERE access = 'ALLOW' AND active = 1
                  AND valid_until IS NOT NULL AND valid_until <= ?
                ORDER BY valid_until
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_vehicle(row) for row in rows]

    # --- Shifts and end-of-shift cash reconciliation (ca trực) ---

    def current_shift(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM shifts WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def open_shift(self, username: str | None, opening_cash: float = 0.0) -> dict:
        if self.current_shift() is not None:
            raise ValueError("Đang có ca trực chưa đóng")
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO shifts (username, opened_at, opening_cash) VALUES (?, ?, ?)",
                (username or "", now, float(opening_cash)),
            )
            shift_id = int(cursor.lastrowid)
        self.write_audit(username, "SHIFT_OPEN", f"{shift_id} {round(float(opening_cash))}")
        return self.shift(shift_id)

    def shift(self, shift_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if row is None:
            raise ValueError(f"Không tìm thấy ca {shift_id}")
        return dict(row)

    def shift_totals(self, shift_id: int) -> dict[str, float]:
        """What the shift took in, split by payment method."""
        with self._connect() as connection:
            visits = connection.execute(
                """
                SELECT payment_method, COUNT(*) AS visits, COALESCE(SUM(fee), 0) AS total
                FROM vehicle_visits
                WHERE shift_id = ? AND payment_status = 'PAID'
                GROUP BY payment_method
                """,
                (shift_id,),
            ).fetchall()
            subs = connection.execute(
                "SELECT COUNT(*) AS tickets, COALESCE(SUM(amount), 0) AS total "
                "FROM subscriptions WHERE shift_id = ?",
                (shift_id,),
            ).fetchone()
            opening = connection.execute(
                "SELECT opening_cash FROM shifts WHERE id = ?", (shift_id,)
            ).fetchone()
        totals = {
            "cash_total": 0.0, "cash_visits": 0,
            "qr_total": 0.0, "qr_visits": 0,
            "subscription_total": float(subs["total"]), "subscription_tickets": int(subs["tickets"]),
            "opening_cash": float(opening["opening_cash"]) if opening else 0.0,
        }
        for row in visits:
            prefix = "qr" if (row["payment_method"] or "CASH").upper() == "QR" else "cash"
            totals[f"{prefix}_total"] += float(row["total"])
            totals[f"{prefix}_visits"] += int(row["visits"])
        # Monthly passes are assumed to be paid in cash at the booth.
        totals["expected_cash"] = (
            totals["opening_cash"] + totals["cash_total"] + totals["subscription_total"]
        )
        totals["collected_total"] = (
            totals["cash_total"] + totals["qr_total"] + totals["subscription_total"]
        )
        return totals

    def close_shift(
        self, shift_id: int, counted_cash: float, note: str = "", username: str | None = None
    ) -> dict:
        totals = self.shift_totals(shift_id)
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE shifts SET closed_at = ?, counted_cash = ?, expected_cash = ?, note = ? "
                "WHERE id = ? AND closed_at IS NULL",
                (now, float(counted_cash), totals["expected_cash"], note, shift_id),
            )
        difference = float(counted_cash) - totals["expected_cash"]
        self.write_audit(
            username, "SHIFT_CLOSE",
            f"{shift_id} thu={round(totals['expected_cash'])} đếm={round(float(counted_cash))} "
            f"lệch={round(difference)}",
        )
        result = dict(totals)
        result.update(self.shift(shift_id))
        result["difference"] = difference
        return result

    def list_shifts(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM shifts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Data retention ---

    def purge_older_than(self, days: int, username: str | None = None) -> dict[str, int]:
        """Delete visits, events and snapshots older than `days`.

        Vehicles still inside are never touched, however old their entry is.
        """
        days = int(days)
        if days <= 0:
            return {"visits": 0, "events": 0, "snapshots": 0}
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(
            timespec="milliseconds"
        )
        with self._lock, self._connect() as connection:
            visits = connection.execute(
                "DELETE FROM vehicle_visits WHERE status != 'INSIDE' "
                "AND COALESCE(exit_at, created_at) < ?",
                (cutoff,),
            ).rowcount
            doomed = connection.execute(
                """
                SELECT id, snapshot_path FROM plate_events
                WHERE detected_at < ?
                  AND id NOT IN (
                      SELECT entry_event_id FROM vehicle_visits WHERE entry_event_id IS NOT NULL
                      UNION
                      SELECT exit_event_id FROM vehicle_visits WHERE exit_event_id IS NOT NULL
                  )
                """,
                (cutoff,),
            ).fetchall()
            removed_files = 0
            for row in doomed:
                path = Path(row["snapshot_path"] or "")
                if path.name and path.exists():
                    try:
                        path.unlink()
                        removed_files += 1
                    except OSError:
                        pass
            if doomed:
                connection.executemany(
                    "DELETE FROM plate_events WHERE id = ?",
                    [(row["id"],) for row in doomed],
                )
        result = {"visits": visits, "events": len(doomed), "snapshots": removed_files}
        if result["visits"] or result["events"]:
            self.write_audit(
                username, "PURGE",
                f"{days}d visits={result['visits']} events={result['events']}",
            )
        return result

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
