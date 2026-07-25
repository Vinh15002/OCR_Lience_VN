from __future__ import annotations

import argparse
import math
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from .auth import ROLE_ADMIN
from .config import AppConfig, CameraConfig, load_config, save_config
from .gate import build_gate
from .parking import (
    ALLOW,
    DENY,
    GUEST,
    POLICY_ALL,
    POLICY_REGISTERED_ONLY,
    RegisteredVehicle,
)
from .payment import build_vietqr
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
        self.event_store = EventStore(
            Path(self.app_config.data_dir),
            policy=self.app_config.open_gate_policy,
            tariff=self.app_config.tariff(),
        )
        self.gate = build_gate(self.app_config)
        self._gate_denied_until = 0.0
        self._gate_denied_plate = ""
        self.current_user = None
        self._camera_seen: dict[str, float] = {}
        self.streams: dict[str, tuple[CameraConfig, CameraStream]] = {}
        self.processor: MultiCameraProcessor | None = None
        self.output_queue: Queue = Queue(maxsize=30)
        self.camera_labels: dict[str, ttk.Label] = {}
        self.camera_panels: dict[str, ttk.LabelFrame] = {}
        self.camera_images: dict[str, ImageTk.PhotoImage] = {}
        self._preview_sequences: dict[str, int] = {}
        self._latest_detections: dict[str, tuple[float, tuple[Detection, ...]]] = {}
        self._streams_lock = threading.Lock()
        self._running = False
        self._events_total = 0
        self._detect_times: deque[float] = deque(maxlen=90)

        self._build_ui()
        self.event_store.sync_cameras(self.app_config.cameras)
        self._refresh_source_tree()
        self._load_latest_events()
        self._events_total = self.event_store.count_events()
        self._update_stats()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_output)
        self.after(50, self._poll_previews)
        if self.app_config.require_login:
            self.after(120, self._prompt_login)
        if self.app_config.auto_start and self.app_config.cameras:
            self.after(900, self._maybe_auto_start)

    def _build_ui(self) -> None:
        self._setup_style()
        self.status_var = tk.StringVar(value="Ready")

        # --- Toolbar row 1: source management ---
        sources_bar = ttk.Frame(self, padding=(10, 8, 10, 3))
        sources_bar.pack(fill=tk.X)
        ttk.Label(sources_bar, text="SOURCES", style="Chip.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(sources_bar, text="Add video", command=self._add_video).pack(side=tk.LEFT, padx=3)
        sample_paths = sorted(Path("sample_videos").glob("*.mp4"))
        self.sample_lookup = {path.name: path for path in sample_paths}
        self.sample_var = tk.StringVar()
        self.sample_combo = ttk.Combobox(
            sources_bar,
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
            sources_bar,
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
        ttk.Label(sources_bar, text="Delay").pack(side=tk.LEFT, padx=(3, 1))
        self.sample_delay_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(
            sources_bar,
            from_=0,
            to=300,
            increment=1,
            width=4,
            textvariable=self.sample_delay_var,
        ).pack(side=tk.LEFT)
        ttk.Button(sources_bar, text="Replace", command=self._use_sample).pack(side=tk.LEFT, padx=2)
        ttk.Button(sources_bar, text="Add sample", command=self._add_sample).pack(side=tk.LEFT, padx=2)
        ttk.Separator(sources_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        self.source_var = tk.StringVar()
        ttk.Entry(sources_bar, textvariable=self.source_var, width=27).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(sources_bar, text="Add camera/RTSP", command=self._add_uri).pack(side=tk.LEFT, padx=3)

        # --- Toolbar row 2: run controls ---
        controls_bar = ttk.Frame(self, padding=(10, 3, 10, 8))
        controls_bar.pack(fill=tk.X)
        ttk.Label(controls_bar, text="RUN", style="Chip.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        self.start_button = ttk.Button(
            controls_bar, text="▶  Start", command=self.start, style="Accent.TButton"
        )
        self.start_button.pack(side=tk.LEFT, padx=3)
        self.stop_button = ttk.Button(
            controls_bar, text="■  Stop", command=self.stop, state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=3)
        ttk.Separator(controls_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12)

        ttk.Label(controls_bar, text="ROI width %").pack(side=tk.LEFT, padx=(0, 3))
        self.roi_width_var = tk.IntVar(value=int(self.app_config.roi_width * 100))
        ttk.Spinbox(controls_bar, from_=20, to=100, width=5, textvariable=self.roi_width_var).pack(side=tk.LEFT)
        ttk.Label(controls_bar, text="height %").pack(side=tk.LEFT, padx=(8, 3))
        self.roi_height_var = tk.IntVar(value=int(self.app_config.roi_height * 100))
        ttk.Spinbox(controls_bar, from_=20, to=100, width=5, textvariable=self.roi_height_var).pack(side=tk.LEFT)
        ttk.Label(controls_bar, text="Detect (s)").pack(side=tk.LEFT, padx=(12, 3))
        self.detection_interval_var = tk.DoubleVar(value=self.app_config.detection_interval_seconds)
        ttk.Spinbox(
            controls_bar,
            from_=0.1,
            to=5.0,
            increment=0.1,
            width=5,
            textvariable=self.detection_interval_var,
        ).pack(side=tk.LEFT)
        ttk.Label(controls_bar, text="Img size").pack(side=tk.LEFT, padx=(12, 3))
        self.detection_imgsz_var = tk.IntVar(value=self.app_config.detection_imgsz)
        ttk.Combobox(
            controls_bar,
            textvariable=self.detection_imgsz_var,
            values=(640, 768, 960, 1280),
            state="readonly",
            width=5,
        ).pack(side=tk.LEFT)

        ttk.Button(controls_bar, text="📊 Báo cáo", command=self._show_report).pack(side=tk.RIGHT, padx=3)
        ttk.Button(controls_bar, text="👤 Tài khoản", command=self._show_users).pack(side=tk.RIGHT, padx=3)
        ttk.Button(controls_bar, text="💾 Backup", command=self._backup_now).pack(side=tk.RIGHT, padx=3)
        self.user_label_var = tk.StringVar(value="")
        ttk.Label(controls_bar, textvariable=self.user_label_var).pack(side=tk.RIGHT, padx=8)

        # --- Bottom status bar (packed before the main area so it anchors) ---
        self._build_statusbar()

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        sidebar = ttk.Frame(paned, width=360)
        display = ttk.Frame(paned)
        paned.add(sidebar, weight=1)
        paned.add(display, weight=4)

        ttk.Label(sidebar, text="Sources", style="Heading.TLabel").pack(anchor=tk.W, pady=(0, 5))
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
        vehicles_tab = ttk.Frame(notebook, padding=4)
        notebook.add(event_tab, text="Events")
        notebook.add(inside_tab, text="Inside")
        notebook.add(visits_tab, text="Visits")
        notebook.add(vehicles_tab, text="Vehicles")

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
            columns=("time", "direction", "plate", "access", "score"),
            show="headings",
            height=20,
        )
        for column, label, width in (
            ("time", "Time", 60),
            ("direction", "Way", 38),
            ("plate", "Plate", 80),
            ("access", "Gate", 66),
            ("score", "%", 38),
        ):
            self.event_tree.heading(column, text=label)
            self.event_tree.column(column, width=width, anchor=tk.CENTER)
        self.event_tree.tag_configure("ALLOW", background=self._palette["row_in"])
        self.event_tree.tag_configure("GUEST", background=self._palette["row_out"])
        self.event_tree.tag_configure("DENY", background=self._palette["row_review"])
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
        self.inside_tree.tag_configure("IN", background=self._palette["row_in"])
        self.inside_tree.pack(fill=tk.BOTH, expand=True)

        visit_header = ttk.Frame(visits_tab)
        visit_header.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(visit_header, text="Tiền mặt", command=lambda: self._collect_payment("CASH")).pack(side=tk.LEFT)
        ttk.Button(visit_header, text="QR", command=lambda: self._collect_payment("QR")).pack(side=tk.LEFT, padx=3)
        ttk.Button(visit_header, text="CSV", command=self._export_visits_csv).pack(side=tk.RIGHT)
        self.revenue_var = tk.StringVar(value="Doanh thu hôm nay: 0")
        ttk.Label(visits_tab, textvariable=self.revenue_var, foreground="#1f6fe0").pack(anchor=tk.W, pady=(0, 4))
        self.visit_tree = ttk.Treeview(
            visits_tab,
            columns=("plate", "entry", "exit", "duration", "fee", "pay"),
            show="headings",
            height=20,
        )
        for column, label, width in (
            ("plate", "Plate", 74),
            ("entry", "In", 50),
            ("exit", "Out", 50),
            ("duration", "Time", 58),
            ("fee", "Phí", 54),
            ("pay", "TT", 46),
        ):
            self.visit_tree.heading(column, text=label)
            self.visit_tree.column(column, width=width, anchor=tk.CENTER)
        self.visit_tree.tag_configure("INSIDE", background=self._palette["row_in"])
        self.visit_tree.tag_configure("COMPLETED", background=self._palette["row_muted"])
        self.visit_tree.tag_configure("REVIEW", background=self._palette["row_review"])
        self.visit_tree.pack(fill=tk.BOTH, expand=True)

        self._build_vehicles_tab(vehicles_tab)

        self.display_frame = display
        ttk.Label(display, text="Camera view", style="Heading.TLabel").pack(anchor=tk.W, pady=(0, 5))
        self._build_gate_simulator(display)
        self.camera_grid = ttk.Frame(display)
        self.camera_grid.pack(fill=tk.BOTH, expand=True)
        self._rebuild_camera_grid()

    def _setup_style(self) -> None:
        self._palette = {
            "bg": "#eef1f5",
            "surface": "#ffffff",
            "text": "#1f2933",
            "muted": "#5b6b7b",
            "accent": "#2d7ff9",
            "accent_active": "#1f6fe0",
            "danger": "#e05260",
            "statusbar": "#1f2933",
            "status_sep": "#3e4c59",
            "status_text": "#cbd2d9",
            "ok": "#3ecf8e",
            "off": "#8a97a5",
            "row_in": "#e7f7ee",
            "row_out": "#e8f0fb",
            "row_review": "#fdeceb",
            "row_muted": "#f1f4f7",
        }
        palette = self._palette
        self.configure(bg=palette["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base_font = ("Segoe UI", 10)
        style.configure(".", font=base_font, background=palette["bg"], foreground=palette["text"])
        style.configure("TFrame", background=palette["bg"])
        style.configure("TLabel", background=palette["bg"], foreground=palette["text"])
        style.configure("TLabelframe", background=palette["bg"], bordercolor="#d5dbe2")
        style.configure(
            "TLabelframe.Label",
            background=palette["bg"],
            foreground=palette["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure("TButton", padding=(10, 5), relief="flat", background=palette["surface"])
        style.map(
            "TButton",
            background=[("active", "#e3e8ee"), ("disabled", "#dfe4ea")],
            foreground=[("disabled", "#9aa5b1")],
        )
        style.configure(
            "Accent.TButton",
            padding=(14, 6),
            font=("Segoe UI", 10, "bold"),
            background=palette["accent"],
            foreground="#ffffff",
        )
        style.map(
            "Accent.TButton",
            background=[("active", palette["accent_active"]), ("disabled", "#a9c4ef")],
            foreground=[("disabled", "#eef2f8")],
        )
        style.configure("Chip.TLabel", background="#dbe3ec", foreground=palette["muted"], font=("Segoe UI", 8, "bold"), padding=(6, 2))
        style.configure("Heading.TLabel", font=("Segoe UI", 12, "bold"), foreground=palette["text"])
        style.configure("TNotebook", background=palette["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 6), font=("Segoe UI", 9, "bold"))
        style.configure(
            "Treeview",
            background=palette["surface"],
            fieldbackground=palette["surface"],
            foreground=palette["text"],
            rowheight=26,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            background="#dbe3ec",
            foreground=palette["muted"],
            relief="flat",
            padding=4,
        )
        style.map("Treeview.Heading", background=[("active", "#cfd9e4")])
        style.map(
            "Treeview",
            background=[("selected", "#cfe0fb")],
            foreground=[("selected", palette["text"])],
        )

    def _build_statusbar(self) -> None:
        palette = self._palette
        bar = tk.Frame(self, bg=palette["statusbar"], height=30)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)

        self.state_dot = tk.Label(bar, text="●", bg=palette["statusbar"], fg=palette["off"], font=("Segoe UI", 11))
        self.state_dot.pack(side=tk.LEFT, padx=(12, 4))
        self.state_text = tk.Label(
            bar, text="Stopped", bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9, "bold")
        )
        self.state_text.pack(side=tk.LEFT)

        def separator() -> None:
            tk.Label(bar, text="│", bg=palette["statusbar"], fg=palette["status_sep"]).pack(side=tk.LEFT, padx=10)

        separator()
        self.cams_stat = tk.Label(bar, text="Cameras 0", bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9))
        self.cams_stat.pack(side=tk.LEFT)
        separator()
        self.events_stat = tk.Label(bar, text="Events 0", bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9))
        self.events_stat.pack(side=tk.LEFT)
        separator()
        self.fps_stat = tk.Label(bar, text="Detect 0.0/s", bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9))
        self.fps_stat.pack(side=tk.LEFT)
        separator()
        self.gate_stat = tk.Label(bar, text="⛔ Cổng đóng", bg=palette["statusbar"], fg=palette["off"], font=("Segoe UI", 9, "bold"))
        self.gate_stat.pack(side=tk.LEFT)

        tk.Label(
            bar, textvariable=self.status_var, bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9)
        ).pack(side=tk.RIGHT, padx=12)

    def _update_stats(self) -> None:
        palette = self._palette
        if self._running:
            self.state_dot.configure(fg=palette["ok"])
            self.state_text.configure(text="Running")
            active = len(self._stream_snapshot())
        else:
            self.state_dot.configure(fg=palette["off"])
            self.state_text.configure(text="Stopped")
            active = sum(1 for camera in self.app_config.cameras if camera.enabled)
        self.cams_stat.configure(text=f"Cameras {active}")
        self.events_stat.configure(text=f"Events {self._events_total}")
        now = time.monotonic()
        while self._detect_times and now - self._detect_times[0] > 5.0:
            self._detect_times.popleft()
        rate = len(self._detect_times) / 5.0
        self.fps_stat.configure(text=f"Detect {rate:.1f}/s")

        if self.gate.is_open(now):
            plate = f" · {self.gate.last_plate}" if self.gate.last_plate else ""
            self.gate_stat.configure(
                text=f"✅ Cổng MỞ {self.gate.seconds_left(now):.0f}s{plate}",
                fg=palette["ok"],
            )
        elif now < self._gate_denied_until:
            plate = f" · {self._gate_denied_plate}" if self._gate_denied_plate else ""
            self.gate_stat.configure(text=f"⛔ TỪ CHỐI{plate}", fg="#ff7a85")
        else:
            self.gate_stat.configure(text="⛔ Cổng đóng", fg=palette["off"])
        self._update_gate_simulator(now)

    def _build_gate_simulator(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Mô phỏng barrier", padding=4)
        panel.pack(fill=tk.X, pady=(0, 6))
        self.gate_canvas = tk.Canvas(panel, height=104, bg="#20303f", highlightthickness=0)
        self.gate_canvas.pack(fill=tk.X)
        self._arm_angle = 0.0  # 0 = closed (horizontal), 1 = fully open (up)

    def _update_gate_simulator(self, now: float) -> None:
        canvas = getattr(self, "gate_canvas", None)
        if canvas is None:
            return
        is_open = self.gate.is_open(now)
        denied = now < self._gate_denied_until
        target = 1.0 if is_open else 0.0
        self._arm_angle += (target - self._arm_angle) * 0.35
        if abs(target - self._arm_angle) < 0.01:
            self._arm_angle = target
        width = max(canvas.winfo_width(), 400)
        height = 104
        canvas.delete("all")
        # road + lane markings
        canvas.create_rectangle(0, height - 20, width, height, fill="#33475b", width=0)
        for x in range(20, width, 64):
            canvas.create_rectangle(x, height - 12, x + 32, height - 9, fill="#5b7085", width=0)
        # traffic light
        lx = width - 66
        canvas.create_rectangle(lx, 8, lx + 38, 92, fill="#0f171e", width=0)
        canvas.create_oval(lx + 7, 14, lx + 31, 38, fill="#ff4d5e" if not is_open else "#4a1f26", width=0)
        canvas.create_oval(lx + 7, 56, lx + 31, 80, fill="#3ecf8e" if is_open else "#1f3a2e", width=0)
        # barrier pole + arm
        pivot_x, pivot_y = 52, height - 20
        canvas.create_rectangle(pivot_x - 7, 24, pivot_x + 7, pivot_y, fill="#8a97a5", width=0)
        angle = math.radians(82 * self._arm_angle)
        arm_len = min(width - 150, 240)
        end_x = pivot_x + arm_len * math.cos(angle)
        end_y = pivot_y - arm_len * math.sin(angle)
        arm_color = "#3ecf8e" if is_open else ("#e6493a" if not denied else "#e6493a")
        canvas.create_line(pivot_x, pivot_y, end_x, end_y, fill=arm_color, width=12, capstyle=tk.ROUND)
        canvas.create_oval(pivot_x - 10, pivot_y - 10, pivot_x + 10, pivot_y + 10, fill="#cbd2d9", width=0)
        # status text
        if is_open:
            state, colour = "CỔNG MỞ", "#3ecf8e"
            plate = self.gate.last_plate
        elif denied:
            state, colour = "TỪ CHỐI", "#ff7a85"
            plate = self._gate_denied_plate
        else:
            state, colour = "CỔNG ĐÓNG", "#8a97a5"
            plate = ""
        canvas.create_text(
            70, 20, anchor="w", fill=colour, font=("Segoe UI", 13, "bold"),
            text=f"{state}   {plate}",
        )

    def _build_vehicles_tab(self, parent: ttk.Frame) -> None:
        rules = ttk.LabelFrame(parent, text="Chế độ cổng & phí gửi xe", padding=6)
        rules.pack(fill=tk.X)
        row1 = ttk.Frame(rules)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Chế độ").pack(side=tk.LEFT)
        self.policy_var = tk.StringVar(value=self.app_config.open_gate_policy)
        ttk.Combobox(
            row1,
            textvariable=self.policy_var,
            values=(POLICY_ALL, POLICY_REGISTERED_ONLY),
            state="readonly",
            width=16,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(row1, text="Apply", command=self._apply_gate_rules).pack(side=tk.RIGHT)
        row2 = ttk.Frame(rules)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="Phí/lượt").pack(side=tk.LEFT)
        self.flat_fee_var = tk.DoubleVar(value=self.app_config.parking_flat_fee)
        ttk.Spinbox(row2, from_=0, to=1_000_000, increment=1000, width=8, textvariable=self.flat_fee_var).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(row2, text="Phí/giờ").pack(side=tk.LEFT)
        self.hourly_fee_var = tk.DoubleVar(value=self.app_config.parking_hourly_fee)
        ttk.Spinbox(row2, from_=0, to=1_000_000, increment=1000, width=8, textvariable=self.hourly_fee_var).pack(side=tk.LEFT, padx=2)
        row3 = ttk.Frame(rules)
        row3.pack(fill=tk.X, pady=2)
        ttk.Label(row3, text="Miễn phí (phút)").pack(side=tk.LEFT)
        self.free_min_var = tk.IntVar(value=self.app_config.parking_free_minutes)
        ttk.Spinbox(row3, from_=0, to=1440, increment=5, width=6, textvariable=self.free_min_var).pack(side=tk.LEFT, padx=2)

        form = ttk.LabelFrame(parent, text="Đăng ký xe (whitelist / blacklist)", padding=6)
        form.pack(fill=tk.X, pady=(8, 0))
        f1 = ttk.Frame(form)
        f1.pack(fill=tk.X, pady=2)
        ttk.Label(f1, text="Biển").pack(side=tk.LEFT)
        self.veh_plate_var = tk.StringVar()
        ttk.Entry(f1, textvariable=self.veh_plate_var, width=12).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(f1, text="Chủ xe").pack(side=tk.LEFT)
        self.veh_owner_var = tk.StringVar()
        ttk.Entry(f1, textvariable=self.veh_owner_var, width=12).pack(side=tk.LEFT, padx=2)
        f2 = ttk.Frame(form)
        f2.pack(fill=tk.X, pady=2)
        ttk.Label(f2, text="Loại").pack(side=tk.LEFT)
        self.veh_access_var = tk.StringVar(value=ALLOW)
        ttk.Combobox(f2, textvariable=self.veh_access_var, values=(ALLOW, DENY), state="readonly", width=7).pack(side=tk.LEFT, padx=2)
        ttk.Button(f2, text="Lưu / Cập nhật", command=self._add_vehicle).pack(side=tk.LEFT, padx=6)
        ttk.Button(f2, text="Xóa", command=self._remove_vehicle_selected).pack(side=tk.RIGHT)

        self.vehicle_tree = ttk.Treeview(
            parent, columns=("plate", "owner", "access"), show="headings", height=12
        )
        for column, label, width in (("plate", "Biển", 90), ("owner", "Chủ xe", 120), ("access", "Loại", 70)):
            self.vehicle_tree.heading(column, text=label)
            self.vehicle_tree.column(column, width=width, anchor=tk.CENTER)
        self.vehicle_tree.tag_configure(ALLOW, background=self._palette["row_in"])
        self.vehicle_tree.tag_configure(DENY, background=self._palette["row_review"])
        self.vehicle_tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.vehicle_tree.bind("<<TreeviewSelect>>", self._vehicle_selected)
        self._refresh_vehicle_list()

    def _apply_gate_rules(self) -> None:
        self.app_config.open_gate_policy = self.policy_var.get()
        self.app_config.parking_flat_fee = max(0.0, self.flat_fee_var.get())
        self.app_config.parking_hourly_fee = max(0.0, self.hourly_fee_var.get())
        self.app_config.parking_free_minutes = max(0, int(self.free_min_var.get()))
        self.event_store.configure_rules(
            self.app_config.open_gate_policy, self.app_config.tariff()
        )
        save_config(self.app_config, self.config_path)
        mode = "Tính phí" if self.app_config.open_gate_policy == POLICY_ALL else "Whitelist"
        self.status_var.set(
            f"Áp dụng: {mode} · phí/lượt {self._money(self.app_config.parking_flat_fee)}"
        )

    def _add_vehicle(self) -> None:
        plate = self.veh_plate_var.get().strip()
        if not plate:
            messagebox.showinfo("Đăng ký xe", "Nhập biển số.")
            return
        try:
            self.event_store.upsert_vehicle(
                RegisteredVehicle(
                    plate=plate,
                    owner_name=self.veh_owner_var.get().strip(),
                    access=self.veh_access_var.get(),
                )
            )
        except ValueError as exc:
            messagebox.showerror("Đăng ký xe", str(exc))
            return
        self.veh_plate_var.set("")
        self.veh_owner_var.set("")
        self._refresh_vehicle_list()
        self.status_var.set(f"Đã lưu xe {plate}")

    def _remove_vehicle_selected(self) -> None:
        selected = self.vehicle_tree.selection()
        if not selected:
            return
        for plate in selected:
            self.event_store.remove_vehicle(plate)
        self._refresh_vehicle_list()

    def _vehicle_selected(self, _event=None) -> None:
        selected = self.vehicle_tree.selection()
        if not selected:
            return
        vehicle = self.event_store.find_vehicle(selected[0])
        if vehicle is not None:
            self.veh_plate_var.set(vehicle.plate)
            self.veh_owner_var.set(vehicle.owner_name)
            self.veh_access_var.set(vehicle.access)

    def _refresh_vehicle_list(self) -> None:
        self.vehicle_tree.delete(*self.vehicle_tree.get_children())
        for vehicle in self.event_store.list_vehicles():
            self.vehicle_tree.insert(
                "",
                tk.END,
                iid=vehicle.plate,
                values=(vehicle.plate, vehicle.owner_name or "-", vehicle.access),
                tags=(vehicle.access,),
            )

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

    @staticmethod
    def _panel_title(camera) -> str:
        if camera.start_delay_seconds:
            return f"[{camera.direction} +{camera.start_delay_seconds:g}s] {camera.name}"
        return f"[{camera.direction}] {camera.name}"

    def _rebuild_camera_grid(self) -> None:
        for child in self.camera_grid.winfo_children():
            child.destroy()
        self.camera_labels.clear()
        self.camera_panels.clear()
        self.camera_images.clear()
        self._preview_sequences.clear()
        self._latest_detections.clear()
        cameras = self.app_config.cameras
        columns = 1 if len(cameras) <= 1 else 2
        for index, camera in enumerate(cameras):
            panel = ttk.LabelFrame(
                self.camera_grid,
                text=self._panel_title(camera),
                padding=4,
            )
            panel.grid(row=index // columns, column=index % columns, sticky="nsew", padx=4, pady=4)
            # Keep the panel at its grid-assigned size so a large video frame
            # cannot stretch its cell and make the IN/OUT panels uneven.
            panel.pack_propagate(False)
            label = ttk.Label(panel, text=f"Not started\n{camera.uri}", anchor=tk.CENTER)
            label.pack(fill=tk.BOTH, expand=True)
            self.camera_labels[camera.id] = label
            self.camera_panels[camera.id] = panel
        rows = max(1, math.ceil(max(1, len(cameras)) / columns))
        # uniform groups force every column/row to the same size regardless of
        # the video resolution shown inside, so all camera panels stay equal.
        for column in range(columns):
            self.camera_grid.columnconfigure(column, weight=1, uniform="cam")
        for row in range(rows):
            self.camera_grid.rowconfigure(row, weight=1, uniform="cam")
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
        self.app_config.detection_imgsz = int(self.detection_imgsz_var.get())
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
        self._update_stats()

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
        self._detect_times.clear()
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.status_var.set("Stopped")
        self._update_stats()

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
                    self._detect_times.append(time.monotonic())
                    for event in item.new_events:
                        self._insert_event(event, top=True)
                        self._events_total += 1
                        self._handle_gate(event)
                    if item.new_events:
                        self._refresh_visit_views()
        except Empty:
            pass
        self._update_stats()
        self.after(50, self._poll_output)

    def _handle_gate(self, event) -> None:
        user = self.current_user.username if self.current_user else None
        if event.access_status in (ALLOW, GUEST):
            self.gate.open(event.access_reason, plate=event.plate)
            self.event_store.write_audit(user, "GATE_OPEN", f"{event.plate} {event.access_status}")
        elif event.access_status == DENY:
            self._gate_denied_until = time.monotonic() + 3.0
            self._gate_denied_plate = event.plate
            self.event_store.write_audit(user, "GATE_DENY", f"{event.plate} {event.access_reason}")
        self._update_stats()

    def _poll_previews(self) -> None:
        now = time.monotonic()
        if self._running:
            for camera, stream in self._stream_snapshot():
                previous = self._preview_sequences.get(camera.id, -1)
                sequence, frame, status = stream.latest_if_new(previous)
                if frame is None:
                    self._check_camera_alert(camera, status, now)
                    continue
                self._camera_seen[camera.id] = now
                panel = self.camera_panels.get(camera.id)
                if panel is not None and getattr(panel, "_alerting", False):
                    panel.configure(text=self._panel_title(camera))
                    panel._alerting = False
                self._preview_sequences[camera.id] = sequence
                self._draw_preview_overlay(camera.id, frame)
                self._show_frame(camera.id, frame)
        preview_fps = max(1, int(self.app_config.preview_fps))
        self.after(max(15, round(1000 / preview_fps)), self._poll_previews)

    def _check_camera_alert(self, camera, status: str, now: float) -> None:
        label = self.camera_labels.get(camera.id)
        if label is not None and self._preview_sequences.get(camera.id, -1) < 0:
            label.configure(text=f"{camera.name}\n{status}", image="")
        seen = self._camera_seen.get(camera.id)
        if seen is None or now - seen < self.app_config.camera_alert_seconds:
            return
        panel = self.camera_panels.get(camera.id)
        if panel is not None and not getattr(panel, "_alerting", False):
            panel.configure(text=f"⚠ MẤT KẾT NỐI · {camera.name}")
            panel._alerting = True

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

    _ACCESS_LABELS = {ALLOW: "MỞ", GUEST: "Khách", DENY: "TỪ CHỐI"}

    def _insert_event(self, event, top: bool) -> None:
        clock = event.detected_at[11:19] if len(event.detected_at) >= 19 else event.detected_at
        access = getattr(event, "access_status", "UNKNOWN")
        tag = access if access in (ALLOW, GUEST, DENY) else "GUEST"
        self.event_tree.insert(
            "",
            0 if top else tk.END,
            values=(
                clock,
                event.direction,
                event.plate,
                self._ACCESS_LABELS.get(access, access),
                f"{event.confidence:.0%}",
            ),
            tags=(tag,),
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
    def _money(amount: float | None) -> str:
        if not amount:
            return "-"
        return f"{int(round(amount)):,}".replace(",", ".")

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
                tags=("IN",),
            )

        self.visit_tree.delete(*self.visit_tree.get_children())
        for visit in self.event_store.latest_visits(limit=100):
            self.visit_tree.insert(
                "",
                tk.END,
                iid=str(visit.id),
                values=(
                    visit.plate,
                    self._clock(visit.entry_at),
                    self._clock(visit.exit_at),
                    self._duration(visit.duration_seconds),
                    self._money(visit.fee),
                    self._PAY_LABELS.get(visit.payment_status, "-") if visit.status == "COMPLETED" else "-",
                ),
                tags=(visit.status,),
            )
        self._refresh_revenue()

    _PAY_LABELS = {"PAID": "✓ Đã thu", "UNPAID": "Nợ", "EXEMPT": "Miễn"}

    def _collect_payment(self, method: str) -> None:
        selected = self.visit_tree.selection()
        if not selected:
            messagebox.showinfo("Thu tiền", "Chọn một lượt đã hoàn tất trong danh sách.")
            return
        visits = {str(visit.id): visit for visit in self.event_store.latest_visits(limit=500)}
        user = self.current_user.username if self.current_user else None
        if method == "QR":
            visit = visits.get(selected[0])
            if visit is None or visit.status != "COMPLETED":
                messagebox.showinfo("Thu tiền", "Chọn một lượt đã hoàn tất.")
                return
            self._show_qr_window(visit.id, visit.fee or 0, visit.plate)
            return
        paid = 0
        for iid in selected:
            paid += self.event_store.mark_paid(int(iid), method)
            visit = visits.get(iid)
            if visit is not None:
                self.event_store.write_audit(user, "PAYMENT_CASH", f"{visit.plate} {int(visit.fee or 0)}")
        self._refresh_visit_views()
        self.status_var.set(f"Đã thu {paid} lượt bằng tiền mặt")

    def _refresh_revenue(self) -> None:
        midnight = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        summary = self.event_store.revenue_summary(since=midnight.isoformat(timespec="seconds"))
        self.revenue_var.set(
            f"Doanh thu hôm nay: {self._money(summary['paid_total'])}đ  ·  "
            f"Chưa thu: {int(summary['unpaid_count'])} lượt "
            f"({self._money(summary['unpaid_total'])}đ)"
        )

    # --- Auth / admin ---

    def _require_admin(self, action: str = "thao tác này") -> bool:
        if self.current_user is None or self.current_user.role == ROLE_ADMIN:
            return True
        messagebox.showwarning("Không đủ quyền", f"Chỉ admin mới được {action}.")
        return False

    def _prompt_login(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Đăng nhập")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack()
        ttk.Label(frame, text="Đăng nhập hệ thống", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, pady=(0, 12))
        ttk.Label(frame, text="Tài khoản").grid(row=1, column=0, sticky=tk.W, pady=4)
        user_var = tk.StringVar(value="admin")
        ttk.Entry(frame, textvariable=user_var, width=22).grid(row=1, column=1, pady=4)
        ttk.Label(frame, text="Mật khẩu").grid(row=2, column=0, sticky=tk.W, pady=4)
        pw_var = tk.StringVar()
        pw_entry = ttk.Entry(frame, textvariable=pw_var, show="•", width=22)
        pw_entry.grid(row=2, column=1, pady=4)
        message = ttk.Label(frame, text="Mặc định: admin / admin", foreground="#5b6b7b")
        message.grid(row=3, column=0, columnspan=2, pady=(4, 0))

        def attempt(_event=None) -> None:
            user = self.event_store.verify_login(user_var.get(), pw_var.get())
            if user is None:
                message.configure(text="Sai tài khoản hoặc mật khẩu", foreground="#e05260")
                return
            self.current_user = user
            self.user_label_var.set(f"👤 {user.username} · {user.role}")
            self.event_store.write_audit(user.username, "LOGIN", "")
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()
            if self.app_config.require_login and self.current_user is None:
                self._on_close()

        ttk.Button(frame, text="Đăng nhập", command=attempt, style="Accent.TButton").grid(
            row=4, column=0, columnspan=2, pady=(12, 0), sticky="ew"
        )
        pw_entry.bind("<Return>", attempt)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        pw_entry.focus_set()

    def _maybe_auto_start(self) -> None:
        if not self._running and self.app_config.cameras:
            self.start()

    def _backup_now(self) -> None:
        if not self._require_admin("sao lưu dữ liệu"):
            return
        path = self.event_store.backup_database()
        user = self.current_user.username if self.current_user else None
        self.event_store.write_audit(user, "BACKUP", path.name)
        messagebox.showinfo("Backup", f"Đã sao lưu:\n{path}")
        self.status_var.set(f"Đã backup: {path.name}")

    def _show_users(self) -> None:
        if not self._require_admin("quản lý tài khoản"):
            return
        window = tk.Toplevel(self)
        window.title("Quản lý tài khoản")
        window.transient(self)
        window.grab_set()
        window.geometry("440x420")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=("user", "role", "active"), show="headings", height=11)
        for column, label, width in (("user", "Tài khoản", 160), ("role", "Vai trò", 90), ("active", "Hoạt động", 90)):
            tree.heading(column, text=label)
            tree.column(column, width=width, anchor=tk.CENTER)
        tree.pack(fill=tk.BOTH, expand=True)

        def refresh() -> None:
            tree.delete(*tree.get_children())
            for user in self.event_store.list_users():
                tree.insert("", tk.END, iid=user.username, values=(user.username, user.role, "✓" if user.active else "✗"))

        refresh()
        form = ttk.Frame(frame)
        form.pack(fill=tk.X, pady=8)
        ttk.Label(form, text="User").pack(side=tk.LEFT)
        user_var = tk.StringVar()
        ttk.Entry(form, textvariable=user_var, width=11).pack(side=tk.LEFT, padx=3)
        ttk.Label(form, text="Mật khẩu").pack(side=tk.LEFT)
        pw_var = tk.StringVar()
        ttk.Entry(form, textvariable=pw_var, width=11, show="•").pack(side=tk.LEFT, padx=3)
        role_var = tk.StringVar(value="operator")
        ttk.Combobox(form, textvariable=role_var, values=("operator", "admin"), state="readonly", width=8).pack(side=tk.LEFT, padx=3)

        def add() -> None:
            try:
                self.event_store.create_user(user_var.get(), pw_var.get(), role_var.get())
            except ValueError as exc:
                messagebox.showerror("Tài khoản", str(exc))
                return
            user_var.set("")
            pw_var.set("")
            refresh()

        def remove() -> None:
            selected = tree.selection()
            if not selected:
                return
            try:
                self.event_store.delete_user(selected[0])
            except ValueError as exc:
                messagebox.showerror("Tài khoản", str(exc))
                return
            refresh()

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="Thêm / Đổi mật khẩu", command=add).pack(side=tk.LEFT)
        ttk.Button(actions, text="Xóa", command=remove).pack(side=tk.LEFT, padx=4)

    # --- Reports & QR payment ---

    def _show_report(self) -> None:
        window = tk.Toplevel(self)
        window.title("Báo cáo doanh thu")
        window.geometry("580x560")
        window.transient(self)
        ttk.Label(window, text="Doanh thu theo ngày", style="Heading.TLabel").pack(anchor=tk.W, padx=10, pady=(10, 4))
        revenue_tree = ttk.Treeview(window, columns=("day", "paid", "count", "unpaid"), show="headings", height=11)
        for column, label, width in (("day", "Ngày", 120), ("paid", "Đã thu", 120), ("count", "Số lượt", 90), ("unpaid", "Chưa thu", 120)):
            revenue_tree.heading(column, text=label)
            revenue_tree.column(column, width=width, anchor=tk.CENTER)
        revenue_tree.pack(fill=tk.X, padx=10)
        total = 0.0
        for row in self.event_store.revenue_by_day(60):
            total += row["paid_total"] or 0
            revenue_tree.insert(
                "", tk.END,
                values=(row["day"], self._money(row["paid_total"]), int(row["paid_count"] or 0), self._money(row["unpaid_total"])),
            )
        ttk.Label(window, text=f"Tổng đã thu: {self._money(total)}đ", foreground="#1f6fe0").pack(anchor=tk.W, padx=10, pady=6)
        ttk.Label(window, text="Nhật ký hệ thống (audit)", style="Heading.TLabel").pack(anchor=tk.W, padx=10, pady=(6, 4))
        audit_tree = ttk.Treeview(window, columns=("ts", "user", "action", "detail"), show="headings", height=10)
        for column, label, width in (("ts", "Thời gian", 150), ("user", "User", 80), ("action", "Hành động", 110), ("detail", "Chi tiết", 150)):
            audit_tree.heading(column, text=label)
            audit_tree.column(column, width=width, anchor=tk.W)
        audit_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        for entry in self.event_store.list_audit(200):
            timestamp = (entry["ts"] or "")[:19].replace("T", " ")
            audit_tree.insert("", tk.END, values=(timestamp, entry["username"] or "-", entry["action"], entry["detail"] or ""))

    def _qr_image(self, payload: str):
        try:
            import qrcode

            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(payload)
            qr.make(fit=True)
            return qr.make_image(fill_color="black", back_color="white").convert("RGB")
        except Exception:
            return None

    def _show_qr_window(self, visit_id: int, amount: float, plate: str) -> None:
        bank = self.app_config.bank()
        window = tk.Toplevel(self)
        window.title("Thu tiền QR")
        window.transient(self)
        window.grab_set()
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=16)
        frame.pack()
        ttk.Label(frame, text=f"Phí gửi xe: {self._money(amount)}đ", style="Heading.TLabel").pack()
        ttk.Label(frame, text=f"Biển số: {plate}", foreground="#5b6b7b").pack(pady=(0, 8))
        if bank.is_configured:
            payload = build_vietqr(bank, amount=amount or None, description=f"Gui xe {plate}")
            image = self._qr_image(payload)
            if image is not None:
                photo = ImageTk.PhotoImage(image)
                self._qr_photo = photo  # keep a reference
                ttk.Label(frame, image=photo).pack(pady=6)
            else:
                ttk.Label(frame, text=payload, wraplength=280, foreground="#5b6b7b").pack(pady=6)
            ttk.Label(frame, text=f"{bank.account_name or ''}  {bank.account_number}", foreground="#5b6b7b").pack()
        else:
            ttk.Label(
                frame,
                text="Chưa cấu hình tài khoản ngân hàng.\nĐặt bank_bin + bank_account trong config để hiện mã VietQR.",
                foreground="#e05260",
                wraplength=280,
                justify=tk.CENTER,
            ).pack(pady=8)

        def confirm() -> None:
            self.event_store.mark_paid(visit_id, "QR")
            user = self.current_user.username if self.current_user else None
            self.event_store.write_audit(user, "PAYMENT_QR", f"{plate} {int(amount or 0)}")
            self._refresh_visit_views()
            self.status_var.set(f"Đã thu QR {self._money(amount)}đ")
            window.destroy()

        ttk.Button(frame, text="✅ Xác nhận đã thu", command=confirm, style="Accent.TButton").pack(fill=tk.X, pady=(12, 0))

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
        if not self._require_admin("xóa toàn bộ dữ liệu"):
            return
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
