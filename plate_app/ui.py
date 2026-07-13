from __future__ import annotations

import argparse
import math
import threading
import uuid
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from .config import AppConfig, CameraConfig, load_config, save_config
from .recognition import MultiCameraProcessor, ProcessedFrame, ProcessorStatus
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
        self._streams_lock = threading.Lock()
        self._running = False

        self._build_ui()
        self._refresh_source_tree()
        self._load_latest_events()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_output)

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
            width=27,
            state="readonly",
        )
        self.sample_combo.pack(side=tk.LEFT, padx=(8, 3))
        if sample_paths:
            current_uri = self.app_config.cameras[0].uri if self.app_config.cameras else ""
            current_name = Path(current_uri).name
            self.sample_var.set(current_name if current_name in self.sample_lookup else sample_paths[0].name)
        ttk.Button(toolbar, text="Use sample", command=self._use_sample).pack(side=tk.LEFT, padx=3)
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
        self.source_tree = ttk.Treeview(sidebar, columns=("name", "uri"), show="headings", height=7)
        self.source_tree.heading("name", text="Name")
        self.source_tree.heading("uri", text="Source")
        self.source_tree.column("name", width=100)
        self.source_tree.column("uri", width=230)
        self.source_tree.pack(fill=tk.X)
        ttk.Button(sidebar, text="Remove selected", command=self._remove_selected).pack(anchor=tk.W, pady=5)

        event_header = ttk.Frame(sidebar)
        event_header.pack(fill=tk.X, pady=(14, 5))
        ttk.Label(event_header, text="Recent events", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(event_header, text="Export CSV", command=self._export_csv).pack(side=tk.RIGHT)
        self.event_tree = ttk.Treeview(
            sidebar,
            columns=("time", "camera", "plate", "score"),
            show="headings",
            height=20,
        )
        for column, label, width in (
            ("time", "Time", 85),
            ("camera", "Camera", 80),
            ("plate", "Plate", 90),
            ("score", "Score", 55),
        ):
            self.event_tree.heading(column, text=label)
            self.event_tree.column(column, width=width, anchor=tk.CENTER)
        self.event_tree.pack(fill=tk.BOTH, expand=True)

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
            self._append_camera(path, Path(path).stem)

    def _use_sample(self) -> None:
        selected = self.sample_var.get()
        path = self.sample_lookup.get(selected)
        if path is None:
            messagebox.showinfo("Sample", "No sample video is available.")
            return
        if self._running:
            self.stop()
        camera = CameraConfig(
            id=f"sample-{path.stem}",
            name=path.stem.replace("_", " ").title(),
            uri=path.as_posix(),
            enabled=True,
            loop_video=True,
        )
        self.app_config.cameras = [camera]
        self._save_settings()
        self._refresh_source_tree()
        self._rebuild_camera_grid()
        self.status_var.set(f"Selected sample: {path.name}")

    def _add_uri(self) -> None:
        uri = self.source_var.get().strip()
        if not uri:
            messagebox.showinfo("Source", "Enter 0, 1, an RTSP URL, or a video path.")
            return
        self._append_camera(uri, f"Camera {len(self.app_config.cameras) + 1}")
        self.source_var.set("")

    def _append_camera(self, uri: str, name: str) -> None:
        camera = CameraConfig(id=f"cam-{uuid.uuid4().hex[:8]}", name=name, uri=uri)
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

    def _refresh_source_tree(self) -> None:
        self.source_tree.delete(*self.source_tree.get_children())
        for camera in self.app_config.cameras:
            self.source_tree.insert("", tk.END, iid=camera.id, values=(camera.name, camera.uri))

    def _rebuild_camera_grid(self) -> None:
        for child in self.camera_grid.winfo_children():
            child.destroy()
        self.camera_labels.clear()
        self.camera_images.clear()
        cameras = self.app_config.cameras
        columns = 1 if len(cameras) <= 1 else 2
        for index, camera in enumerate(cameras):
            panel = ttk.LabelFrame(self.camera_grid, text=camera.name, padding=4)
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
                    self._show_frame(item)
                    for event in item.new_events:
                        self._insert_event(event, top=True)
        except Empty:
            pass
        self.after(50, self._poll_output)

    def _show_frame(self, item: ProcessedFrame) -> None:
        label = self.camera_labels.get(item.camera_id)
        if label is None:
            return
        width = max(320, label.winfo_width() - 8)
        height = max(240, label.winfo_height() - 8)
        frame_height, frame_width = item.frame.shape[:2]
        scale = min(width / frame_width, height / frame_height)
        resized = cv2.resize(
            item.frame,
            (max(1, int(frame_width * scale)), max(1, int(frame_height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        image = Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))
        photo = ImageTk.PhotoImage(image=image)
        self.camera_images[item.camera_id] = photo
        label.configure(image=photo, text="")

    def _load_latest_events(self) -> None:
        for event in reversed(self.event_store.latest(100)):
            self._insert_event(event, top=True)

    def _insert_event(self, event, top: bool) -> None:
        clock = event.detected_at[11:19] if len(event.detected_at) >= 19 else event.detected_at
        self.event_tree.insert(
            "",
            0 if top else tk.END,
            values=(clock, event.camera_name, event.plate, f"{event.confidence:.0%}"),
        )
        children = self.event_tree.get_children()
        for item in children[100:]:
            self.event_tree.delete(item)

    def _export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export events",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            count = self.event_store.export_csv(Path(path))
            messagebox.showinfo("Export complete", f"Exported {count} events.")

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
