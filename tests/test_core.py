import tempfile
import unittest
from pathlib import Path

from plate_app.config import AppConfig, CameraConfig, load_config, save_config
from plate_app.recognition import (
    ConsensusTracker,
    boxes_match,
    is_plausible_vietnamese_plate,
    normalize_plate,
)
from plate_app.storage import EventStore


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
                cameras=[CameraConfig("cam-1", "Gate", "0")],
            )
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.cameras[0].uri, "0")
            self.assertEqual(loaded.detection_interval_seconds, 0.5)

    def test_empty_event_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            self.assertEqual(store.latest(), [])


if __name__ == "__main__":
    unittest.main()
