from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Callable

import cv2
import numpy as np

from .config import AppConfig, CameraConfig
from .storage import EventStore, PlateEvent
from .video import CameraStream


def normalize_plate(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


def is_plausible_vietnamese_plate(text: str) -> bool:
    plate = normalize_plate(text)
    if not 7 <= len(plate) <= 10:
        return False
    if not plate[:2].isdigit():
        return False
    return any(char.isalpha() for char in plate[2:5]) and sum(c.isdigit() for c in plate) >= 6


@dataclass(frozen=True)
class Detection:
    plate: str
    ocr_confidence: float
    detection_confidence: float
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class ProcessedFrame:
    camera_id: str
    camera_name: str
    frame: np.ndarray
    status: str
    detections: tuple[Detection, ...] = ()
    new_events: tuple[PlateEvent, ...] = ()


@dataclass(frozen=True)
class ProcessorStatus:
    message: str
    is_error: bool = False


@dataclass
class RecognizedTrack:
    box: tuple[int, int, int, int]
    plate: str
    confidence: float
    created_at: float
    missed_frames: int = 0


def boxes_match(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    """Matches a moving plate without requiring a heavyweight video tracker."""
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    intersection_width = max(0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    iou = intersection / max(1, area_a + area_b - intersection)
    if iou >= 0.15:
        return True

    area_ratio = min(area_a, area_b) / max(area_a, area_b)
    center_a = ((ax1 + ax2) / 2, (ay1 + ay2) / 2)
    center_b = ((bx1 + bx2) / 2, (by1 + by2) / 2)
    center_distance = ((center_a[0] - center_b[0]) ** 2 + (center_a[1] - center_b[1]) ** 2) ** 0.5
    largest_side = max(ax2 - ax1, ay2 - ay1, bx2 - bx1, by2 - by1, 1)
    return area_ratio >= 0.40 and center_distance <= largest_side * 1.25


class ConsensusTracker:
    def __init__(self, min_votes: int, window_seconds: float, cooldown_seconds: float):
        self.min_votes = max(1, min_votes)
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._observations: dict[str, deque[tuple[float, str, float]]] = defaultdict(deque)
        self._last_emitted: dict[tuple[str, str], float] = {}

    def observe(self, camera_id: str, plate: str, score: float, now: float) -> tuple[str, float] | None:
        observations = self._observations[camera_id]
        observations.append((now, plate, score))
        while observations and now - observations[0][0] > self.window_seconds:
            observations.popleft()

        matching = [(text, confidence) for _, text, confidence in observations if text == plate]
        if len(matching) < self.min_votes:
            return None
        key = (camera_id, plate)
        if now - self._last_emitted.get(key, float("-inf")) < self.cooldown_seconds:
            return None
        self._last_emitted[key] = now
        confidence = sum(item[1] for item in matching) / len(matching)
        return plate, confidence


class RecognitionEngine:
    def __init__(self, config: AppConfig, event_store: EventStore):
        from paddleocr import TextRecognition
        from ultralytics import YOLO

        model_path = Path(config.model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path.resolve()}")
        self.config = config
        self.event_store = event_store
        self.model = YOLO(str(model_path))
        # The detector already returns a tightly cropped plate, so direct text
        # recognition is much faster than running a second text detector.
        self.ocr = TextRecognition(model_name=config.ocr_recognition_model)
        self.consensus = ConsensusTracker(
            config.min_votes,
            config.vote_window_seconds,
            config.duplicate_cooldown_seconds,
        )
        self._recognized_tracks: dict[str, list[RecognizedTrack]] = defaultdict(list)

    def _roi(self, frame: np.ndarray) -> tuple[int, int, int, int]:
        height, width = frame.shape[:2]
        roi_width = int(width * min(max(self.config.roi_width, 0.1), 1.0))
        roi_height = int(height * min(max(self.config.roi_height, 0.1), 1.0))
        x1 = (width - roi_width) // 2
        y1 = (height - roi_height) // 2
        return x1, y1, x1 + roi_width, y1 + roi_height

    def _read_plate(self, crop: np.ndarray) -> tuple[str, float]:
        if crop.size == 0:
            return "", 0.0
        height, width = crop.shape[:2]
        inputs: list[np.ndarray]
        if width / max(1, height) < 2.2:
            # Vietnamese motorcycle plates normally have two rows. A small
            # overlap makes the split tolerant of imperfect detector boxes.
            middle = height // 2
            overlap = max(2, int(height * 0.06))
            inputs = [crop[: middle + overlap], crop[max(0, middle - overlap) :]]
        else:
            inputs = [crop]
        inputs = [
            cv2.resize(image, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            for image in inputs
            if image.size
        ]
        predictions = self.ocr.predict(input=inputs)
        candidates: list[tuple[str, float]] = []
        for prediction in predictions:
            data = getattr(prediction, "json", prediction)
            if callable(data):
                data = data()
            if not isinstance(data, dict):
                continue
            result = data.get("res", data)
            text = str(result.get("rec_text", ""))
            score = float(result.get("rec_score", 0.0))
            if text and score >= self.config.ocr_confidence:
                candidates.append((text, score))
        plate = normalize_plate("".join(text for text, _ in candidates))
        score = sum(value for _, value in candidates) / len(candidates) if candidates else 0.0
        return plate, score

    def process(self, camera: CameraConfig, frame: np.ndarray) -> ProcessedFrame:
        annotated = frame.copy()
        rx1, ry1, rx2, ry2 = self._roi(frame)
        cv2.rectangle(annotated, (rx1, ry1), (rx2, ry2), (0, 220, 255), 2)
        cv2.putText(annotated, "ROI", (rx1 + 8, ry1 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 255), 2)
        roi = frame[ry1:ry2, rx1:rx2]
        result = self.model.predict(
            source=roi,
            conf=self.config.detection_confidence,
            verbose=False,
        )[0]

        ranked = []
        center_x, center_y = roi.shape[1] / 2, roi.shape[0] / 2
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
            confidence = float(box.conf[0])
            distance = ((x1 + x2) / 2 - center_x) ** 2 + ((y1 + y2) / 2 - center_y) ** 2
            ranked.append((distance, -confidence, x1, y1, x2, y2, confidence))

        selected = sorted(ranked)[: self.config.max_plates_per_frame]
        detections: list[Detection] = []
        events: list[PlateEvent] = []
        now = time.monotonic()
        tracks = self._recognized_tracks[camera.id]
        matched_track_ids: set[int] = set()
        for _, _, x1, y1, x2, y2, detection_score in selected:
            padding = 5
            x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
            x2, y2 = min(roi.shape[1], x2 + padding), min(roi.shape[0], y2 + padding)
            full_box = (x1 + rx1, y1 + ry1, x2 + rx1, y2 + ry1)

            cached_track = next(
                (
                    track
                    for track in tracks
                    if id(track) not in matched_track_ids
                    and now - track.created_at <= self.config.recognized_cache_seconds
                    and boxes_match(track.box, full_box)
                ),
                None,
            )
            if cached_track is not None:
                matched_track_ids.add(id(cached_track))
                cached_track.box = full_box
                cached_track.missed_frames = 0
                detection = Detection(
                    cached_track.plate,
                    cached_track.confidence,
                    detection_score,
                    full_box,
                )
                detections.append(detection)
                self._draw_detection(annotated, detection, cached=True)
                continue

            plate, ocr_score = self._read_plate(roi[y1:y2, x1:x2])
            detection = Detection(plate, ocr_score, detection_score, full_box)
            detections.append(detection)
            self._draw_detection(annotated, detection, cached=False)
            if not is_plausible_vietnamese_plate(plate):
                continue
            same_plate_track = next(
                (
                    track
                    for track in tracks
                    if track.plate == plate
                    and now - track.created_at <= self.config.recognized_cache_seconds
                ),
                None,
            )
            if same_plate_track is not None:
                same_plate_track.box = full_box
                same_plate_track.missed_frames = 0
                matched_track_ids.add(id(same_plate_track))
                continue
            consensus = self.consensus.observe(camera.id, plate, ocr_score, now)
            if consensus:
                confirmed_plate, confirmed_score = consensus
                events.append(
                    self.event_store.record(
                        camera.id,
                        camera.name,
                        confirmed_plate,
                        confirmed_score,
                        annotated,
                        datetime.now().astimezone(),
                    )
                )
                new_track = RecognizedTrack(
                    box=full_box,
                    plate=confirmed_plate,
                    confidence=confirmed_score,
                    created_at=now,
                )
                tracks.append(new_track)
                matched_track_ids.add(id(new_track))

        for track in tracks:
            if id(track) not in matched_track_ids:
                track.missed_frames += 1
        self._recognized_tracks[camera.id] = [
            track
            for track in tracks
            if track.missed_frames <= self.config.track_max_missed_frames
            and now - track.created_at <= self.config.recognized_cache_seconds
        ]

        return ProcessedFrame(
            camera_id=camera.id,
            camera_name=camera.name,
            frame=annotated,
            status="connected",
            detections=tuple(detections),
            new_events=tuple(events),
        )

    @staticmethod
    def _draw_detection(annotated: np.ndarray, detection: Detection, cached: bool) -> None:
        plausible = is_plausible_vietnamese_plate(detection.plate)
        color = (0, 200, 0) if plausible else (0, 128, 255)
        cv2.rectangle(annotated, detection.box[:2], detection.box[2:], color, 2)
        suffix = " CACHED" if cached else ""
        label = f"{detection.plate or 'UNKNOWN'} {detection.ocr_confidence:.0%}{suffix}"
        cv2.putText(
            annotated,
            label,
            (detection.box[0], max(20, detection.box[1] - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
        )


class MultiCameraProcessor:
    def __init__(
        self,
        config: AppConfig,
        streams_provider: Callable[[], list[tuple[CameraConfig, CameraStream]]],
        event_store: EventStore,
        output_queue: Queue,
    ):
        self.config = config
        self.streams_provider = streams_provider
        self.event_store = event_store
        self.output_queue = output_queue
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_sequences: dict[str, int] = {}
        self._last_processed_at: dict[str, float] = {}

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="recognition", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _put(self, item) -> None:
        try:
            self.output_queue.put_nowait(item)
        except Full:
            try:
                self.output_queue.get_nowait()
            except Empty:
                pass
            try:
                self.output_queue.put_nowait(item)
            except Full:
                pass

    def _run(self) -> None:
        try:
            self._put(ProcessorStatus("Loading detector and OCR..."))
            engine = RecognitionEngine(self.config, self.event_store)
            self._put(ProcessorStatus("Recognition ready"))
        except Exception as exc:
            self._put(ProcessorStatus(f"Cannot start recognition: {exc}", True))
            return

        while not self._stop.is_set():
            did_work = False
            for camera, stream in self.streams_provider():
                sequence, frame, status = stream.latest()
                if frame is None:
                    continue
                previous = self._last_sequences.get(camera.id, -1)
                if sequence == previous:
                    continue
                now = time.monotonic()
                interval = max(0.0, self.config.detection_interval_seconds)
                if interval > 0 and now - self._last_processed_at.get(camera.id, float("-inf")) < interval:
                    continue
                if interval <= 0 and sequence % max(1, self.config.frame_skip) != 0:
                    continue
                self._last_sequences[camera.id] = sequence
                self._last_processed_at[camera.id] = now
                did_work = True
                try:
                    processed = engine.process(camera, frame)
                    self._put(processed)
                except Exception as exc:
                    self._put(ProcessorStatus(f"{camera.name}: {exc}", True))
            if not did_work:
                self._stop.wait(0.02)
