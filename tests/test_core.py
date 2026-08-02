import tempfile
import unittest
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from plate_app.config import AppConfig, CameraConfig, load_config, save_config
from plate_app.recognition import (
    ConsensusTracker,
    box_overlaps,
    boxes_match,
    correct_vietnamese_plate,
    is_plausible_vietnamese_plate,
    normalize_plate,
)
from plate_app.parking import (
    ALLOW,
    DENY,
    GUEST,
    POLICY_ALL,
    POLICY_REGISTERED_ONLY,
    RegisteredVehicle,
    Tariff,
    decide_access,
)
from plate_app.auth import ROLE_ADMIN, hash_password, verify_password
from plate_app.gate import (
    CompositeGate,
    SimulatedGate,
    TcpRelayGate,
    build_gate,
)
from plate_app.payment import BankAccount, build_vietqr, crc16_ccitt
from plate_app.storage import EventStore
from plate_app.video import CameraStream


class PlateTextTests(unittest.TestCase):
    def test_normalize_plate(self):
        self.assertEqual(normalize_plate("59-X3 123.45"), "59X312345")

    def test_plausible_motorcycle_plate(self):
        self.assertTrue(is_plausible_vietnamese_plate("59X312345"))
        self.assertTrue(is_plausible_vietnamese_plate("36B112345"))
        self.assertFalse(is_plausible_vietnamese_plate("UNKNOWN"))
        self.assertFalse(is_plausible_vietnamese_plate("12345678"))

    def test_correct_vietnamese_plate_fixes_confusions(self):
        # province digits read as letters, number digits read as letters
        self.assertEqual(correct_vietnamese_plate("S9X3I234S"), "59X312345")
        self.assertEqual(correct_vietnamese_plate("59X3O2345"), "59X302345")
        # already correct stays correct
        self.assertEqual(correct_vietnamese_plate("59X312345"), "59X312345")
        # a real misread should now pass the plausibility gate
        self.assertTrue(is_plausible_vietnamese_plate(correct_vietnamese_plate("S9X3l2345")))

    def test_correct_ignores_non_plate_noise(self):
        # too short to be a plate -> left as-is (no false positives)
        self.assertEqual(correct_vietnamese_plate("ABC"), "ABC")


class ConsensusTests(unittest.TestCase):
    def test_emits_after_two_votes_and_respects_cooldown(self):
        tracker = ConsensusTracker(min_votes=2, window_seconds=2.0, cooldown_seconds=10.0)
        self.assertIsNone(tracker.observe("cam-1", "59X312345", 0.8, 1.0))
        result = tracker.observe("cam-1", "59X312345", 0.9, 1.5)
        self.assertEqual(result[0], "59X312345")
        self.assertAlmostEqual(result[1], 0.85)
        self.assertIsNone(tracker.observe("cam-1", "59X312345", 0.9, 2.0))


class LightweightTrackingTests(unittest.TestCase):
    def test_matches_same_plate_after_small_motion(self):
        self.assertTrue(boxes_match((100, 100, 200, 150), (125, 105, 225, 155)))

    def test_rejects_distant_plate(self):
        self.assertFalse(boxes_match((100, 100, 200, 150), (500, 400, 600, 450)))


class RoiGateTests(unittest.TestCase):
    roi = (100, 100, 500, 400)

    def test_keeps_plate_touching_roi_bottom_edge(self):
        # Nearest vehicle: plate straddles the ROI bottom boundary.
        self.assertTrue(box_overlaps((250, 380, 350, 440), self.roi))

    def test_keeps_plate_fully_inside_roi(self):
        self.assertTrue(box_overlaps((200, 200, 300, 260), self.roi))

    def test_rejects_plate_outside_roi(self):
        self.assertFalse(box_overlaps((600, 500, 700, 560), self.roi))

    def test_rejects_plate_only_touching_edge_line(self):
        # Sharing just the boundary line is not an overlap.
        self.assertFalse(box_overlaps((500, 200, 600, 260), self.roi))


