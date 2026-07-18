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
    boxes_match,
    is_plausible_vietnamese_plate,
    normalize_plate,
)
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
                        direction="OUT",
                        start_delay_seconds=15,
                    )
                ],
            )
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.cameras[0].uri, "0")
            self.assertEqual(loaded.detection_interval_seconds, 0.5)
            self.assertEqual(loaded.preview_fps, 20)
            self.assertEqual(loaded.ocr_recognition_model, "PP-OCRv6_medium_rec")
            self.assertEqual(loaded.cameras[0].direction, "OUT")
            self.assertEqual(loaded.cameras[0].start_delay_seconds, 15)

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
