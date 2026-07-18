from __future__ import annotations

import argparse
import math
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from .config import AppConfig, CameraConfig, load_config, save_config
from .recognition import (
    Detection,
    MultiCameraProcessor,
    ProcessedFrame,
    ProcessorStatus,
    is_plausible_vietnamese_plate,
)
from .storage import EventStore
from .video import CameraStream


class PlateApp(tk.Tk):
    def __init__(self, config_path: Path, cli_sources: list[str] | None = None):
        super().__init__()
        self.title("OCR Plate - Motorcycle Gate")
        self.geometry("1450x900")
        self.minsize(1100, 700)
        self.config_path = config_path
        self.app_config = load_config(config_path)
        if cli_sources:
            self.app_config.cameras = [
                CameraConfig(id=f"cam-{index}", name=f"Camera {index}", uri=source)
                for index, source in enumerate(cli_sources, start=1)
            ]
        self.event_store = EventStore(Path(self.app_config.data_dir))
        self.streams: dict[str, tuple[CameraConfig, CameraStream]] = {}
        self.processor: MultiCameraProcessor | None = None
        self.output_queue: Queue = Queue(maxsize=30)
        self.camera_labels: dict[str, ttk.Label] = {}
        self.camera_images: dict[str, ImageTk.PhotoImage] = {}
        self._preview_sequences: dict[str, int] = {}
        self._latest_detections: dict[str, tuple[float, tuple[Detection, ...]]] = {}
        self._streams_lock = threading.Lock()
        self._running = False

        self._build_ui()
        self.event_store.sync_cameras(self.app_config.cameras)
        self._refresh_source_tree()
        self._load_latest_events()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_output)
        self.after(50, self._poll_previews)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill=tk.X)

        ttk.Button(toolbar, text="Add video", command=self._add_video).pack(side=tk.LEFT, padx=3)
        sample_paths = sorted(Path("sample_videos").glob("*.mp4"))
        self.sample_lookup = {path.name: path for path in sample_paths}
        self.sample_var = tk.StringVar()
        self.sample_combo = ttk.Combobox(
            toolbar,
            textvariable=self.sample_var,
            values=list(self.sample_lookup),
            width=22,
            state="readonly",
        )
        self.sample_combo.pack(side=tk.LEFT, padx=(8, 3))
        if sample_paths:
            current_uri = self.app_config.cameras[0].uri if self.app_config.cameras else ""
            current_name = Path(current_uri).name
            self.sample_var.set(current_name if current_name in self.sample_lookup else sample_paths[0].name)
        self.sample_direction_var = tk.StringVar(value="IN")
        self.sample_direction_combo = ttk.Combobox(
            toolbar,
            textvariable=self.sample_direction_var,
            values=("IN", "OUT"),
            width=4,
            state="readonly",
        )
        self.sample_direction_combo.pack(side=tk.LEFT, padx=2)
        self.sample_direction_combo.bind(
            "<<ComboboxSelected>>",
            self._sample_direction_changed,
        )
        ttk.Label(toolbar, text="Delay").pack(side=tk.LEFT, padx=(3, 1))
        self.sample_delay_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(
            toolbar,
            from_=0,
            to=300,
            increment=1,
            width=4,
            textvariable=self.sample_delay_var,
        ).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Replace", command=self._use_sample).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Add sample", command=self._add_sample).pack(side=tk.LEFT, padx=2)
        self.source_var = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.source_var, width=27).pack(side=tk.LEFT, padx=(8, 3))
        ttk.Button(toolbar, text="Add camera/RTSP", command=self._add_uri).pack(side=tk.LEFT, padx=3)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        self.start_button = ttk.Button(toolbar, text="Start", command=self.start)
        self.start_button.pack(side=tk.LEFT, padx=3)
        self.stop_button = ttk.Button(toolbar, text="Stop", command=self.stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=3)

        ttk.Label(toolbar, text="ROI width %").pack(side=tk.LEFT, padx=(15, 3))
        self.roi_width_var = tk.IntVar(value=int(self.app_config.roi_width * 100))
        ttk.Spinbox(toolbar, from_=20, to=100, width=5, textvariable=self.roi_width_var).pack(side=tk.LEFT)
        ttk.Label(toolbar, text="height %").pack(side=tk.LEFT, padx=(8, 3))
        self.roi_height_var = tk.IntVar(value=int(self.app_config.roi_height * 100))
        ttk.Spinbox(toolbar, from_=20, to=100, width=5, textvariable=self.roi_height_var).pack(side=tk.LEFT)
        ttk.Label(toolbar, text="Detect (s)").pack(side=tk.LEFT, padx=(10, 3))
        self.detection_interval_var = tk.DoubleVar(value=self.app_config.detection_interval_seconds)
        ttk.Spinbox(
            toolbar,
            from_=0.1,
            to=5.0,
            increment=0.1,
            width=5,
            textvariable=self.detection_interval_var,
        ).pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side=tk.RIGHT, padx=8)

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        sidebar = ttk.Frame(paned, width=360)
        display = ttk.Frame(paned)
        paned.add(sidebar, weight=1)
        paned.add(display, weight=4)

        ttk.Label(sidebar, text="Sources", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))
        self.source_tree = ttk.Treeview(
            sidebar,
            columns=("direction", "delay", "name", "uri"),
            show="headings",
            height=7,
        )
        self.source_tree.heading("direction", text="Way")
        self.source_tree.heading("delay", text="Delay")
        self.source_tree.heading("name", text="Name")
        self.source_tree.heading("uri", text="Source")
        self.source_tree.column("direction", width=45, anchor=tk.CENTER)
        self.source_tree.column("delay", width=45, anchor=tk.CENTER)
        self.source_tree.column("name", width=80)
        self.source_tree.column("uri", width=160)
        self.source_tree.pack(fill=tk.X)
        source_actions = ttk.Frame(sidebar)
        source_actions.pack(fill=tk.X, pady=5)
        ttk.Button(source_actions, text="Remove", command=self._remove_selected).pack(side=tk.LEFT)
        ttk.Label(source_actions, text="Direction").pack(side=tk.LEFT, padx=(10, 3))
        self.camera_direction_var = tk.StringVar(value="IN")
        ttk.Combobox(
            source_actions,
            textvariable=self.camera_direction_var,
            values=("IN", "OUT"),
            state="readonly",
            width=5,
        ).pack(side=tk.LEFT)
        ttk.Label(source_actions, text="Delay").pack(side=tk.LEFT, padx=(5, 2))
        self.camera_delay_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(
            source_actions,
            from_=0,
            to=300,
            increment=1,
            width=4,
            textvariable=self.camera_delay_var,
        ).pack(side=tk.LEFT)
        ttk.Button(source_actions, text="Apply", command=self._set_selected_direction).pack(
            side=tk.LEFT, padx=3
        )
        self.source_tree.bind("<<TreeviewSelect>>", self._source_selected)

        model_actions = ttk.Frame(sidebar)
        model_actions.pack(fill=tk.X, pady=(1, 5))
        ttk.Label(model_actions, text="OCR model").pack(side=tk.LEFT)
        self.ocr_model_var = tk.StringVar(value=self.app_config.ocr_recognition_model)
        ocr_models = ["PP-OCRv6_medium_rec", "PP-OCRv6_tiny_rec"]
        if self.app_config.ocr_recognition_model not in ocr_models:
            ocr_models.append(self.app_config.ocr_recognition_model)
        ttk.Combobox(
            model_actions,
            textvariable=self.ocr_model_var,
            values=ocr_models,
            state="readonly",
            width=23,
        ).pack(side=tk.LEFT, padx=(5, 3))
        ttk.Button(model_actions, text="Apply", command=self._apply_ocr_model).pack(
            side=tk.LEFT
        )

        notebook = ttk.Notebook(sidebar)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        event_tab = ttk.Frame(notebook, padding=4)
        inside_tab = ttk.Frame(notebook, padding=4)
        visits_tab = ttk.Frame(notebook, padding=4)
        notebook.add(event_tab, text="Events")
        notebook.add(inside_tab, text="Inside")
        notebook.add(visits_tab, text="Visits")

        event_header = ttk.Frame(event_tab)
        event_header.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(
            event_header,
            text="Clear saved plates",
            command=self._clear_saved_plates,
        ).pack(side=tk.LEFT)
        ttk.Button(event_header, text="Export CSV", command=self._export_csv).pack(side=tk.RIGHT)
        self.event_tree = ttk.Treeview(
            event_tab,
            columns=("time", "direction", "camera", "plate", "score"),
            show="headings",
            height=20,
        )
        for column, label, width in (
            ("time", "Time", 65),
            ("direction", "Way", 38),
            ("camera", "Camera", 65),
            ("plate", "Plate", 80),
            ("score", "%", 40),
        ):
            self.event_tree.heading(column, text=label)
            self.event_tree.column(column, width=width, anchor=tk.CENTER)
        self.event_tree.pack(fill=tk.BOTH, expand=True)

        self.inside_tree = ttk.Treeview(
            inside_tab,
            columns=("plate", "entry", "camera"),
            show="headings",
            height=20,
        )
        for column, label, width in (
            ("plate", "Plate", 90),
            ("entry", "Entry", 125),
            ("camera", "Camera", 95),
        ):
            self.inside_tree.heading(column, text=label)
            self.inside_tree.column(column, width=width, anchor=tk.CENTER)
        self.inside_tree.pack(fill=tk.BOTH, expand=True)

        visit_header = ttk.Frame(visits_tab)
        visit_header.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(visit_header, text="Export CSV", command=self._export_visits_csv).pack(side=tk.RIGHT)
        self.visit_tree = ttk.Treeview(
            visits_tab,
            columns=("plate", "entry", "exit", "status", "duration"),
            show="headings",
            height=20,
        )
        for column, label, width in (
            ("plate", "Plate", 80),
            ("entry", "In", 60),
            ("exit", "Out", 60),
            ("status", "Status", 70),
            ("duration", "Duration", 65),
        ):
            self.visit_tree.heading(column, text=label)
            self.visit_tree.column(column, width=width, anchor=tk.CENTER)
        self.visit_tree.pack(fill=tk.BOTH, expand=True)

        self.display_frame = display
        ttk.Label(display, text="Camera view", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))
        self.camera_grid = ttk.Frame(display)
        self.camera_grid.pack(fill=tk.BOTH, expand=True)
        self._rebuild_camera_grid()

    def _add_video(self) -> None:
        sample_dir = Path("sample_videos")
        paths = filedialog.askopenfilenames(
            title="Choose video",
            initialdir=str(sample_dir.resolve()) if sample_dir.exists() else None,
            filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.m4v"), ("All files", "*.*")],
        )
        for path in paths:
            self._append_camera(
                path,
                Path(path).stem,
                direction=self.sample_direction_var.get(),
                start_delay_seconds=self.sample_delay_var.get(),
            )

    def _sample_direction_changed(self, _event=None) -> None:
        self.sample_delay_var.set(15.0 if self.sample_direction_var.get() == "OUT" else 0.0)

    def _selected_sample(self) -> Path | None:
        selected = self.sample_var.get()
        path = self.sample_lookup.get(selected)
        if path is None:
            messagebox.showinfo("Sample", "No sample video is available.")
        return path

    def _use_sample(self) -> None:
        path = self._selected_sample()
        if path is None:
            return
        if self._running:
            self.stop()
        camera = CameraConfig(
            id=f"sample-{uuid.uuid4().hex[:8]}",
            name=path.stem.replace("_", " ").title(),
            uri=path.as_posix(),
            enabled=True,
            loop_video=True,
            direction=self.sample_direction_var.get(),
            start_delay_seconds=self.sample_delay_var.get(),
        )
        self.app_config.cameras = [camera]
        self._save_settings()
        self._refresh_source_tree()
        self._rebuild_camera_grid()
        self.status_var.set(
            f"Replaced sources with {camera.direction} sample: {path.name}"
        )

    def _add_sample(self) -> None:
        path = self._selected_sample()
        if path is None:
            return
        self._append_camera(
            path.as_posix(),
            path.stem.replace("_", " ").title(),
            direction=self.sample_direction_var.get(),
            loop_video=True,
            start_delay_seconds=self.sample_delay_var.get(),
        )
        self.status_var.set(
            f"Added {self.sample_direction_var.get()} sample: {path.name}"
        )

    def _add_uri(self) -> None:
        uri = self.source_var.get().strip()
        if not uri:
            messagebox.showinfo("Source", "Enter 0, 1, an RTSP URL, or a video path.")
            return
        self._append_camera(
            uri,
            f"Camera {len(self.app_config.cameras) + 1}",
            direction=self.sample_direction_var.get(),
            start_delay_seconds=self.sample_delay_var.get(),
        )
        self.source_var.set("")

    def _append_camera(
        self,
        uri: str,
        name: str,
        direction: str = "IN",
        loop_video: bool = False,
        start_delay_seconds: float = 0.0,
    ) -> None:
        camera = CameraConfig(
            id=f"cam-{uuid.uuid4().hex[:8]}",
            name=name,
            uri=uri,
            loop_video=loop_video,
            direction=direction,
            start_delay_seconds=start_delay_seconds,
        )
        self.app_config.cameras.append(camera)
        self._save_settings()
        self._refresh_source_tree()
        self._rebuild_camera_grid()
        if self._running:
            self._start_stream(camera)

    def _remove_selected(self) -> None:
        selected = self.source_tree.selection()
        if not selected:
            return
        ids = set(selected)
        with self._streams_lock:
            for camera_id in ids:
                pair = self.streams.pop(camera_id, None)
                if pair:
                    pair[1].stop()
        self.app_config.cameras = [camera for camera in self.app_config.cameras if camera.id not in ids]
        self._save_settings()
        self._refresh_source_tree()
        self._rebuild_camera_grid()

    def _source_selected(self, _event=None) -> None:
        selected = self.source_tree.selection()
        if not selected:
            return
        camera_id = selected[0]
        camera = next((item for item in self.app_config.cameras if item.id == camera_id), None)
        if camera is not None:
            self.camera_direction_var.set(camera.direction)
            self.camera_delay_var.set(camera.start_delay_seconds)

    def _set_selected_direction(self) -> None:
        selected = set(self.source_tree.selection())
        if not selected:
            messagebox.showinfo("Direction", "Select at least one camera first.")
            return
        direction = self.camera_direction_var.get().upper()
        start_delay = max(0.0, self.camera_delay_var.get())
        for camera in self.app_config.cameras:
            if camera.id in selected:
                camera.direction = direction
                camera.start_delay_seconds = start_delay
        self._save_settings()
        self._refresh_source_tree()
        self._rebuild_camera_grid()
        self.status_var.set(
            f"Updated selected camera to {direction}, delay {start_delay:g}s"
        )

    def _apply_ocr_model(self) -> None:
        model_name = self.ocr_model_var.get().strip()
        if not model_name or model_name == self.app_config.ocr_recognition_model:
            return
        was_running = self._running
        if was_running:
            self.stop()
        self.app_config.ocr_recognition_model = model_name
        self._save_settings()
        suffix = "; press Start to reload recognition" if was_running else ""
        self.status_var.set(f"OCR model: {model_name}{suffix}")

    def _refresh_source_tree(self) -> None:
        self.source_tree.delete(*self.source_tree.get_children())
        for camera in self.app_config.cameras:
            self.source_tree.insert(
                "",
                tk.END,
                iid=camera.id,
                values=(
                    camera.direction,
                    f"{camera.start_delay_seconds:g}s",
                    camera.name,
                    camera.uri,
                ),
            )

    def _rebuild_camera_grid(self) -> None:
        for child in self.camera_grid.winfo_children():
            child.destroy()
        self.camera_labels.clear()
        self.camera_images.clear()
        self._preview_sequences.clear()
        self._latest_detections.clear()
        cameras = self.app_config.cameras
        columns = 1 if len(cameras) <= 1 else 2
        for index, camera in enumerate(cameras):
            panel = ttk.LabelFrame(
                self.camera_grid,
                text=(
                    f"[{camera.direction} +{camera.start_delay_seconds:g}s] {camera.name}"
                    if camera.start_delay_seconds
                    else f"[{camera.direction}] {camera.name}"
                ),
                padding=4,
            )
            panel.grid(row=index // columns, column=index % columns, sticky="nsew", padx=4, pady=4)
            label = ttk.Label(panel, text=f"Not started\n{camera.uri}", anchor=tk.CENTER)
            label.pack(fill=tk.BOTH, expand=True)
            self.camera_labels[camera.id] = label
        rows = max(1, math.ceil(max(1, len(cameras)) / columns))
        for column in range(columns):
            self.camera_grid.columnconfigure(column, weight=1)
        for row in range(rows):
            self.camera_grid.rowconfigure(row, weight=1)
        if not cameras:
            ttk.Label(
                self.camera_grid,
                text="Add a video, webcam index (0), or RTSP camera to begin.",
                anchor=tk.CENTER,
            ).grid(row=0, column=0, sticky="nsew")

    def _save_settings(self) -> None:
        self.app_config.roi_width = max(0.2, min(1.0, self.roi_width_var.get() / 100))
        self.app_config.roi_height = max(0.2, min(1.0, self.roi_height_var.get() / 100))
        self.app_config.detection_interval_seconds = max(0.1, self.detection_interval_var.get())
        save_config(self.app_config, self.config_path)
        self.event_store.sync_cameras(self.app_config.cameras)

    def _start_stream(self, camera: CameraConfig) -> None:
        stream = CameraStream(camera)
        with self._streams_lock:
            self.streams[camera.id] = (camera, stream)
        stream.start()

    def _stream_snapshot(self) -> list[tuple[CameraConfig, CameraStream]]:
        with self._streams_lock:
            return list(self.streams.values())

    def start(self) -> None:
        if self._running:
            return
        if not self.app_config.cameras:
            messagebox.showinfo("No source", "Add at least one video or camera first.")
            return
        self._save_settings()
        self._preview_sequences.clear()
        self._latest_detections.clear()
        self._running = True
        for camera in self.app_config.cameras:
            if camera.enabled:
                self._start_stream(camera)
        self.processor = MultiCameraProcessor(
            self.app_config,
            self._stream_snapshot,
            self.event_store,
            self.output_queue,
        )
        self.processor.start()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("Starting...")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self.processor:
            self.processor.stop()
            self.processor = None
        with self._streams_lock:
            streams = list(self.streams.values())
            self.streams.clear()
        for _, stream in streams:
            stream.stop()
        self._preview_sequences.clear()
        self._latest_detections.clear()
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.status_var.set("Stopped")

    def _poll_output(self) -> None:
        try:
            while True:
                item = self.output_queue.get_nowait()
                if isinstance(item, ProcessorStatus):
                    self.status_var.set(item.message)
                    if item.is_error:
                        self.status_var.set(f"Error: {item.message}")
                elif isinstance(item, ProcessedFrame):
                    self._latest_detections[item.camera_id] = (
                        time.monotonic(),
                        item.detections,
                    )
                    for event in item.new_events:
                        self._insert_event(event, top=True)
                    if item.new_events:
                        self._refresh_visit_views()
        except Empty:
            pass
        self.after(50, self._poll_output)

    def _poll_previews(self) -> None:
        if self._running:
            for camera, stream in self._stream_snapshot():
                previous = self._preview_sequences.get(camera.id, -1)
                sequence, frame, status = stream.latest_if_new(previous)
                if frame is None:
                    label = self.camera_labels.get(camera.id)
                    if label is not None and previous < 0:
                        label.configure(text=f"{camera.name}\n{status}", image="")
                    continue
                self._preview_sequences[camera.id] = sequence
                self._draw_preview_overlay(camera.id, frame)
                self._show_frame(camera.id, frame)
        preview_fps = max(1, int(self.app_config.preview_fps))
        self.after(max(15, round(1000 / preview_fps)), self._poll_previews)

    def _draw_preview_overlay(self, camera_id: str, frame) -> None:
        height, width = frame.shape[:2]
        roi_width = int(width * min(max(self.app_config.roi_width, 0.1), 1.0))
        roi_height = int(height * min(max(self.app_config.roi_height, 0.1), 1.0))
        x1 = (width - roi_width) // 2
        y1 = (height - roi_height) // 2
        x2, y2 = x1 + roi_width, y1 + roi_height
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 255), 2)
        cv2.putText(frame, "ROI", (x1 + 8, y1 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 255), 2)

        latest = self._latest_detections.get(camera_id)
        if latest is None:
            return
        detected_at, detections = latest
        hold_seconds = max(0.75, self.app_config.detection_interval_seconds * 2.5)
        if time.monotonic() - detected_at > hold_seconds:
            self._latest_detections.pop(camera_id, None)
            return
        for detection in detections:
            color = (0, 200, 0) if is_plausible_vietnamese_plate(detection.plate) else (0, 128, 255)
            cv2.rectangle(frame, detection.box[:2], detection.box[2:], color, 2)
            label = f"{detection.plate or 'PLATE'} {detection.ocr_confidence:.0%}"
            cv2.putText(
                frame,
                label,
                (detection.box[0], max(20, detection.box[1] - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                color,
                2,
            )

    def _show_frame(self, camera_id: str, frame) -> None:
        label = self.camera_labels.get(camera_id)
        if label is None:
            return
        width = max(320, label.winfo_width() - 8)
        height = max(240, label.winfo_height() - 8)
        frame_height, frame_width = frame.shape[:2]
        scale = min(width / frame_width, height / frame_height)
        resized = cv2.resize(
            frame,
            (max(1, int(frame_width * scale)), max(1, int(frame_height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        image = Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))
        photo = ImageTk.PhotoImage(image=image)
        self.camera_images[camera_id] = photo
        label.configure(image=photo, text="")

    def _load_latest_events(self) -> None:
        for event in reversed(self.event_store.latest(100)):
            self._insert_event(event, top=True)
        self._refresh_visit_views()

    def _insert_event(self, event, top: bool) -> None:
        clock = event.detected_at[11:19] if len(event.detected_at) >= 19 else event.detected_at
        self.event_tree.insert(
            "",
            0 if top else tk.END,
            values=(clock, event.direction, event.camera_name, event.plate, f"{event.confidence:.0%}"),
        )
        children = self.event_tree.get_children()
        for item in children[100:]:
            self.event_tree.delete(item)

    @staticmethod
    def _clock(value: str | None) -> str:
        if not value:
            return "-"
        return value[11:19] if len(value) >= 19 else value

    @staticmethod
    def _duration(seconds: int | None) -> str:
        if seconds is None:
            return "-"
        hours, remainder = divmod(seconds, 3600)
        minutes, remaining_seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{remaining_seconds:02}"

    def _refresh_visit_views(self) -> None:
        self.inside_tree.delete(*self.inside_tree.get_children())
        for visit in self.event_store.latest_visits(limit=100, status="INSIDE"):
            self.inside_tree.insert(
                "",
                tk.END,
                values=(visit.plate, self._clock(visit.entry_at), visit.entry_camera_name or "-"),
            )

        self.visit_tree.delete(*self.visit_tree.get_children())
        for visit in self.event_store.latest_visits(limit=100):
            self.visit_tree.insert(
                "",
                tk.END,
                values=(
                    visit.plate,
                    self._clock(visit.entry_at),
                    self._clock(visit.exit_at),
                    visit.status,
                    self._duration(visit.duration_seconds),
                ),
            )

    def _export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export events",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            count = self.event_store.export_csv(Path(path))
            messagebox.showinfo("Export complete", f"Exported {count} events.")

    def _export_visits_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export visits",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            count = self.event_store.export_visits_csv(Path(path))
            messagebox.showinfo("Export complete", f"Exported {count} visits.")

    def _clear_saved_plates(self) -> None:
        confirmed = messagebox.askyesno(
            "Clear saved plates",
            "Delete all saved plate events, entry/exit visits, and snapshots?\n\n"
            "Camera settings will be kept. Recognition will stop before clearing.",
            icon="warning",
        )
        if not confirmed:
            return
        if self._running:
            self.stop()
        events, visits, snapshots = self.event_store.clear_saved_plates()
        self.event_tree.delete(*self.event_tree.get_children())
        self._refresh_visit_views()
        self.status_var.set(
            f"Cleared {events} events, {visits} visits, {snapshots} snapshots"
        )

    def _on_close(self) -> None:
        self._save_settings()
        self.stop()
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Motorcycle license plate recognition app")
    parser.add_argument("--config", default="config.json", help="Path to JSON configuration")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Video path, webcam index, or RTSP URL; may be repeated",
    )
    args = parser.parse_args()
    app = PlateApp(Path(args.config), args.source or None)
    app.mainloop()