class ParkingRulesTests(unittest.TestCase):
    now = datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)

    def test_registered_vehicle_opens_gate(self):
        vehicle = RegisteredVehicle("59X312345", owner_name="An", access=ALLOW)
        decision = decide_access(vehicle, POLICY_ALL, self.now)
        self.assertEqual(decision.status, ALLOW)
        self.assertTrue(decision.opens_gate)

    def test_blacklisted_vehicle_denied_even_in_open_mode(self):
        vehicle = RegisteredVehicle("59X312345", access=DENY)
        decision = decide_access(vehicle, POLICY_ALL, self.now)
        self.assertEqual(decision.status, DENY)
        self.assertFalse(decision.opens_gate)

    def test_unknown_plate_is_guest_in_paid_mode(self):
        decision = decide_access(None, POLICY_ALL, self.now)
        self.assertEqual(decision.status, GUEST)
        self.assertTrue(decision.opens_gate)

    def test_unknown_plate_denied_in_registered_only_mode(self):
        decision = decide_access(None, POLICY_REGISTERED_ONLY, self.now)
        self.assertEqual(decision.status, DENY)

    def test_expired_registration_denied_in_registered_only_mode(self):
        vehicle = RegisteredVehicle(
            "59X312345", valid_until="2020-01-01T00:00:00+00:00"
        )
        decision = decide_access(vehicle, POLICY_REGISTERED_ONLY, self.now)
        self.assertEqual(decision.status, DENY)
        self.assertEqual(decision.reason, "expired")

    def test_flat_fee_only(self):
        self.assertEqual(Tariff(flat_fee=5000).fee_for(3600), 5000)

    def test_hourly_fee_rounds_up_started_hours(self):
        tariff = Tariff(flat_fee=3000, hourly_fee=2000, free_minutes=15)
        # 15 free + 1h01m billable -> 2 started hours
        self.assertEqual(tariff.fee_for((15 + 61) * 60), 3000 + 2 * 2000)

    def test_within_free_window_charges_flat_only(self):
        tariff = Tariff(flat_fee=3000, hourly_fee=2000, free_minutes=30)
        self.assertEqual(tariff.fee_for(10 * 60), 3000)


class ConfigAndStorageTests(unittest.TestCase):
    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = AppConfig(
                detection_interval_seconds=0.5,
                cameras=[
                    CameraConfig(
                        "cam-1",
                        "Gate",
                        "0",
                        loop_video=True,
                        direction="OUT",
                        start_delay_seconds=15,
                    )
                ],
            )
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.cameras[0].uri, "0")
            self.assertEqual(loaded.detection_interval_seconds, 0.5)
            self.assertEqual(loaded.detection_imgsz, 960)
            self.assertEqual(loaded.preview_fps, 20)
            self.assertEqual(loaded.ocr_recognition_model, "PP-OCRv6_medium_rec")
            self.assertEqual(loaded.cameras[0].direction, "OUT")
            self.assertEqual(loaded.cameras[0].start_delay_seconds, 15)
            self.assertTrue(loaded.cameras[0].loop_video)

    def test_empty_event_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            self.assertEqual(store.latest(), [])
            self.assertEqual(store.latest_visits(), [])

    def test_pairs_entry_and_exit_into_completed_visit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            entry_at = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
            entry = store.record(
                "gate-in", "Gate IN", "59X312345", 0.91, frame,
                entry_at, direction="IN",
            )
            inside = store.latest_visits(status="INSIDE")
            self.assertEqual(len(inside), 1)
            self.assertEqual(inside[0].entry_event_id, entry.id)

            exit_event = store.record(
                "gate-out", "Gate OUT", "59X312345", 0.89, frame,
                entry_at + timedelta(minutes=12), direction="OUT",
            )
            visits = store.latest_visits()
            self.assertEqual(len(visits), 1)
            self.assertEqual(visits[0].status, "COMPLETED")
            self.assertEqual(visits[0].exit_event_id, exit_event.id)
            self.assertEqual(visits[0].duration_seconds, 720)
            self.assertEqual(store.latest_visits(status="INSIDE"), [])

    def test_exit_without_entry_requires_review(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            event = store.record(
                "gate-out", "Gate OUT", "36B112345", 0.85, frame,
                direction="OUT",
            )
            visits = store.latest_visits()
            self.assertEqual(visits[0].status, "REVIEW")
            self.assertIsNone(visits[0].entry_at)
            self.assertEqual(visits[0].exit_event_id, event.id)

    def test_repeated_entry_keeps_one_open_visit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            first = store.record("in", "IN", "59X312345", 0.9, frame, direction="IN")
            second = store.record("in", "IN", "59X312345", 0.9, frame, direction="IN")
            visits = store.latest_visits(status="INSIDE")
            self.assertEqual(len(visits), 1)
            self.assertEqual(first.visit_id, second.visit_id)

    def test_migrates_existing_event_database_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "events.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE plate_events (
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
                    """
                    INSERT INTO plate_events
                        (camera_id, camera_name, plate, confidence, detected_at, snapshot_path)
                    VALUES ('old-cam', 'Old Camera', '59X312345', 0.8,
                            '2026-07-13T08:00:00+00:00', '')
                    """
                )
            store = EventStore(Path(directory))
            events = store.latest()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].direction, "IN")
            self.assertIsNone(events[0].visit_id)

    def test_clear_saved_plates_keeps_camera_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            camera = CameraConfig("gate-in", "Gate IN", "0", direction="IN")
            store.sync_cameras([camera])
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            store.record("gate-in", "Gate IN", "59X312345", 0.9, frame, direction="IN")

            events, visits, snapshots = store.clear_saved_plates()

            self.assertEqual((events, visits, snapshots), (1, 1, 1))
            self.assertEqual(store.latest(), [])
            self.assertEqual(store.latest_visits(), [])
            with store._connect() as connection:
                camera_count = connection.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
            self.assertEqual(camera_count, 1)


class VehicleRegistryAndGateTests(unittest.TestCase):
    def _store(self, directory, **kwargs):
        return EventStore(Path(directory), **kwargs)

    def test_register_find_and_remove_vehicle(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.upsert_vehicle(RegisteredVehicle("59-X3 123.45", owner_name="An"))
            found = store.find_vehicle("59X312345")
            self.assertIsNotNone(found)
            self.assertEqual(found.owner_name, "An")
            self.assertEqual(len(store.list_vehicles()), 1)
            self.assertEqual(store.remove_vehicle("59X312345"), 1)
            self.assertIsNone(store.find_vehicle("59X312345"))

    def test_registered_event_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.upsert_vehicle(RegisteredVehicle("59X312345", owner_name="An"))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            event = store.record("gate-in", "IN", "59X312345", 0.9, frame, direction="IN")
            self.assertEqual(event.access_status, ALLOW)

    def test_blacklisted_event_is_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.upsert_vehicle(RegisteredVehicle("59X312345", access=DENY))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            event = store.record("gate-in", "IN", "59X312345", 0.9, frame, direction="IN")
            self.assertEqual(event.access_status, DENY)

    def test_unknown_plate_denied_in_registered_only_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory, policy=POLICY_REGISTERED_ONLY)
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            event = store.record("gate-in", "IN", "99Z999999", 0.9, frame, direction="IN")
            self.assertEqual(event.access_status, DENY)

    def test_completed_visit_gets_fee(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory, tariff=Tariff(flat_fee=5000))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            entry_at = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
            store.record("in", "IN", "59X312345", 0.9, frame, entry_at, direction="IN")
            store.record(
                "out", "OUT", "59X312345", 0.9, frame,
                entry_at + timedelta(minutes=30), direction="OUT",
            )
            visit = store.latest_visits()[0]
            self.assertEqual(visit.status, "COMPLETED")
            self.assertEqual(visit.fee, 5000)

    def test_guest_visit_unpaid_then_mark_paid_counts_revenue(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory, tariff=Tariff(flat_fee=5000))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            entry_at = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
            store.record("in", "IN", "43K199999", 0.9, frame, entry_at, direction="IN")
            store.record(
                "out", "OUT", "43K199999", 0.9, frame,
                entry_at + timedelta(minutes=15), direction="OUT",
            )
            visit = store.latest_visits()[0]
            self.assertEqual(visit.payment_status, "UNPAID")
            self.assertEqual(store.revenue_summary()["unpaid_total"], 5000)

            self.assertEqual(store.mark_paid(visit.id, "QR"), 1)
            summary = store.revenue_summary()
            self.assertEqual(summary["paid_total"], 5000)
            self.assertEqual(summary["paid_count"], 1)
            self.assertEqual(summary["unpaid_total"], 0)

    def test_registered_subscriber_visit_is_exempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory, tariff=Tariff(flat_fee=5000))
            store.upsert_vehicle(RegisteredVehicle("59X312345", owner_name="An"))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            entry_at = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
            store.record("in", "IN", "59X312345", 0.9, frame, entry_at, direction="IN")
            store.record(
                "out", "OUT", "59X312345", 0.9, frame,
                entry_at + timedelta(hours=3), direction="OUT",
            )
            visit = store.latest_visits()[0]
            self.assertEqual(visit.payment_status, "EXEMPT")

    def test_simulated_gate_opens_then_auto_closes(self):
        clock = {"t": 100.0}
        gate = SimulatedGate(open_seconds=4.0, clock=lambda: clock["t"])
        self.assertFalse(gate.is_open())
        gate.open("registered", plate="59X312345")
        self.assertTrue(gate.is_open())
        clock["t"] = 103.9
        self.assertTrue(gate.is_open())
        clock["t"] = 104.1
        self.assertFalse(gate.is_open())


class PaymentTests(unittest.TestCase):
    account = BankAccount(bank_bin="970415", account_number="0123456789", account_name="BAI XE")

    def test_payload_has_valid_crc_and_amount(self):
        payload = build_vietqr(self.account, amount=5000, description="Ve xe")
        # CRC recomputed over everything up to the 4-hex checksum must match
        self.assertEqual(f"{crc16_ccitt(payload[:-4]):04X}", payload[-4:])
        self.assertIn("970415", payload)
        self.assertIn("0123456789", payload)
        self.assertIn("54045000", payload)  # tag 54 len 04 value 5000
        self.assertTrue(payload.startswith("000201"))

    def test_static_qr_when_no_amount(self):
        payload = build_vietqr(self.account)
        self.assertIn("010211", payload)  # point of initiation = static
        self.assertNotIn("5404", payload)


class AuthTests(unittest.TestCase):
    def test_hash_and_verify_roundtrip(self):
        encoded = hash_password("secret")
        self.assertTrue(verify_password("secret", encoded))
        self.assertFalse(verify_password("wrong", encoded))

    def test_malformed_hash_is_rejected(self):
        self.assertFalse(verify_password("x", "not-a-valid-hash"))


class HardwareGateTests(unittest.TestCase):
    def test_tcp_relay_sends_command_and_swallows_errors(self):
        sent = []
        gate = TcpRelayGate("10.0.0.1", 8000, command=b"OPEN\n", sender=sent.append)
        gate.open("test")
        self.assertEqual(sent, [b"OPEN\n"])

        def boom(_):
            raise OSError("no route")

        broken = TcpRelayGate("10.0.0.1", 8000, sender=boom)
        broken.open()  # must not raise
        self.assertIn("no route", broken.last_error)

    def test_composite_fans_out_and_delegates_state(self):
        clock = {"t": 0.0}
        primary = SimulatedGate(open_seconds=4.0, clock=lambda: clock["t"])
        sent = []
        gate = CompositeGate(primary, [TcpRelayGate("h", 1, sender=sent.append)])
        gate.open("registered", plate="59X312345")
        self.assertTrue(gate.is_open())
        self.assertEqual(gate.last_plate, "59X312345")
        self.assertEqual(sent, [b"OPEN\n"])

    def test_build_gate_selects_backend(self):
        from plate_app.config import AppConfig

        self.assertIsInstance(build_gate(AppConfig()), SimulatedGate)
        tcp_cfg = AppConfig(gate_backend="tcp", gate_host="192.168.1.9")
        self.assertIsInstance(build_gate(tcp_cfg), CompositeGate)


class UsersAndReportingTests(unittest.TestCase):
    def test_default_admin_seeded_and_login(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            user = store.verify_login("admin", "admin")
            self.assertIsNotNone(user)
            self.assertEqual(user.role, ROLE_ADMIN)
            self.assertIsNone(store.verify_login("admin", "wrong"))

    def test_cannot_delete_last_admin(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            with self.assertRaises(ValueError):
                store.delete_user("admin")

    def test_create_operator_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.create_user("guard", "pw123", role="operator")
            self.assertIsNotNone(store.verify_login("guard", "pw123"))
            store.write_audit("guard", "GATE_OPEN", "59X312345")
            self.assertEqual(store.list_audit()[0]["action"], "GATE_OPEN")

    def test_revenue_by_day_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory), tariff=Tariff(flat_fee=5000))
            frame = np.zeros((20, 30, 3), dtype=np.uint8)
            entry = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
            store.record("in", "IN", "43K199999", 0.9, frame, entry, direction="IN")
            out = store.record(
                "out", "OUT", "43K199999", 0.9, frame,
                entry + timedelta(minutes=20), direction="OUT",
            )
            store.mark_paid(out.visit_id, "CASH")
            report = store.revenue_by_day()
            self.assertEqual(report[0]["paid_total"], 5000)
            backup = store.backup_database()
            self.assertTrue(backup.exists())


class CameraStreamTests(unittest.TestCase):
    def test_latest_if_new_does_not_copy_same_frame_twice(self):
        stream = CameraStream(CameraConfig("cam-1", "Gate", "0"))
        with stream._lock:
            stream._frame = np.full((2, 3, 3), 7, dtype=np.uint8)
            stream._sequence = 1
            stream._status = "connected"

        sequence, frame, status = stream.latest_if_new(0)
        self.assertEqual((sequence, status), (1, "connected"))
        self.assertIsNotNone(frame)
        frame[0, 0, 0] = 99
        self.assertEqual(stream._frame[0, 0, 0], 7)

        sequence, frame, status = stream.latest_if_new(1)
        self.assertEqual((sequence, status), (1, "connected"))
        self.assertIsNone(frame)

    def test_start_delay_can_be_interrupted_without_opening_source(self):
        stream = CameraStream(
            CameraConfig(
                "cam-delay",
                "Delayed",
                "missing-video.mp4",
                start_delay_seconds=15,
            )
        )
        stream.start()
        deadline = time.monotonic() + 1
        while stream.status == "stopped" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(stream.status, "waiting 15s")
        started_at = time.monotonic()
        stream.stop()
        self.assertLess(time.monotonic() - started_at, 1)
        self.assertEqual(stream.status, "stopped")


if __name__ == "__main__":
    unittest.main()
