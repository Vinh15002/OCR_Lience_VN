from __future__ import annotations

import argparse
import math
import threading
import time
import uuid
from collections import deque
from datetime import date, datetime
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from . import analytics
from .auth import ROLE_ADMIN
from .charts import BarChart, HBarChart, StatTile
from .config import AppConfig, CameraConfig, load_config, save_config
from .fileops import open_with_default_app
from .gate import build_gate
from .parking import (
    ALLOW,
    BICYCLE,
    CAR,
    DENY,
    GUEST,
    MOTORBIKE,
    POLICY_ALL,
    POLICY_REGISTERED_ONLY,
    VEHICLE_TYPE_LABELS,
    VEHICLE_TYPES,
    RegisteredVehicle,
)
from .bankfeed import PROVIDERS, FeedError, build_feed, match_requested
from .payment import VIETQR_BANKS, build_vietqr, transfer_note
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
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        min_width = min(screen_width, max(640, min(1000, screen_width - 80)))
        min_height = min(screen_height, max(480, min(650, screen_height - 120)))
        initial_width = max(min_width, min(1450, int(screen_width * 0.92)))
        initial_height = max(min_height, min(900, int(screen_height * 0.88)))
        left = max(0, (screen_width - initial_width) // 2)
        top = max(0, (screen_height - initial_height) // 3)
        self.geometry(f"{initial_width}x{initial_height}+{left}+{top}")
        self.minsize(min_width, min_height)
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
            tariff_table=self.app_config.tariff_table(),
            default_vehicle_type=self.app_config.default_vehicle_type,
        )
        self.gate = build_gate(self.app_config)
        self._gate_denied_until = 0.0
        self._gate_denied_plate = ""
        self.current_user = None
        self.active_shift = self.event_store.current_shift()
        self._bank_feed = None
        self._bank_thread: threading.Thread | None = None
        self._bank_stop = threading.Event()
        self._feed_status = ""
        self._paid_visit_ids: set[int] = set()
        self._open_payment_visits: set[int] = set()
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
        self._apply_retention()
        self._start_bank_feed()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_output)
        self.after(50, self._poll_previews)
        if self.app_config.require_login:
            self.after(120, self._prompt_login)
        if self.app_config.auto_start and self.app_config.cameras:
            self.after(900, self._maybe_auto_start)

    def _build_ui(self) -> None:
        self._setup_style()
        self.status_var = tk.StringVar(value="Sẵn sàng")

        self._build_header()
        self._build_statusbar()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        pages: dict[str, ttk.Frame] = {}
        for key, title in (
            ("monitor", "  Giám sát  "),
            ("parking", "  Bãi xe  "),
            ("vehicles", "  Đăng ký xe  "),
            ("reports", "  Báo cáo  "),
            ("settings", "  Cài đặt  "),
        ):
            page = ttk.Frame(self.notebook, padding=8)
            self.notebook.add(page, text=title)
            pages[key] = page

        # Settings first: it owns the ROI/detection variables that saving and
        # starting recognition read.
        self._build_settings_page(pages["settings"])
        self._build_monitor_page(pages["monitor"])
        self._build_parking_page(pages["parking"])
        self._build_vehicles_page(pages["vehicles"])
        self._build_report_page(pages["reports"])
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _make_scrollable_tree(
        self,
        parent: tk.Misc,
        *,
        columns: tuple[str, ...],
        height: int,
        expand: bool = True,
    ) -> ttk.Treeview:
        """Create a Treeview with permanent vertical and horizontal scrollbars."""
        host = ttk.Frame(parent)
        host.pack(fill=tk.BOTH if expand else tk.X, expand=expand)
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)

        tree = ttk.Treeview(host, columns=columns, show="headings", height=height)
        vertical = ttk.Scrollbar(host, orient=tk.VERTICAL, command=tree.yview)
        horizontal = ttk.Scrollbar(host, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        return tree

    def _make_scrollable_frame(
        self,
        parent: tk.Misc,
        *,
        horizontal: bool = False,
        fill_height: bool = False,
    ) -> tuple[ttk.Frame, tk.Canvas, ttk.Frame]:
        """Create a canvas-backed frame whose content can grow beyond the viewport."""
        host = ttk.Frame(parent)
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            host,
            borderwidth=0,
            highlightthickness=0,
            background=self._palette["bg"],
        )
        vertical = ttk.Scrollbar(host, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vertical.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")

        if horizontal:
            horizontal_bar = ttk.Scrollbar(host, orient=tk.HORIZONTAL, command=canvas.xview)
            canvas.configure(xscrollcommand=horizontal_bar.set)
            horizontal_bar.grid(row=1, column=0, sticky="ew")

        content = ttk.Frame(canvas)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def sync_scroll_region(_event=None) -> None:
            options: dict[str, int] = {}
            if horizontal and canvas.winfo_width() > 1:
                options["width"] = max(canvas.winfo_width(), content.winfo_reqwidth())
            if fill_height and canvas.winfo_height() > 1:
                options["height"] = max(canvas.winfo_height(), content.winfo_reqheight())
            if options:
                canvas.itemconfigure(content_window, **options)
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_viewport(event) -> None:
            target_width = event.width
            if horizontal:
                target_width = max(target_width, content.winfo_reqwidth())
            options: dict[str, int] = {"width": target_width}
            if fill_height:
                options["height"] = max(event.height, content.winfo_reqheight())
            canvas.itemconfigure(content_window, **options)
            canvas.configure(scrollregion=canvas.bbox("all"))

        content.bind("<Configure>", sync_scroll_region)
        canvas.bind("<Configure>", fit_viewport)
        content._sync_scroll_region = sync_scroll_region
        return host, canvas, content

    @staticmethod
    def _bind_canvas_mousewheel(widget: tk.Misc, canvas: tk.Canvas, horizontal: bool = False) -> None:
        """Let the wheel scroll a canvas while the pointer is over any child control."""
        def scroll_vertical(event) -> str:
            if event.delta:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        widget.bind("<MouseWheel>", scroll_vertical)
        if horizontal:
            def scroll_horizontal(event) -> str:
                if event.delta:
                    canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"

            widget.bind("<Shift-MouseWheel>", scroll_horizontal)
        for child in widget.winfo_children():
            PlateApp._bind_canvas_mousewheel(child, canvas, horizontal)

    @staticmethod
    def _set_initial_sash(panes: ttk.Panedwindow, fraction: float) -> None:
        """Set a useful first split while leaving the sash user-adjustable afterwards."""
        def position(_event=None) -> None:
            if getattr(panes, "_initial_sash_set", False):
                return
            vertical = str(panes.cget("orient")) == str(tk.VERTICAL)
            size = panes.winfo_height() if vertical else panes.winfo_width()
            if size <= 2:
                panes.after_idle(position)
                return
            panes.sashpos(0, int(size * fraction))
            panes._initial_sash_set = True

        panes.bind("<Map>", position, add="+")

    def _build_header(self) -> None:
        palette = self._palette
        bar = tk.Frame(self, bg=palette["surface"])
        bar.pack(fill=tk.X, side=tk.TOP)
        tk.Frame(self, bg="#d5dbe2", height=1).pack(fill=tk.X, side=tk.TOP)

        identity = tk.Frame(bar, bg=palette["surface"])
        identity.pack(fill=tk.X, padx=14, pady=(6, 2))
        tk.Label(
            identity, text="OCR PLATE", bg=palette["surface"], fg=palette["text"],
            font=("Segoe UI", 14, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(
            identity, text="Hệ thống bãi xe tự động", bg=palette["surface"], fg=palette["muted"],
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        self.user_label_var = tk.StringVar(value="")
        tk.Label(
            identity, textvariable=self.user_label_var, bg=palette["surface"], fg=palette["muted"],
            font=("Segoe UI", 9), anchor=tk.E,
        ).pack(side=tk.RIGHT)
        self.shift_label_var = tk.StringVar(value="Chưa mở ca")
        tk.Label(
            identity, textvariable=self.shift_label_var, bg=palette["surface"], fg=palette["muted"],
            font=("Segoe UI", 9), anchor=tk.E,
        ).pack(side=tk.RIGHT, padx=12)

        actions = tk.Frame(bar, bg=palette["surface"])
        actions.pack(fill=tk.X, padx=14, pady=(0, 6))
        self.start_button = ttk.Button(
            actions, text="▶  Bắt đầu", command=self.start, style="Accent.TButton"
        )
        self.start_button.pack(side=tk.LEFT, padx=(0, 4))
        self.stop_button = ttk.Button(actions, text="■  Dừng", command=self.stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT)
        ttk.Button(actions, text="🚧  Mở barrier", command=self._open_gate_manually).pack(
            side=tk.LEFT, padx=(16, 4)
        )
        ttk.Button(actions, text="⛔  Đóng barrier", command=self._close_gate_manually).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        self.shift_button = ttk.Button(actions, text="🕒  Mở ca", command=self._toggle_shift)
        self.shift_button.pack(side=tk.LEFT)
        self._refresh_shift_widgets()

    def _build_monitor_page(self, parent: ttk.Frame) -> None:
        self._build_gate_simulator(parent)

        split = ttk.Panedwindow(parent, orient=tk.VERTICAL)
        split.pack(fill=tk.BOTH, expand=True)
        camera_host, self.camera_canvas, self.camera_grid = self._make_scrollable_frame(
            split, fill_height=True
        )
        split.add(camera_host, weight=3)
        self._rebuild_camera_grid()

        recent = ttk.LabelFrame(split, text="Nhận dạng gần đây", padding=4)
        split.add(recent, weight=2)
        self._set_initial_sash(split, 0.55)
        self.event_tree = self._make_scrollable_tree(
            recent,
            columns=("time", "direction", "plate", "access", "score"),
            height=6,
        )
        for column, label, width in (
            ("time", "Thời gian", 90),
            ("direction", "Chiều", 60),
            ("plate", "Biển số", 130),
            ("access", "Cổng", 100),
            ("score", "Độ tin cậy", 90),
        ):
            self.event_tree.heading(column, text=label)
            self.event_tree.column(
                column, width=width, minwidth=max(55, int(width * 0.65)),
                anchor=tk.CENTER, stretch=True,
            )
        self.event_tree.tag_configure("ALLOW", background=self._palette["row_in"])
        self.event_tree.tag_configure("GUEST", background=self._palette["row_out"])
        self.event_tree.tag_configure("DENY", background=self._palette["row_review"])

    def _build_parking_page(self, parent: ttk.Frame) -> None:
        split = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)

        inside_panel = ttk.LabelFrame(split, text="Xe đang trong bãi", padding=6)
        split.add(inside_panel, weight=2)
        self.inside_count_var = tk.StringVar(value="0 xe")
        ttk.Label(
            inside_panel, textvariable=self.inside_count_var, style="Heading.TLabel"
        ).pack(anchor=tk.W, pady=(0, 4))
        self.inside_tree = self._make_scrollable_tree(
            inside_panel, columns=("plate", "type", "entry", "camera"), height=16
        )
        for column, label, width in (
            ("plate", "Biển số", 110),
            ("type", "Loại xe", 80),
            ("entry", "Giờ vào", 90),
            ("camera", "Cổng vào", 100),
        ):
            self.inside_tree.heading(column, text=label)
            self.inside_tree.column(
                column, width=width, minwidth=max(55, int(width * 0.65)),
                anchor=tk.CENTER, stretch=True,
            )
        self.inside_tree.tag_configure("IN", background=self._palette["row_in"])

        manual = ttk.LabelFrame(inside_panel, text="Ghi thủ công (mất vé / camera không đọc được)", padding=6)
        manual.pack(fill=tk.X, pady=(6, 0))
        line = ttk.Frame(manual)
        line.pack(fill=tk.X)
        ttk.Label(line, text="Biển số").pack(side=tk.LEFT)
        self.manual_plate_var = tk.StringVar()
        ttk.Entry(line, textvariable=self.manual_plate_var, width=12).pack(side=tk.LEFT, padx=4)
        self.manual_type_var = tk.StringVar(value=self.app_config.default_vehicle_type)
        ttk.Combobox(
            line, textvariable=self.manual_type_var, values=VEHICLE_TYPES,
            state="readonly", width=10,
        ).pack(side=tk.LEFT, padx=4)
        buttons = ttk.Frame(manual)
        buttons.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(
            buttons, text="⬅ Ghi xe VÀO", command=lambda: self._record_manual_event("IN")
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons, text="Ghi xe RA ➡", command=lambda: self._record_manual_event("OUT")
        ).pack(side=tk.LEFT, padx=4)

        visits_panel = ttk.LabelFrame(split, text="Lượt gửi xe & thu phí", padding=6)
        split.add(visits_panel, weight=3)
        self._set_initial_sash(split, 0.40)
        primary_actions = ttk.Frame(visits_panel)
        primary_actions.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(
            primary_actions, text="💵 Thu tiền mặt", command=lambda: self._collect_payment("CASH"),
            style="Accent.TButton",
        ).pack(side=tk.LEFT)
        ttk.Button(primary_actions, text="📱 Thu QR", command=lambda: self._collect_payment("QR")).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(primary_actions, text="🟣 Thu MoMo", command=lambda: self._collect_payment("MOMO")).pack(
            side=tk.LEFT
        )
        secondary_actions = ttk.Frame(visits_panel)
        secondary_actions.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(secondary_actions, text="🔍 Đối soát", command=self._show_visit_review).pack(
            side=tk.LEFT
        )
        ttk.Button(secondary_actions, text="✏ Sửa biển", command=self._correct_visit_plate).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(secondary_actions, text="🏍 Đổi loại xe", command=self._retype_visit).pack(side=tk.LEFT)
        export_actions = ttk.Frame(visits_panel)
        export_actions.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(export_actions, text="Xuất CSV", command=self._export_visits_csv).pack(side=tk.RIGHT)
        self.revenue_var = tk.StringVar(value="Doanh thu hôm nay: 0")
        ttk.Label(visits_panel, textvariable=self.revenue_var, foreground="#1f6fe0").pack(
            anchor=tk.W, pady=(0, 4)
        )
        self.visit_tree = self._make_scrollable_tree(
            visits_panel,
            columns=("plate", "type", "entry", "exit", "duration", "fee", "pay", "flag"),
            height=18,
        )
        for column, label, width in (
            ("plate", "Biển số", 105),
            ("type", "Loại", 65),
            ("entry", "Vào", 70),
            ("exit", "Ra", 70),
            ("duration", "Thời gian", 85),
            ("fee", "Phí", 75),
            ("pay", "Thanh toán", 125),
            ("flag", "Đối soát", 110),
        ):
            self.visit_tree.heading(column, text=label)
            self.visit_tree.column(
                column, width=width, minwidth=max(55, int(width * 0.65)),
                anchor=tk.CENTER, stretch=True,
            )
        self.visit_tree.column("pay", minwidth=115)
        self.visit_tree.tag_configure("INSIDE", background=self._palette["row_in"])
        self.visit_tree.tag_configure("COMPLETED", background=self._palette["row_muted"])
        self.visit_tree.tag_configure("REVIEW", background=self._palette["row_review"])
        self.visit_tree.tag_configure("FLAGGED", background="#ffe9c7")
        self.visit_tree.bind("<Double-1>", lambda _event: self._show_visit_review())
        ttk.Label(
            visits_panel,
            text="Bấm đúp một lượt để mở cửa sổ đối soát ảnh xe vào / xe ra.",
            foreground=self._palette["muted"],
        ).pack(anchor=tk.W, pady=(2, 0))

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
        style.configure("Heading.TLabel", font=("Segoe UI", 12, "bold"), foreground=palette["text"])
        # KPI tiles on the report screen: a white card whose labels must opt out
        # of the grey page background.
        style.configure("Card.TFrame", background=palette["surface"], relief="flat")
        style.configure(
            "TileTitle.TLabel",
            background=palette["surface"],
            foreground=palette["muted"],
            font=("Segoe UI", 8, "bold"),
        )
        style.configure(
            "TileValue.TLabel", background=palette["surface"], font=("Segoe UI", 17, "bold")
        )
        style.configure(
            "TileHint.TLabel",
            background=palette["surface"],
            foreground=palette["muted"],
            font=("Segoe UI", 8),
        )
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

        stats = tk.Frame(bar, bg=palette["statusbar"])
        stats.pack(side=tk.LEFT, fill=tk.Y)

        self.state_dot = tk.Label(stats, text="●", bg=palette["statusbar"], fg=palette["off"], font=("Segoe UI", 11))
        self.state_dot.pack(side=tk.LEFT, padx=(12, 4))
        self.state_text = tk.Label(
            stats, text="Stopped", bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9, "bold")
        )
        self.state_text.pack(side=tk.LEFT)

        def separator() -> None:
            tk.Label(stats, text="│", bg=palette["statusbar"], fg=palette["status_sep"]).pack(side=tk.LEFT, padx=10)

        separator()
        self.cams_stat = tk.Label(stats, text="Cameras 0", bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9))
        self.cams_stat.pack(side=tk.LEFT)
        separator()
        self.events_stat = tk.Label(stats, text="Events 0", bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9))
        self.events_stat.pack(side=tk.LEFT)
        separator()
        self.fps_stat = tk.Label(stats, text="Detect 0.0/s", bg=palette["statusbar"], fg=palette["status_text"], font=("Segoe UI", 9))
        self.fps_stat.pack(side=tk.LEFT)
        separator()
        self.gate_stat = tk.Label(stats, text="⛔ Cổng đóng", bg=palette["statusbar"], fg=palette["off"], font=("Segoe UI", 9, "bold"))
        self.gate_stat.pack(side=tk.LEFT)

        tk.Label(
            bar, textvariable=self.status_var, bg=palette["statusbar"], fg=palette["status_text"],
            font=("Segoe UI", 9), anchor=tk.E,
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=12)

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

    def _build_vehicles_page(self, parent: ttk.Frame) -> None:
        form = ttk.LabelFrame(parent, text="Đăng ký xe (whitelist / blacklist)", padding=8)
        form.pack(fill=tk.X)
        row = ttk.Frame(form)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Biển số").pack(side=tk.LEFT)
        self.veh_plate_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.veh_plate_var, width=14).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(row, text="Chủ xe").pack(side=tk.LEFT)
        self.veh_owner_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.veh_owner_var, width=22).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(row, text="SĐT").pack(side=tk.LEFT)
        self.veh_phone_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.veh_phone_var, width=13).pack(side=tk.LEFT, padx=(4, 12))

        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=3)
        ttk.Label(row2, text="Quyền").pack(side=tk.LEFT)
        self.veh_access_var = tk.StringVar(value=ALLOW)
        ttk.Combobox(
            row2, textvariable=self.veh_access_var, values=(ALLOW, DENY), state="readonly", width=7
        ).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(row2, text="Loại xe").pack(side=tk.LEFT)
        self.veh_type_var = tk.StringVar(value=self.app_config.default_vehicle_type)
        type_box = ttk.Combobox(
            row2, textvariable=self.veh_type_var, values=VEHICLE_TYPES, state="readonly", width=10
        )
        type_box.pack(side=tk.LEFT, padx=(4, 12))
        type_box.bind("<<ComboboxSelected>>", self._sync_subscription_price)
        ttk.Button(row2, text="Lưu / Cập nhật", command=self._add_vehicle, style="Accent.TButton").pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(row2, text="Xóa", command=self._remove_vehicle_selected).pack(side=tk.LEFT)
        ttk.Label(
            form,
            text="ALLOW = xe tháng / được vào tự do (miễn phí lượt).   DENY = chặn, cổng không mở.",
            foreground=self._palette["muted"],
        ).pack(anchor=tk.W, pady=(4, 0))

        pass_form = ttk.LabelFrame(parent, text="Vé tháng", padding=8)
        pass_form.pack(fill=tk.X, pady=(10, 0))
        pass_row = ttk.Frame(pass_form)
        pass_row.pack(fill=tk.X)
        ttk.Label(pass_row, text="Số tháng").pack(side=tk.LEFT)
        self.sub_months_var = tk.IntVar(value=1)
        months_box = ttk.Spinbox(
            pass_row, from_=1, to=24, width=5, textvariable=self.sub_months_var,
            command=self._sync_subscription_price,
        )
        months_box.pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(pass_row, text="Số tiền").pack(side=tk.LEFT)
        self.sub_amount_var = tk.DoubleVar(value=self.app_config.monthly_fee(self.veh_type_var.get()))
        ttk.Spinbox(
            pass_row, from_=0, to=100_000_000, increment=50_000, width=12,
            textvariable=self.sub_amount_var,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            pass_row, text="🎫 Bán / Gia hạn vé tháng", command=self._sell_subscription,
            style="Accent.TButton",
        ).pack(side=tk.LEFT, padx=10)
        ttk.Label(
            pass_form,
            text="Gia hạn sớm sẽ cộng dồn vào số ngày còn lại. Tiền vé tháng được tính vào ca trực đang mở.",
            foreground=self._palette["muted"],
        ).pack(anchor=tk.W, pady=(4, 0))

        listing = ttk.LabelFrame(parent, text="Danh sách xe đã đăng ký", padding=6)
        listing.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.expiring_var = tk.StringVar(value="")
        ttk.Label(listing, textvariable=self.expiring_var, foreground=self._palette["danger"]).pack(
            anchor=tk.W, pady=(0, 4)
        )
        self.vehicle_tree = self._make_scrollable_tree(
            listing,
            columns=("plate", "owner", "phone", "type", "access", "until", "left"),
            height=14,
        )
        for column, label, width in (
            ("plate", "Biển số", 110),
            ("owner", "Chủ xe", 170),
            ("phone", "SĐT", 100),
            ("type", "Loại xe", 80),
            ("access", "Quyền", 70),
            ("until", "Hạn vé", 95),
            ("left", "Còn lại", 85),
        ):
            self.vehicle_tree.heading(column, text=label)
            self.vehicle_tree.column(
                column, width=width, minwidth=max(55, int(width * 0.65)),
                anchor=tk.CENTER, stretch=True,
            )
        self.vehicle_tree.tag_configure(ALLOW, background=self._palette["row_in"])
        self.vehicle_tree.tag_configure(DENY, background=self._palette["row_review"])
        self.vehicle_tree.tag_configure("EXPIRING", background="#ffe9c7")
        self.vehicle_tree.tag_configure("EXPIRED", background=self._palette["row_review"])
        self.vehicle_tree.bind("<<TreeviewSelect>>", self._vehicle_selected)
        self._refresh_vehicle_list()

    def _build_settings_page(self, parent: ttk.Frame) -> None:
        split = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(split)
        split.add(left, weight=1)

        # The settings column is taller than the window on smaller displays.
        # It can also grow wider at high DPI, so Shift+wheel and the horizontal
        # bar keep every field reachable when either pane is made very narrow.
        right_host, right_canvas, right = self._make_scrollable_frame(
            split, horizontal=True
        )
        split.add(right_host, weight=1)
        self._set_initial_sash(split, 0.55)

        self._build_sources_settings(left)
        self._build_recognition_settings(right)
        self._build_rules_settings(right)
        self._build_payment_settings(right)
        self._build_system_settings(right)

        self._bind_canvas_mousewheel(right_canvas, right_canvas, horizontal=True)
        self._bind_canvas_mousewheel(right, right_canvas, horizontal=True)

    def _build_sources_settings(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Nguồn video / camera", padding=8)
        panel.pack(fill=tk.BOTH, expand=True)

        video_row = ttk.Frame(panel)
        video_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(video_row, text="Video mẫu").pack(side=tk.LEFT)
        sample_paths = sorted(Path("sample_videos").glob("*.mp4"))
        self.sample_lookup = {path.name: path for path in sample_paths}
        self.sample_var = tk.StringVar()
        self.sample_combo = ttk.Combobox(
            video_row, textvariable=self.sample_var, values=list(self.sample_lookup),
            width=26, state="readonly",
        )
        self.sample_combo.pack(side=tk.LEFT, padx=(6, 4))
        if sample_paths:
            current_uri = self.app_config.cameras[0].uri if self.app_config.cameras else ""
            current_name = Path(current_uri).name
            self.sample_var.set(
                current_name if current_name in self.sample_lookup else sample_paths[0].name
            )
        sample_options = ttk.Frame(panel)
        sample_options.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(sample_options, text="Chiều").pack(side=tk.LEFT)
        self.sample_direction_var = tk.StringVar(value="IN")
        self.sample_direction_combo = ttk.Combobox(
            sample_options, textvariable=self.sample_direction_var, values=("IN", "OUT"),
            width=5, state="readonly",
        )
        self.sample_direction_combo.pack(side=tk.LEFT, padx=2)
        self.sample_direction_combo.bind("<<ComboboxSelected>>", self._sample_direction_changed)
        ttk.Label(sample_options, text="Trễ (s)").pack(side=tk.LEFT, padx=(8, 2))
        self.sample_delay_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(
            sample_options, from_=0, to=300, increment=1, width=5, textvariable=self.sample_delay_var
        ).pack(side=tk.LEFT)
        self.sample_loop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            sample_options, text="Lặp video", variable=self.sample_loop_var
        ).pack(side=tk.LEFT, padx=(8, 0))

        buttons = ttk.Frame(panel)
        buttons.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(buttons, text="Thay toàn bộ bằng video mẫu", command=self._use_sample).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Thêm video mẫu", command=self._add_sample).pack(side=tk.LEFT, padx=4)
        ttk.Button(panel, text="Mở file video...", command=self._add_video).pack(anchor=tk.W, pady=(0, 6))

        uri_row = ttk.Frame(panel)
        uri_row.pack(fill=tk.X, pady=(0, 8))
        uri_row.columnconfigure(1, weight=1)
        ttk.Label(uri_row, text="Camera IP / RTSP / webcam").grid(row=0, column=0, sticky=tk.W)
        self.source_var = tk.StringVar()
        ttk.Entry(uri_row, textvariable=self.source_var, width=12).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(uri_row, text="Thêm nguồn", command=self._add_uri).grid(row=0, column=2)

        self.source_tree = self._make_scrollable_tree(
            panel, columns=("direction", "delay", "loop", "name", "uri"),
            height=9,
        )
        self.source_tree.heading("direction", text="Chiều")
        self.source_tree.heading("delay", text="Trễ")
        self.source_tree.heading("loop", text="Lặp")
        self.source_tree.heading("name", text="Tên")
        self.source_tree.heading("uri", text="Nguồn")
        self.source_tree.column("direction", width=55, minwidth=55, anchor=tk.CENTER, stretch=False)
        self.source_tree.column("delay", width=50, minwidth=50, anchor=tk.CENTER, stretch=False)
        self.source_tree.column("loop", width=45, minwidth=45, anchor=tk.CENTER, stretch=False)
        self.source_tree.column("name", width=130, minwidth=90)
        self.source_tree.column("uri", width=360, minwidth=180)
        self.source_tree.bind("<<TreeviewSelect>>", self._source_selected)

        source_fields = ttk.Frame(panel)
        source_fields.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(source_fields, text="Chiều").pack(side=tk.LEFT)
        self.camera_direction_var = tk.StringVar(value="IN")
        ttk.Combobox(
            source_fields, textvariable=self.camera_direction_var, values=("IN", "OUT"),
            state="readonly", width=5,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(source_fields, text="Trễ (s)").pack(side=tk.LEFT, padx=(6, 2))
        self.camera_delay_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(
            source_fields, from_=0, to=300, increment=1, width=5, textvariable=self.camera_delay_var
        ).pack(side=tk.LEFT)
        self.camera_loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(source_fields, text="Lặp video", variable=self.camera_loop_var).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        source_actions = ttk.Frame(panel)
        source_actions.pack(fill=tk.X)
        ttk.Button(
            source_actions, text="Áp dụng cho nguồn đã chọn", command=self._set_selected_direction
        ).pack(
            side=tk.LEFT
        )
        ttk.Button(source_actions, text="Xóa nguồn", command=self._remove_selected).pack(side=tk.RIGHT)

    def _build_recognition_settings(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Nhận dạng", padding=8)
        panel.pack(fill=tk.X)

        roi_row = ttk.Frame(panel)
        roi_row.pack(fill=tk.X, pady=3)
        ttk.Label(roi_row, text="Vùng quét ngang %").pack(side=tk.LEFT)
        self.roi_width_var = tk.IntVar(value=int(self.app_config.roi_width * 100))
        ttk.Spinbox(roi_row, from_=20, to=100, width=5, textvariable=self.roi_width_var).pack(side=tk.LEFT, padx=4)
        ttk.Label(roi_row, text="dọc %").pack(side=tk.LEFT, padx=(8, 0))
        self.roi_height_var = tk.IntVar(value=int(self.app_config.roi_height * 100))
        ttk.Spinbox(roi_row, from_=20, to=100, width=5, textvariable=self.roi_height_var).pack(side=tk.LEFT, padx=4)

        detect_row = ttk.Frame(panel)
        detect_row.pack(fill=tk.X, pady=3)
        ttk.Label(detect_row, text="Chu kỳ nhận dạng (s)").pack(side=tk.LEFT)
        self.detection_interval_var = tk.DoubleVar(value=self.app_config.detection_interval_seconds)
        ttk.Spinbox(
            detect_row, from_=0.1, to=5.0, increment=0.1, width=5,
            textvariable=self.detection_interval_var,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(detect_row, text="Kích thước ảnh").pack(side=tk.LEFT, padx=(8, 0))
        self.detection_imgsz_var = tk.IntVar(value=self.app_config.detection_imgsz)
        ttk.Combobox(
            detect_row, textvariable=self.detection_imgsz_var, values=(640, 768, 960, 1280),
            state="readonly", width=6,
        ).pack(side=tk.LEFT, padx=4)

        model_row = ttk.Frame(panel)
        model_row.pack(fill=tk.X, pady=3)
        ttk.Label(model_row, text="Mô hình OCR").pack(side=tk.LEFT)
        self.ocr_model_var = tk.StringVar(value=self.app_config.ocr_recognition_model)
        ocr_models = ["PP-OCRv6_medium_rec", "PP-OCRv6_tiny_rec"]
        if self.app_config.ocr_recognition_model not in ocr_models:
            ocr_models.append(self.app_config.ocr_recognition_model)
        ttk.Combobox(
            model_row, textvariable=self.ocr_model_var, values=ocr_models, state="readonly", width=22
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(model_row, text="Áp dụng", command=self._apply_ocr_model).pack(side=tk.LEFT)

        ttk.Button(panel, text="Lưu thông số nhận dạng", command=self._save_recognition_settings).pack(
            anchor=tk.E, pady=(6, 0)
        )

    def _build_rules_settings(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Cổng & phí gửi xe", padding=8)
        panel.pack(fill=tk.X, pady=(10, 0))

        row1 = ttk.Frame(panel)
        row1.pack(fill=tk.X, pady=3)
        ttk.Label(row1, text="Chế độ cổng").pack(side=tk.LEFT)
        self.policy_var = tk.StringVar(value=self.app_config.open_gate_policy)
        ttk.Combobox(
            row1, textvariable=self.policy_var, values=(POLICY_ALL, POLICY_REGISTERED_ONLY),
            state="readonly", width=18,
        ).pack(side=tk.LEFT, padx=4)

        row2 = ttk.Frame(panel)
        row2.pack(fill=tk.X, pady=3)
        ttk.Label(row2, text="Phí/lượt").pack(side=tk.LEFT)
        self.flat_fee_var = tk.DoubleVar(value=self.app_config.parking_flat_fee)
        ttk.Spinbox(
            row2, from_=0, to=1_000_000, increment=1000, width=9, textvariable=self.flat_fee_var
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(row2, text="Phí/giờ").pack(side=tk.LEFT)
        self.hourly_fee_var = tk.DoubleVar(value=self.app_config.parking_hourly_fee)
        ttk.Spinbox(
            row2, from_=0, to=1_000_000, increment=1000, width=9, textvariable=self.hourly_fee_var
        ).pack(side=tk.LEFT, padx=4)

        row3 = ttk.Frame(panel)
        row3.pack(fill=tk.X, pady=3)
        ttk.Label(row3, text="Miễn phí (phút)").pack(side=tk.LEFT)
        self.free_min_var = tk.IntVar(value=self.app_config.parking_free_minutes)
        ttk.Spinbox(
            row3, from_=0, to=1440, increment=5, width=7, textvariable=self.free_min_var
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(row3, text="Sức chứa (chỗ)").pack(side=tk.LEFT)
        self.capacity_var = tk.IntVar(value=self.app_config.parking_capacity)
        ttk.Spinbox(
            row3, from_=0, to=100_000, increment=10, width=7, textvariable=self.capacity_var
        ).pack(side=tk.LEFT, padx=4)

        row4 = ttk.Frame(panel)
        row4.pack(fill=tk.X, pady=3)
        ttk.Label(row4, text="Trần/ngày").pack(side=tk.LEFT)
        self.daily_cap_var = tk.DoubleVar(value=self.app_config.parking_daily_cap)
        ttk.Spinbox(
            row4, from_=0, to=10_000_000, increment=10_000, width=9, textvariable=self.daily_cap_var
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(row4, text="Phí qua đêm").pack(side=tk.LEFT)
        self.overnight_var = tk.DoubleVar(value=self.app_config.parking_overnight_fee)
        ttk.Spinbox(
            row4, from_=0, to=10_000_000, increment=5_000, width=9, textvariable=self.overnight_var
        ).pack(side=tk.LEFT, padx=(4, 10))

        row5 = ttk.Frame(panel)
        row5.pack(fill=tk.X, pady=3)
        ttk.Label(row5, text="Giờ tính đêm").pack(side=tk.LEFT)
        self.night_hour_var = tk.IntVar(value=self.app_config.parking_night_hour)
        ttk.Spinbox(
            row5, from_=0, to=23, width=4, textvariable=self.night_hour_var
        ).pack(side=tk.LEFT, padx=4)

        row6 = ttk.Frame(panel)
        row6.pack(fill=tk.X, pady=3)
        ttk.Label(row6, text="Loại xe mặc định").pack(side=tk.LEFT)
        self.default_type_var = tk.StringVar(value=self.app_config.default_vehicle_type)
        ttk.Combobox(
            row6, textvariable=self.default_type_var, values=VEHICLE_TYPES,
            state="readonly", width=11,
        ).pack(side=tk.LEFT, padx=4)

        # Per-class overrides: the default class uses the price list above, the
        # others get their own flat/hourly price.
        other = ttk.LabelFrame(panel, text="Biểu phí riêng theo loại xe", padding=6)
        other.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(other, text="Loại", width=9).grid(row=0, column=0)
        ttk.Label(other, text="Phí/lượt", width=10).grid(row=0, column=1)
        ttk.Label(other, text="Phí/giờ", width=10).grid(row=0, column=2)
        ttk.Label(other, text="Trần/ngày", width=10).grid(row=0, column=3)
        self.type_fee_vars: dict[str, tuple[tk.DoubleVar, tk.DoubleVar, tk.DoubleVar]] = {}
        for index, vehicle_type in enumerate((CAR, BICYCLE), start=1):
            saved = (self.app_config.parking_tariffs or {}).get(vehicle_type, {})
            variables = (
                tk.DoubleVar(value=float(saved.get("flat_fee", 0) or 0)),
                tk.DoubleVar(value=float(saved.get("hourly_fee", 0) or 0)),
                tk.DoubleVar(value=float(saved.get("daily_cap", 0) or 0)),
            )
            self.type_fee_vars[vehicle_type] = variables
            ttk.Label(other, text=VEHICLE_TYPE_LABELS[vehicle_type]).grid(row=index, column=0)
            for column, variable in enumerate(variables, start=1):
                ttk.Spinbox(
                    other, from_=0, to=10_000_000, increment=5_000, width=9, textvariable=variable
                ).grid(row=index, column=column, padx=2, pady=2)
        ttk.Label(
            panel,
            text="Sức chứa dùng để tính tỷ lệ lấp đầy và vòng quay chỗ trong báo cáo (0 = bỏ qua). "
                 "Để trống (0) biểu phí riêng nghĩa là loại xe đó dùng chung biểu phí ở trên.",
            foreground=self._palette["muted"],
            wraplength=360,
        ).pack(anchor=tk.W, pady=(2, 0))
        ttk.Button(panel, text="Áp dụng quy tắc", command=self._apply_gate_rules, style="Accent.TButton").pack(
            anchor=tk.E, pady=(6, 0)
        )

    def _build_payment_settings(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Thu tiền QR (VietQR)", padding=8)
        panel.pack(fill=tk.X, pady=(10, 0))
        row1 = ttk.Frame(panel)
        row1.pack(fill=tk.X, pady=3)
        ttk.Label(row1, text="Mã NH (BIN)").pack(side=tk.LEFT)
        self.bank_lookup = {
            f"{name} ({bin_code})": bin_code for name, bin_code in VIETQR_BANKS
        }
        bank_options = ["Chọn ngân hàng", *self.bank_lookup]
        selected_bank = next(
            (
                label
                for label, bin_code in self.bank_lookup.items()
                if bin_code == self.app_config.bank_bin
            ),
            "",
        )
        if not selected_bank and self.app_config.bank_bin:
            selected_bank = f"Mã BIN hiện tại ({self.app_config.bank_bin})"
            self.bank_lookup[selected_bank] = self.app_config.bank_bin
            bank_options.append(selected_bank)
        self.bank_choice_var = tk.StringVar(value=selected_bank or "Chọn ngân hàng")
        ttk.Combobox(
            row1, textvariable=self.bank_choice_var, values=bank_options,
            state="readonly", width=25,
        ).pack(side=tk.LEFT, padx=4)

        row2 = ttk.Frame(panel)
        row2.pack(fill=tk.X, pady=3)
        ttk.Label(row2, text="Số tài khoản").pack(side=tk.LEFT)
        self.bank_account_var = tk.StringVar(value=self.app_config.bank_account)
        ttk.Entry(row2, textvariable=self.bank_account_var, width=18).pack(side=tk.LEFT, padx=4)

        row3 = ttk.Frame(panel)
        row3.pack(fill=tk.X, pady=3)
        ttk.Label(row3, text="Tên chủ TK").pack(side=tk.LEFT)
        self.bank_name_var = tk.StringVar(value=self.app_config.bank_account_name)
        ttk.Entry(row3, textvariable=self.bank_name_var, width=24).pack(side=tk.LEFT, padx=4)
        ttk.Button(row3, text="Lưu & kiểm tra", command=self._apply_bank_settings).pack(
            side=tk.LEFT, padx=6
        )
        self.bank_status_var = tk.StringVar(value="")
        ttk.Label(
            panel, textvariable=self.bank_status_var, wraplength=360, foreground=self._palette["muted"]
        ).pack(anchor=tk.W, pady=(2, 0))
        self._check_bank_settings()

        auto = ttk.LabelFrame(panel, text="Tự động xác nhận đã nhận tiền", padding=6)
        auto.pack(fill=tk.X, pady=(8, 0))
        feed_row1 = ttk.Frame(auto)
        feed_row1.pack(fill=tk.X, pady=2)
        ttk.Label(feed_row1, text="Dịch vụ").pack(side=tk.LEFT)
        self.payment_provider_var = tk.StringVar(value=self.app_config.payment_provider)
        ttk.Combobox(
            feed_row1, textvariable=self.payment_provider_var, values=PROVIDERS,
            state="readonly", width=8,
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(feed_row1, text="Chu kỳ (s)").pack(side=tk.LEFT)
        self.payment_poll_var = tk.DoubleVar(value=self.app_config.payment_poll_seconds)
        ttk.Spinbox(
            feed_row1, from_=5, to=600, increment=5, width=6, textvariable=self.payment_poll_var
        ).pack(side=tk.LEFT, padx=4)

        feed_row2 = ttk.Frame(auto)
        feed_row2.pack(fill=tk.X, pady=2)
        ttk.Label(feed_row2, text="API token").pack(side=tk.LEFT)
        self.payment_token_var = tk.StringVar(value=self.app_config.payment_api_token)
        ttk.Entry(feed_row2, textvariable=self.payment_token_var, width=26, show="•").pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(feed_row2, text="Áp dụng", command=self._apply_feed_settings).pack(side=tk.LEFT, padx=4)

        self.feed_status_var = tk.StringVar(value=self._feed_status or "Tắt")
        ttk.Label(
            auto, textvariable=self.feed_status_var, wraplength=350,
            foreground=self._palette["muted"],
        ).pack(anchor=tk.W, pady=(2, 0))
        ttk.Label(
            auto,
            text="Lấy API token ở my.sepay.vn (Cấu hình công ty → API Access) hoặc casso.vn. "
                 "Phần mềm chỉ ĐỌC giao dịch đến, không rút được tiền.",
            foreground=self._palette["muted"],
            wraplength=350,
        ).pack(anchor=tk.W, pady=(2, 0))

        momo = ttk.LabelFrame(panel, text="Ví điện tử MoMo", padding=6)
        momo.pack(fill=tk.X, pady=(8, 0))
        momo_row1 = ttk.Frame(momo)
        momo_row1.pack(fill=tk.X, pady=2)
        ttk.Label(momo_row1, text="Môi trường").pack(side=tk.LEFT)
        self.momo_environment_var = tk.StringVar(value=self.app_config.momo_environment)
        ttk.Combobox(
            momo_row1, textvariable=self.momo_environment_var,
            values=("sandbox", "production"), state="readonly", width=10,
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(momo_row1, text="Partner Code").pack(side=tk.LEFT)
        self.momo_partner_var = tk.StringVar(value=self.app_config.momo_partner_code)
        ttk.Entry(momo_row1, textvariable=self.momo_partner_var, width=18).pack(side=tk.LEFT, padx=4)

        momo_row2 = ttk.Frame(momo)
        momo_row2.pack(fill=tk.X, pady=2)
        ttk.Label(momo_row2, text="Access Key").pack(side=tk.LEFT)
        self.momo_access_var = tk.StringVar(value=self.app_config.momo_access_key)
        ttk.Entry(momo_row2, textvariable=self.momo_access_var, width=22, show="•").pack(
            side=tk.LEFT, padx=(4, 10)
        )

        momo_row3 = ttk.Frame(momo)
        momo_row3.pack(fill=tk.X, pady=2)
        ttk.Label(momo_row3, text="Secret Key").pack(side=tk.LEFT)
        self.momo_secret_var = tk.StringVar(value=self.app_config.momo_secret_key)
        ttk.Entry(momo_row3, textvariable=self.momo_secret_var, width=28, show="•").pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(momo_row3, text="Lưu MoMo", command=self._apply_momo_settings).pack(
            side=tk.LEFT, padx=4
        )
        self.momo_status_var = tk.StringVar(value="")
        ttk.Label(
            momo, textvariable=self.momo_status_var, wraplength=350,
            foreground=self._palette["muted"],
        ).pack(anchor=tk.W, pady=(2, 0))
        self._check_momo_settings()

    def _apply_feed_settings(self) -> None:
        self._stop_bank_feed()
        self.app_config.payment_provider = self.payment_provider_var.get()
        self.app_config.payment_api_token = self.payment_token_var.get().strip()
        self.app_config.payment_poll_seconds = max(5.0, float(self.payment_poll_var.get()))
        save_config(self.app_config, self.config_path)
        self._start_bank_feed()
        self.status_var.set("Đã áp dụng cấu hình đối soát tự động")

    def _check_momo_settings(self) -> None:
        problem = self.app_config.momo().problem
        self.momo_status_var.set(
            f"⚠ {problem}" if problem else f"✓ MoMo {self.app_config.momo_environment} đã sẵn sàng"
        )

    def _apply_momo_settings(self) -> None:
        self.app_config.momo_environment = self.momo_environment_var.get()
        self.app_config.momo_partner_code = self.momo_partner_var.get().strip()
        self.app_config.momo_access_key = self.momo_access_var.get().strip()
        self.app_config.momo_secret_key = self.momo_secret_var.get().strip()
        save_config(self.app_config, self.config_path)
        self._check_momo_settings()
        self.status_var.set("Đã lưu cấu hình ví MoMo")

    def _check_bank_settings(self) -> None:
        problem = self.app_config.bank().problem
        self.bank_status_var.set(
            f"⚠ {problem}" if problem else "✓ Tài khoản hợp lệ, nút QR ở tab Bãi xe đã dùng được."
        )

    def _apply_bank_settings(self) -> None:
        self.app_config.bank_bin = self.bank_lookup.get(self.bank_choice_var.get(), "")
        self.app_config.bank_account = self.bank_account_var.get().strip()
        self.app_config.bank_account_name = self.bank_name_var.get().strip().upper()
        self.bank_name_var.set(self.app_config.bank_account_name)
        save_config(self.app_config, self.config_path)
        self._check_bank_settings()
        self.status_var.set("Đã lưu tài khoản nhận tiền")

    def _build_system_settings(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="Hệ thống & dữ liệu", padding=8)
        panel.pack(fill=tk.X, pady=(10, 0))
        row1 = ttk.Frame(panel)
        row1.pack(fill=tk.X, pady=3)
        ttk.Button(row1, text="👤 Tài khoản", command=self._show_users).pack(side=tk.LEFT)
        ttk.Button(row1, text="💾 Sao lưu CSDL", command=self._backup_now).pack(side=tk.LEFT, padx=4)
        row2 = ttk.Frame(panel)
        row2.pack(fill=tk.X, pady=3)
        ttk.Button(row2, text="Xuất sự kiện CSV", command=self._export_csv).pack(side=tk.LEFT)
        ttk.Button(row2, text="Xuất lượt gửi CSV", command=self._export_visits_csv).pack(side=tk.LEFT, padx=4)
        row3 = ttk.Frame(panel)
        row3.pack(fill=tk.X, pady=3)
        ttk.Label(row3, text="Giữ dữ liệu (ngày)").pack(side=tk.LEFT)
        self.retention_var = tk.IntVar(value=self.app_config.retention_days)
        ttk.Spinbox(
            row3, from_=0, to=3650, increment=30, width=7, textvariable=self.retention_var
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(row3, text="🧹 Dọn dữ liệu cũ", command=self._purge_now).pack(side=tk.LEFT, padx=4)
        ttk.Label(
            panel,
            text="0 = giữ mãi. Khác 0: mỗi lần mở app sẽ tự xóa sự kiện, lượt và ảnh cũ hơn số ngày này "
                 "(xe đang trong bãi luôn được giữ lại).",
            foreground=self._palette["muted"],
            wraplength=360,
        ).pack(anchor=tk.W, pady=(2, 0))
        ttk.Button(
            panel, text="🗑 Xóa toàn bộ dữ liệu nhận dạng", command=self._clear_saved_plates
        ).pack(anchor=tk.W, pady=(6, 0))

    def _save_recognition_settings(self) -> None:
        self._save_settings()
        self.status_var.set(
            f"Đã lưu: vùng quét {self.roi_width_var.get()}x{self.roi_height_var.get()}%, "
            f"chu kỳ {self.detection_interval_var.get():g}s"
        )

    def _apply_gate_rules(self) -> None:
        self.app_config.open_gate_policy = self.policy_var.get()
        self.app_config.parking_flat_fee = max(0.0, self.flat_fee_var.get())
        self.app_config.parking_hourly_fee = max(0.0, self.hourly_fee_var.get())
        self.app_config.parking_free_minutes = max(0, int(self.free_min_var.get()))
        self.app_config.parking_capacity = max(0, int(self.capacity_var.get()))
        self.app_config.parking_daily_cap = max(0.0, self.daily_cap_var.get())
        self.app_config.parking_overnight_fee = max(0.0, self.overnight_var.get())
        self.app_config.parking_night_hour = max(0, min(23, int(self.night_hour_var.get())))
        self.app_config.default_vehicle_type = self.default_type_var.get()
        overrides: dict[str, dict] = {}
        for vehicle_type, (flat, hourly, cap) in self.type_fee_vars.items():
            values = {
                "flat_fee": max(0.0, flat.get()),
                "hourly_fee": max(0.0, hourly.get()),
                "daily_cap": max(0.0, cap.get()),
            }
            if any(values.values()):
                overrides[vehicle_type] = values
        self.app_config.parking_tariffs = overrides
        self.event_store.configure_rules(
            self.app_config.open_gate_policy,
            self.app_config.tariff(),
            tariff_table=self.app_config.tariff_table(),
            default_vehicle_type=self.app_config.default_vehicle_type,
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
        existing = self.event_store.find_vehicle(plate)
        try:
            self.event_store.upsert_vehicle(
                RegisteredVehicle(
                    plate=plate,
                    owner_name=self.veh_owner_var.get().strip(),
                    access=self.veh_access_var.get(),
                    vehicle_type=self.veh_type_var.get(),
                    phone=self.veh_phone_var.get().strip(),
                    # Editing details must not silently drop an existing pass.
                    valid_from=existing.valid_from if existing else None,
                    valid_until=existing.valid_until if existing else None,
                    note=existing.note if existing else "",
                )
            )
        except ValueError as exc:
            messagebox.showerror("Đăng ký xe", str(exc))
            return
        self.veh_plate_var.set("")
        self.veh_owner_var.set("")
        self.veh_phone_var.set("")
        self._refresh_vehicle_list()
        self.status_var.set(f"Đã lưu xe {plate}")

    def _sync_subscription_price(self, _event=None) -> None:
        """Suggest the price of the pass for the class and number of months."""
        months = max(1, int(self.sub_months_var.get() or 1))
        self.sub_amount_var.set(self.app_config.monthly_fee(self.veh_type_var.get()) * months)

    def _sell_subscription(self) -> None:
        plate = self.veh_plate_var.get().strip()
        if not plate:
            messagebox.showinfo("Vé tháng", "Nhập biển số xe mua vé.")
            return
        user = self.current_user.username if self.current_user else None
        months = max(1, int(self.sub_months_var.get() or 1))
        try:
            vehicle = self.event_store.add_subscription(
                plate,
                months=months,
                amount=max(0.0, float(self.sub_amount_var.get() or 0)),
                vehicle_type=self.veh_type_var.get(),
                owner_name=self.veh_owner_var.get().strip(),
                phone=self.veh_phone_var.get().strip(),
                username=user,
                shift_id=self.active_shift["id"] if self.active_shift else None,
            )
        except ValueError as exc:
            messagebox.showerror("Vé tháng", str(exc))
            return
        self._refresh_vehicle_list()
        messagebox.showinfo(
            "Vé tháng",
            f"{vehicle.plate}: {months} tháng, thu {self._money(self.sub_amount_var.get())}đ.\n"
            f"Hạn mới: {vehicle.valid_until}",
        )
        self.status_var.set(f"Đã bán vé tháng {vehicle.plate} đến {vehicle.valid_until}")

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
            self.veh_type_var.set(vehicle.vehicle_type)
            self.veh_phone_var.set(vehicle.phone)
            self._sync_subscription_price()

    def _refresh_vehicle_list(self) -> None:
        self.vehicle_tree.delete(*self.vehicle_tree.get_children())
        today = date.today()
        for vehicle in self.event_store.list_vehicles():
            days_left = vehicle.days_left(today)
            tags = [vehicle.access]
            if vehicle.access == ALLOW and days_left is not None:
                if days_left < 0:
                    tags.append("EXPIRED")
                elif days_left <= 7:
                    tags.append("EXPIRING")
            if days_left is None:
                left = "Không hạn"
            elif days_left < 0:
                left = f"Hết hạn {-days_left} ngày"
            else:
                left = f"{days_left} ngày"
            self.vehicle_tree.insert(
                "",
                tk.END,
                iid=vehicle.plate,
                values=(
                    vehicle.plate,
                    vehicle.owner_name or "-",
                    vehicle.phone or "-",
                    VEHICLE_TYPE_LABELS.get(vehicle.vehicle_type, vehicle.vehicle_type),
                    vehicle.access,
                    vehicle.valid_until or "-",
                    left,
                ),
                tags=tuple(tags),
            )
        expiring = self.event_store.expiring_vehicles(days=7, today=today)
        if expiring:
            plates = ", ".join(item.plate for item in expiring[:6])
            more = "…" if len(expiring) > 6 else ""
            self.expiring_var.set(
                f"⚠ {len(expiring)} vé tháng sắp hết hạn hoặc đã hết hạn: {plates}{more}"
            )
        else:
            self.expiring_var.set("")

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
                loop_video=self.sample_loop_var.get(),
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
            loop_video=self.sample_loop_var.get(),
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
            loop_video=self.sample_loop_var.get(),
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
            loop_video=self.sample_loop_var.get(),
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
            self.camera_loop_var.set(camera.loop_video)

    def _set_selected_direction(self) -> None:
        selected = set(self.source_tree.selection())
        if not selected:
            messagebox.showinfo("Direction", "Select at least one camera first.")
            return
        direction = self.camera_direction_var.get().upper()
        start_delay = max(0.0, self.camera_delay_var.get())
        loop_video = bool(self.camera_loop_var.get())
        for camera in self.app_config.cameras:
            if camera.id in selected:
                camera.direction = direction
                camera.start_delay_seconds = start_delay
                camera.loop_video = loop_video
        self._save_settings()
        self._refresh_source_tree()
        self._rebuild_camera_grid()
        self.status_var.set(
            f"Đã cập nhật {direction}, trễ {start_delay:g}s, "
            f"lặp video: {'Có' if loop_video else 'Không'}"
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
                    "Có" if camera.loop_video else "Không",
                    camera.name,
                    camera.uri,
                ),
            )

    @staticmethod
    def _panel_title(camera) -> str:
        loop = " · LẶP" if camera.loop_video and Path(camera.uri).is_file() else ""
        if camera.start_delay_seconds:
            return f"[{camera.direction} +{camera.start_delay_seconds:g}s{loop}] {camera.name}"
        return f"[{camera.direction}{loop}] {camera.name}"

    def _rebuild_camera_grid(self) -> None:
        for child in self.camera_grid.winfo_children():
            child.destroy()
        for column in range(2):
            self.camera_grid.columnconfigure(column, weight=0, minsize=0, uniform="")
        for row in range(getattr(self, "_camera_grid_rows", 1)):
            self.camera_grid.rowconfigure(row, weight=0, minsize=0, uniform="")
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
        self._camera_grid_rows = rows
        # uniform groups force every column/row to the same size regardless of
        # the video resolution shown inside, so all camera panels stay equal.
        for column in range(columns):
            self.camera_grid.columnconfigure(column, weight=1, minsize=300, uniform="cam")
        for row in range(rows):
            self.camera_grid.rowconfigure(row, weight=1, minsize=230, uniform="cam")
        if not cameras:
            ttk.Label(
                self.camera_grid,
                text="Add a video, webcam index (0), or RTSP camera to begin.",
                anchor=tk.CENTER,
            ).grid(row=0, column=0, sticky="nsew")
        self._bind_canvas_mousewheel(self.camera_canvas, self.camera_canvas)
        self._bind_canvas_mousewheel(self.camera_grid, self.camera_canvas)
        self.camera_grid.after_idle(self.camera_grid._sync_scroll_region)

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
                        for event in item.new_events:
                            self._maybe_open_exit_payment(event)
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

    def _maybe_open_exit_payment(self, event) -> None:
        """Show collection QR as soon as an OUT event completes a paid visit."""
        if event.direction != "OUT" or not event.visit_id:
            return
        detail = self.event_store.visit_detail(int(event.visit_id))
        if not detail:
            return
        if (
            detail.get("status") != "COMPLETED"
            or detail.get("payment_status") != "UNPAID"
            or float(detail.get("fee") or 0) <= 0
        ):
            return
        self._show_qr_window(
            int(detail["id"]), float(detail["fee"]), str(detail["plate"])
        )

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
        width = label.winfo_width() - 8
        height = label.winfo_height() - 8
        if width < 16 or height < 16:
            return
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
        inside = self.event_store.latest_visits(limit=100, status="INSIDE")
        capacity = int(self.app_config.parking_capacity or 0)
        self.inside_count_var.set(
            f"{len(inside)} xe" + (f" / {capacity} chỗ" if capacity else "")
        )
        for visit in inside:
            self.inside_tree.insert(
                "",
                tk.END,
                values=(
                    visit.plate,
                    VEHICLE_TYPE_LABELS.get(visit.vehicle_type, visit.vehicle_type),
                    self._clock(visit.entry_at),
                    visit.entry_camera_name or "-",
                ),
                tags=("IN",),
            )

        # Keep the operator's selection across a refresh: they usually collect
        # money right after looking at the row.
        previous = set(self.visit_tree.selection())
        self.visit_tree.delete(*self.visit_tree.get_children())
        for visit in self.event_store.latest_visits(limit=100):
            tags = [visit.status]
            if visit.review_flag and visit.status != "REVIEW":
                tags.append("FLAGGED")
            self.visit_tree.insert(
                "",
                tk.END,
                iid=str(visit.id),
                values=(
                    visit.plate,
                    VEHICLE_TYPE_LABELS.get(visit.vehicle_type, visit.vehicle_type),
                    self._clock(visit.entry_at),
                    self._clock(visit.exit_at),
                    self._duration(visit.duration_seconds),
                    self._money(visit.fee),
                    self._PAY_LABELS.get(visit.payment_status, "-") if visit.status == "COMPLETED" else "-",
                    self._flag_label(visit.review_flag),
                ),
                tags=tuple(tags),
            )
        still_there = [iid for iid in previous if self.visit_tree.exists(iid)]
        if still_there:
            self.visit_tree.selection_set(still_there)
        self._refresh_revenue()

    _PAY_LABELS = {"PAID": "✓ Đã thu", "UNPAID": "Chưa thanh toán", "EXEMPT": "Miễn"}

    _FLAG_LABELS = {
        "low_confidence": "Đọc biển yếu",
        "short_stay": "Gửi quá ngắn",
        "manual": "Ghi tay",
        "no_entry": "Không có lượt vào",
    }

    @classmethod
    def _flag_label(cls, review_flag: str | None) -> str:
        flags = [item for item in (review_flag or "").split(",") if item]
        if not flags:
            return "✓"
        return "⚠ " + ", ".join(cls._FLAG_LABELS.get(flag, flag) for flag in flags)

    def _collect_payment(self, method: str) -> None:
        selected = self.visit_tree.selection()
        if not selected:
            messagebox.showinfo("Thu tiền", "Chọn một lượt đã hoàn tất trong danh sách.")
            return
        visits = {str(visit.id): visit for visit in self.event_store.latest_visits(limit=500)}
        user = self.current_user.username if self.current_user else None
        if method == "QR":
            visit = visits.get(selected[0])
            problem = self._payable_problem(visit)
            if problem:
                messagebox.showinfo("Thu tiền", problem)
                return
            self._show_qr_window(visit.id, visit.fee or 0, visit.plate)
            return
        if method == "MOMO":
            visit = visits.get(selected[0])
            problem = self._payable_problem(visit)
            if problem:
                messagebox.showinfo("Thu tiền", problem)
                return
            self._show_momo_window(visit.id, visit.fee or 0, visit.plate)
            return
        paid = 0
        skipped = 0
        shift_id = self.active_shift["id"] if self.active_shift else None
        for iid in selected:
            visit = visits.get(iid)
            if self._payable_problem(visit):
                skipped += 1
                continue
            paid += self.event_store.mark_paid(
                int(iid), method, username=user, shift_id=shift_id
            )
            self.event_store.write_audit(user, "PAYMENT_CASH", f"{visit.plate} {int(visit.fee or 0)}")
        self._refresh_visit_views()
        note = f" · bỏ qua {skipped} lượt không phải thu" if skipped else ""
        self.status_var.set(f"Đã thu {paid} lượt bằng tiền mặt{note}")

    @staticmethod
    def _payable_problem(visit) -> str:
        """Why this visit cannot be collected ('' when it can) — guards against
        charging the same visit twice or charging a free one."""
        if visit is None or visit.status != "COMPLETED":
            return "Chọn một lượt đã hoàn tất (xe đã ra khỏi bãi)."
        if visit.payment_status == "PAID":
            return f"Lượt {visit.plate} đã thu tiền rồi."
        if visit.payment_status == "EXEMPT" or not visit.fee:
            return f"Lượt {visit.plate} không mất phí (xe đăng ký hoặc phí bằng 0)."
        return ""

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

    # --- Reconciliation, manual overrides, shifts ---

    def _selected_visit(self, message: str = "Chọn một lượt trong danh sách."):
        selected = self.visit_tree.selection()
        if not selected:
            messagebox.showinfo("Lượt gửi xe", message)
            return None
        visit_id = int(selected[0])
        for visit in self.event_store.latest_visits(limit=500):
            if visit.id == visit_id:
                return visit
        return None

    def _snapshot_photo(self, path: str | None, width: int = 380, height: int = 260):
        """Load a snapshot scaled to fit the review window, or None when missing."""
        if not path:
            return None
        file_path = Path(path)
        if not file_path.exists():
            return None
        image = Image.open(file_path)
        image.thumbnail((width, height))
        return ImageTk.PhotoImage(image)

    def _show_visit_review(self) -> None:
        """Side-by-side entry/exit photos so the operator can spot a swapped vehicle."""
        visit = self._selected_visit("Chọn một lượt để đối soát.")
        if visit is None:
            return
        detail = self.event_store.visit_detail(visit.id)
        if detail is None:
            return

        window = tk.Toplevel(self)
        window.title(f"Đối soát lượt {visit.plate}")
        window.transient(self)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        header = ttk.Label(
            frame,
            text=f"{visit.plate}   ·   {VEHICLE_TYPE_LABELS.get(visit.vehicle_type, visit.vehicle_type)}"
                 f"   ·   {self._duration(visit.duration_seconds)}"
                 f"   ·   Phí {self._money(visit.fee)}đ",
            style="Heading.TLabel",
        )
        header.pack(anchor=tk.W)
        flags = self._flag_label(visit.review_flag)
        ttk.Label(
            frame,
            text="Cảnh báo: " + flags if flags != "✓" else "Không có cảnh báo tự động.",
            foreground=self._palette["danger"] if flags != "✓" else self._palette["muted"],
        ).pack(anchor=tk.W, pady=(2, 8))

        photos = ttk.Frame(frame)
        photos.pack(fill=tk.BOTH, expand=True)
        window._photos = []  # keep the PhotoImage references alive
        for index, (key, title) in enumerate((("entry_event", "XE VÀO"), ("exit_event", "XE RA"))):
            event = detail.get(key)
            card = ttk.LabelFrame(photos, text=title, padding=6)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            photos.columnconfigure(index, weight=1)
            if event is None:
                ttk.Label(card, text="Chưa có dữ liệu", foreground=self._palette["muted"]).pack(
                    padx=40, pady=60
                )
                continue
            photo = self._snapshot_photo(event.get("snapshot_path"))
            if photo is None:
                ttk.Label(
                    card, text="Không có ảnh (ghi thủ công)", foreground=self._palette["muted"]
                ).pack(padx=30, pady=60)
            else:
                window._photos.append(photo)
                ttk.Label(card, image=photo).pack()
            ttk.Label(
                card,
                text=f"{self._clock(event.get('detected_at'))} · {event.get('camera_name', '-')}\n"
                     f"Biển đọc được: {event.get('plate')} "
                     f"({float(event.get('confidence') or 0) * 100:.0f}%)",
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(4, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(10, 0))

        def confirm() -> None:
            user = self.current_user.username if self.current_user else None
            self.event_store.clear_visit_flag(visit.id, username=user)
            self._refresh_visit_views()
            window.destroy()
            self.status_var.set(f"Đã xác nhận lượt {visit.plate}")

        ttk.Button(actions, text="✓ Đúng xe, bỏ cảnh báo", command=confirm, style="Accent.TButton").pack(
            side=tk.LEFT
        )
        ttk.Button(actions, text="Đóng", command=window.destroy).pack(side=tk.RIGHT)

    def _correct_visit_plate(self) -> None:
        visit = self._selected_visit("Chọn lượt cần sửa biển số.")
        if visit is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Sửa biển số")
        dialog.transient(self)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"Biển đang lưu: {visit.plate}").pack(anchor=tk.W)
        value = tk.StringVar(value=visit.plate)
        entry = ttk.Entry(body, textvariable=value, width=18)
        entry.pack(anchor=tk.W, pady=8)
        entry.focus_set()

        def apply() -> None:
            user = self.current_user.username if self.current_user else None
            try:
                plate = self.event_store.update_visit_plate(visit.id, value.get(), username=user)
            except ValueError as exc:
                messagebox.showerror("Sửa biển số", str(exc), parent=dialog)
                return
            dialog.destroy()
            self._refresh_visit_views()
            self._load_latest_events()
            self.status_var.set(f"Đã sửa biển {visit.plate} → {plate}")

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Lưu", command=apply, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(buttons, text="Hủy", command=dialog.destroy).pack(side=tk.LEFT, padx=6)

    def _retype_visit(self) -> None:
        visit = self._selected_visit("Chọn lượt cần đổi loại xe.")
        if visit is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Đổi loại xe")
        dialog.transient(self)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"Lượt {visit.plate}").pack(anchor=tk.W)
        value = tk.StringVar(value=visit.vehicle_type)
        ttk.Combobox(
            body, textvariable=value, values=VEHICLE_TYPES, state="readonly", width=12
        ).pack(anchor=tk.W, pady=8)
        ttk.Label(
            body,
            text="Phí sẽ được tính lại theo biểu phí của loại xe (trừ lượt đã thu tiền).",
            foreground=self._palette["muted"],
            wraplength=280,
        ).pack(anchor=tk.W, pady=(0, 8))

        def apply() -> None:
            user = self.current_user.username if self.current_user else None
            fee = self.event_store.set_visit_vehicle_type(visit.id, value.get(), username=user)
            dialog.destroy()
            self._refresh_visit_views()
            self.status_var.set(f"Lượt {visit.plate}: {value.get()} · phí {self._money(fee)}đ")

        ttk.Button(body, text="Áp dụng", command=apply, style="Accent.TButton").pack(anchor=tk.W)

    def _record_manual_event(self, direction: str) -> None:
        plate = self.manual_plate_var.get().strip()
        if not plate:
            messagebox.showinfo("Ghi thủ công", "Nhập biển số cần ghi.")
            return
        user = self.current_user.username if self.current_user else None
        try:
            event = self.event_store.record_manual(
                plate,
                direction,
                username=user,
                vehicle_type=self.manual_type_var.get(),
                note="ghi tay",
            )
        except ValueError as exc:
            messagebox.showerror("Ghi thủ công", str(exc))
            return
        self.manual_plate_var.set("")
        self._events_total += 1
        self._insert_event(event, top=True)
        self._refresh_visit_views()
        self._maybe_open_exit_payment(event)
        self._update_stats()
        self.status_var.set(
            f"Đã ghi tay xe {event.plate} {'vào' if event.direction == 'IN' else 'ra'}"
        )

    def _open_gate_manually(self) -> None:
        """Let the guard raise the barrier when the camera cannot decide."""
        self.gate.open("manual", plate="")
        user = self.current_user.username if self.current_user else None
        self.event_store.write_audit(user, "GATE_MANUAL", "mở cổng thủ công")
        self.status_var.set("Đã mở barrier thủ công")
        self._update_stats()

    def _close_gate_manually(self) -> None:
        """Lower the simulated barrier and send CLOSE to configured hardware."""
        self.gate.close("manual")
        user = self.current_user.username if self.current_user else None
        self.event_store.write_audit(user, "GATE_MANUAL_CLOSE", "đóng cổng thủ công")
        self.status_var.set("Đã đóng barrier thủ công")
        self._update_stats()

    def _refresh_shift_widgets(self) -> None:
        if self.active_shift:
            opened = self._clock(self.active_shift["opened_at"])
            who = self.active_shift["username"] or "-"
            self.shift_label_var.set(f"Ca: {who} từ {opened}")
            self.shift_button.configure(text="🕒  Đóng ca")
        else:
            self.shift_label_var.set("Chưa mở ca")
            self.shift_button.configure(text="🕒  Mở ca")

    def _toggle_shift(self) -> None:
        if self.active_shift:
            self._close_shift_dialog()
        else:
            self._open_shift_dialog()

    def _open_shift_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Mở ca trực")
        dialog.transient(self)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Tiền mặt đầu ca (quỹ lẻ)").pack(anchor=tk.W)
        opening = tk.DoubleVar(value=0.0)
        ttk.Spinbox(
            body, from_=0, to=100_000_000, increment=50_000, width=14, textvariable=opening
        ).pack(anchor=tk.W, pady=8)

        def apply() -> None:
            user = self.current_user.username if self.current_user else "(chưa đăng nhập)"
            try:
                self.active_shift = self.event_store.open_shift(user, opening.get())
            except ValueError as exc:
                messagebox.showerror("Ca trực", str(exc), parent=dialog)
                return
            dialog.destroy()
            self._refresh_shift_widgets()
            self.status_var.set(f"Đã mở ca cho {user}")

        ttk.Button(body, text="Mở ca", command=apply, style="Accent.TButton").pack(anchor=tk.W)

    def _close_shift_dialog(self) -> None:
        shift = self.active_shift
        if not shift:
            return
        totals = self.event_store.shift_totals(shift["id"])
        dialog = tk.Toplevel(self)
        dialog.title("Đóng ca & đối soát tiền")
        dialog.transient(self)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        lines = (
            ("Quỹ đầu ca", totals["opening_cash"]),
            (f"Tiền mặt vé lượt ({totals['cash_visits']} lượt)", totals["cash_total"]),
            (f"Vé tháng ({totals['subscription_tickets']} vé)", totals["subscription_total"]),
            (f"Chuyển khoản/QR ({totals['qr_visits']} lượt)", totals["qr_total"]),
        )
        for label, amount in lines:
            row = ttk.Frame(body)
            row.pack(fill=tk.X)
            ttk.Label(row, text=label).pack(side=tk.LEFT)
            ttk.Label(row, text=f"{self._money(amount)}đ").pack(side=tk.RIGHT)
        separator = ttk.Frame(body, height=1)
        separator.pack(fill=tk.X, pady=6)
        expected_row = ttk.Frame(body)
        expected_row.pack(fill=tk.X)
        ttk.Label(expected_row, text="Tiền mặt phải có", style="Heading.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            expected_row, text=f"{self._money(totals['expected_cash'])}đ", style="Heading.TLabel"
        ).pack(side=tk.RIGHT)

        ttk.Label(body, text="Tiền mặt đếm được").pack(anchor=tk.W, pady=(10, 0))
        counted = tk.DoubleVar(value=totals["expected_cash"])
        ttk.Spinbox(
            body, from_=0, to=1_000_000_000, increment=10_000, width=16, textvariable=counted
        ).pack(anchor=tk.W, pady=4)
        ttk.Label(body, text="Ghi chú").pack(anchor=tk.W)
        note = tk.StringVar()
        ttk.Entry(body, textvariable=note, width=34).pack(anchor=tk.W, pady=4)

        def apply() -> None:
            user = self.current_user.username if self.current_user else None
            result = self.event_store.close_shift(
                shift["id"], counted.get(), note.get(), username=user
            )
            self.active_shift = None
            dialog.destroy()
            self._refresh_shift_widgets()
            difference = result["difference"]
            verdict = "khớp" if abs(difference) < 1 else f"lệch {self._money(difference)}đ"
            messagebox.showinfo(
                "Đóng ca",
                f"Phải có {self._money(result['expected_cash'])}đ · "
                f"đếm {self._money(counted.get())}đ → {verdict}",
            )
            self.status_var.set(f"Đã đóng ca · {verdict}")

        ttk.Button(body, text="Đóng ca", command=apply, style="Accent.TButton").pack(
            anchor=tk.W, pady=(8, 0)
        )

    # --- Automatic payment confirmation ---

    def _start_bank_feed(self) -> None:
        """Poll the bank feed in the background and settle matching visits."""
        self._bank_feed = build_feed(self.app_config)
        if self._bank_feed is None:
            self._set_feed_status("Tắt — chưa khai báo dịch vụ đối soát")
            return
        self._set_feed_status("Đang khởi động…")
        self._bank_stop.clear()
        self._bank_thread = threading.Thread(
            target=self._bank_feed_loop, name="bank-feed", daemon=True
        )
        self._bank_thread.start()

    def _stop_bank_feed(self) -> None:
        self._bank_stop.set()
        thread = self._bank_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self._bank_thread = None

    def _bank_feed_loop(self) -> None:
        feed = self._bank_feed
        delay = max(5.0, float(self.app_config.payment_poll_seconds or 20))
        since_id = self.event_store.get_state("bank_feed_since_id") or None
        while not self._bank_stop.is_set():
            try:
                transactions = feed.fetch(since_id=since_id)
                error = ""
            except FeedError as exc:
                transactions, error = [], str(exc)
            except Exception as exc:  # a broken feed must never kill the app
                transactions, error = [], f"Lỗi đối soát: {exc}"
            if transactions:
                newest = max(transactions, key=lambda item: str(item.id))
                since_id = newest.id
                self.event_store.set_state("bank_feed_since_id", since_id)
            # Matching and all UI work happen on the Tk thread.
            self.after(0, self._apply_bank_transactions, transactions, error)
            self._bank_stop.wait(delay)

    def _apply_bank_transactions(self, transactions, error: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        if error:
            self._set_feed_status(f"⚠ {error} (lúc {stamp})")
            return
        provider = getattr(self._bank_feed, "name", "bank")
        transactions = [
            transaction
            for transaction in transactions
            if not self.event_store.bank_transaction_processed(provider, transaction.id)
        ]
        pending = self.event_store.pending_payments()
        requested_at = self.event_store.bank_payment_requests()
        matches = match_requested(transactions, pending, requested_at, tolerance=0.0)
        settled = 0
        for visit_id, transaction in matches:
            rows = self.event_store.mark_paid(
                visit_id,
                "BANK",
                username="(tự động)",
                shift_id=self.active_shift["id"] if self.active_shift else None,
                reference=transaction.reference or str(transaction.id),
            )
            if not rows:
                continue
            settled += 1
            self.event_store.write_audit(
                "(tự động)", "PAYMENT_BANK",
                f"visit {visit_id} {int(transaction.amount)} {transaction.reference}",
            )
            self.event_store.record_bank_transaction(provider, transaction.id)
            if visit_id in self._open_payment_visits:
                self._paid_visit_ids.add(visit_id)
        if settled:
            self._refresh_visit_views()
            self.status_var.set(f"✅ Tự động xác nhận {settled} lượt đã chuyển khoản")
        self._set_feed_status(
            f"✓ Đang theo dõi · {len(pending)} lượt chờ thu · cập nhật {stamp}"
        )

    def _set_feed_status(self, text: str) -> None:
        self._feed_status = text
        if hasattr(self, "feed_status_var"):
            self.feed_status_var.set(text)

    # --- Data retention ---

    def _apply_retention(self) -> None:
        """Trim old events on startup so a booth PC never fills its disk."""
        days = int(self.app_config.retention_days or 0)
        if days <= 0:
            return
        result = self.event_store.purge_older_than(days)
        if result["events"]:
            self.status_var.set(
                f"Đã dọn {result['events']} sự kiện cũ hơn {days} ngày "
                f"({result['snapshots']} ảnh)"
            )

    def _purge_now(self) -> None:
        days = max(0, int(self.retention_var.get()))
        self.app_config.retention_days = days
        save_config(self.app_config, self.config_path)
        if days <= 0:
            messagebox.showinfo("Dọn dữ liệu", "Đặt số ngày lớn hơn 0 để bật tự động dọn.")
            return
        if not self._require_admin("dọn dữ liệu cũ"):
            return
        if not messagebox.askyesno(
            "Dọn dữ liệu", f"Xóa vĩnh viễn sự kiện và ảnh cũ hơn {days} ngày?"
        ):
            return
        user = self.current_user.username if self.current_user else None
        result = self.event_store.purge_older_than(days, username=user)
        self._events_total = self.event_store.count_events()
        self._load_latest_events()
        self._refresh_visit_views()
        self._update_stats()
        messagebox.showinfo(
            "Dọn dữ liệu",
            f"Đã xóa {result['visits']} lượt, {result['events']} sự kiện, "
            f"{result['snapshots']} ảnh.",
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
        window.minsize(380, 300)
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = self._make_scrollable_tree(
            frame, columns=("user", "role", "active"), height=11
        )
        for column, label, width in (("user", "Tài khoản", 160), ("role", "Vai trò", 90), ("active", "Hoạt động", 90)):
            tree.heading(column, text=label)
            tree.column(
                column, width=width, minwidth=max(55, int(width * 0.65)),
                anchor=tk.CENTER, stretch=True,
            )

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

    # --- Reports ---

    def _chart_card(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        card = ttk.LabelFrame(parent, text=title, padding=6)
        return card

    def _build_report_page(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X)
        controls.columnconfigure(0, weight=1)
        range_controls = ttk.Frame(controls)
        range_controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(range_controls, text="Từ ngày").pack(side=tk.LEFT)
        self.report_start_var = tk.StringVar()
        ttk.Entry(range_controls, textvariable=self.report_start_var, width=11).pack(
            side=tk.LEFT, padx=(4, 8)
        )
        ttk.Label(range_controls, text="đến").pack(side=tk.LEFT)
        self.report_end_var = tk.StringVar()
        ttk.Entry(range_controls, textvariable=self.report_end_var, width=11).pack(
            side=tk.LEFT, padx=(4, 12)
        )
        for text, factory in (
            ("Hôm nay", analytics.DateRange.today_only),
            ("7 ngày", lambda: analytics.DateRange.last_days(7)),
            ("30 ngày", lambda: analytics.DateRange.last_days(30)),
            ("Tháng này", analytics.DateRange.this_month),
        ):
            ttk.Button(
                range_controls,
                text=text,
                command=lambda chosen=factory: self._set_report_range(chosen()),
            ).pack(side=tk.LEFT, padx=2)

        report_actions = ttk.Frame(controls)
        report_actions.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(
            report_actions, text="🔄 Cập nhật", command=self._refresh_report,
            style="Accent.TButton",
        ).pack(side=tk.LEFT, padx=(0, 2))
        export_button = ttk.Menubutton(report_actions, text="⬇ Xuất báo cáo")
        export_menu = tk.Menu(export_button, tearoff=False)
        export_menu.add_command(label="Xuất CSV", command=lambda: self._export_report("csv"))
        export_menu.add_command(label="Xuất PDF", command=lambda: self._export_report("pdf"))
        export_button.configure(menu=export_menu)
        export_button.pack(side=tk.LEFT, padx=2)
        self.report_range_label = ttk.Label(
            report_actions, text="", foreground=self._palette["muted"]
        )
        self.report_range_label.pack(side=tk.RIGHT)

        tiles = ttk.Frame(parent)
        tiles.pack(fill=tk.X, pady=(10, 2))
        self.report_tile_frame = tiles
        self.report_tiles: dict[str, StatTile] = {}
        self.report_tile_widgets: list[StatTile] = []
        for index, (key, title, accent) in enumerate(
            (
                ("revenue", "Doanh thu đã thu", "#1f6fe0"),
                ("unpaid", "Chưa thu được", "#e05260"),
                ("visits", "Lượt xe", "#1f2933"),
                ("duration", "Thời gian gửi TB", "#1f2933"),
                ("occupancy", "Đỉnh xe trong bãi", "#1f6fe0"),
                ("quality", "Chất lượng nhận dạng", "#2f9e6b"),
            )
        ):
            tile = StatTile(tiles, title, accent=accent)
            self.report_tiles[key] = tile
            self.report_tile_widgets.append(tile)
        self._report_tile_columns = 0
        self._reflow_report_tiles()
        tiles.bind("<Configure>", self._reflow_report_tiles)

        sub = ttk.Notebook(parent)
        sub.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        revenue_tab = ttk.Frame(sub, padding=8)
        traffic_tab = ttk.Frame(sub, padding=8)
        detail_tab = ttk.Frame(sub, padding=8)
        sub.add(revenue_tab, text="  Doanh thu  ")
        sub.add(traffic_tab, text="  Lưu lượng & thời gian  ")
        sub.add(detail_tab, text="  Chi tiết & đối soát  ")

        # --- Doanh thu ---
        day_card = self._chart_card(revenue_tab, "Doanh thu theo ngày")
        day_card.pack(fill=tk.BOTH, expand=True)
        self.revenue_chart = BarChart(day_card, height=210, value_format=self._short_money)
        self.revenue_chart.pack(fill=tk.BOTH, expand=True)

        bottom = ttk.Frame(revenue_tab)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)
        bottom.rowconfigure(0, weight=1)
        mix_card = self._chart_card(bottom, "Hình thức thanh toán")
        mix_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.payment_chart = HBarChart(mix_card, height=150, value_format=self._short_money)
        self.payment_chart.pack(fill=tk.BOTH, expand=True)

        shift_card = self._chart_card(bottom, "Đối soát theo nhân viên")
        shift_card.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.collection_tree = self._make_scrollable_tree(
            shift_card, columns=("user", "method", "visits", "total"), height=5
        )
        for column, label, width in (
            ("user", "Tài khoản", 120),
            ("method", "Hình thức", 80),
            ("visits", "Số lượt", 70),
            ("total", "Số tiền", 100),
        ):
            self.collection_tree.heading(column, text=label)
            self.collection_tree.column(
                column, width=width, minwidth=max(55, int(width * 0.65)),
                anchor=tk.CENTER, stretch=True,
            )

        # --- Lưu lượng ---
        hour_card = self._chart_card(traffic_tab, "Lưu lượng theo giờ trong ngày")
        hour_card.pack(fill=tk.BOTH, expand=True)
        self.hour_chart = BarChart(hour_card, height=200, value_format=lambda value: f"{value:.0f}")
        self.hour_chart.pack(fill=tk.BOTH, expand=True)

        traffic_bottom = ttk.Frame(traffic_tab)
        traffic_bottom.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        traffic_bottom.columnconfigure(0, weight=1)
        traffic_bottom.columnconfigure(1, weight=1)
        traffic_bottom.rowconfigure(0, weight=1)
        weekday_card = self._chart_card(traffic_bottom, "Lượt vào theo thứ")
        weekday_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.weekday_chart = BarChart(
            weekday_card, height=160, value_format=lambda value: f"{value:.0f}", legend=False
        )
        self.weekday_chart.pack(fill=tk.BOTH, expand=True)

        duration_card = self._chart_card(traffic_bottom, "Thời gian gửi xe")
        duration_card.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.duration_chart = HBarChart(
            duration_card, height=160, value_format=lambda value: f"{value:.0f}", colour="#3ecf8e"
        )
        self.duration_chart.pack(fill=tk.BOTH, expand=True)

        # --- Chi tiết ---
        detail_host, detail_canvas, detail_content = self._make_scrollable_frame(detail_tab)
        detail_host.pack(fill=tk.BOTH, expand=True)
        detail_top = ttk.Frame(detail_content)
        detail_top.pack(fill=tk.X)
        detail_top.columnconfigure(0, weight=1)
        detail_top.columnconfigure(1, weight=1)
        detail_top.rowconfigure(0, weight=1)
        top_card = self._chart_card(detail_top, "Xe ra vào nhiều nhất")
        top_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.top_plate_tree = self._make_scrollable_tree(
            top_card, columns=("plate", "visits", "fee", "time"), height=8
        )
        for column, label, width in (
            ("plate", "Biển số", 100),
            ("visits", "Số lượt", 70),
            ("fee", "Tổng phí", 90),
            ("time", "Tổng thời gian", 110),
        ):
            self.top_plate_tree.heading(column, text=label)
            self.top_plate_tree.column(
                column, width=width, minwidth=max(55, int(width * 0.65)),
                anchor=tk.CENTER, stretch=True,
            )

        denied_card = self._chart_card(detail_top, "Lý do từ chối mở cổng")
        denied_card.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.denied_chart = HBarChart(
            denied_card, height=150, value_format=lambda value: f"{value:.0f}", colour="#e05260"
        )
        self.denied_chart.pack(fill=tk.BOTH, expand=True)

        shift_card = self._chart_card(detail_content, "Ca trực & đối soát tiền mặt")
        shift_card.pack(fill=tk.X, pady=(8, 0))
        self.shift_tree = self._make_scrollable_tree(
            shift_card,
            columns=("opened", "closed", "user", "expected", "counted", "difference", "note"),
            height=6,
        )
        for column, label, width in (
            ("opened", "Mở ca", 130),
            ("closed", "Đóng ca", 130),
            ("user", "Nhân viên", 100),
            ("expected", "Phải có", 90),
            ("counted", "Đếm được", 90),
            ("difference", "Lệch", 90),
            ("note", "Ghi chú", 160),
        ):
            self.shift_tree.heading(column, text=label)
            self.shift_tree.column(
                column, width=width, minwidth=max(60, int(width * 0.65)),
                anchor=tk.CENTER, stretch=True,
            )
        self.shift_tree.tag_configure("SHORT", background=self._palette["row_review"])
        self.shift_tree.tag_configure("OPEN", background=self._palette["row_in"])

        audit_card = self._chart_card(detail_content, "Nhật ký hệ thống")
        audit_card.pack(fill=tk.X, pady=(8, 0))
        self.audit_tree = self._make_scrollable_tree(
            audit_card, columns=("ts", "user", "action", "detail"), height=8
        )
        for column, label, width in (
            ("ts", "Thời gian", 150),
            ("user", "Tài khoản", 100),
            ("action", "Hành động", 120),
            ("detail", "Chi tiết", 220),
        ):
            self.audit_tree.heading(column, text=label)
            self.audit_tree.column(
                column, width=width, minwidth=max(70, int(width * 0.65)),
                anchor=tk.W, stretch=True,
            )

        # Keep page scrolling on the empty/card background; Treeviews retain
        # their own wheel behaviour so long tables can be scrolled independently.
        def scroll_detail(event) -> str:
            if event.delta:
                detail_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        def bind_detail_scroll(widget: tk.Misc) -> None:
            if isinstance(widget, (ttk.Treeview, ttk.Scrollbar)):
                return
            widget.bind("<MouseWheel>", scroll_detail)
            for child in widget.winfo_children():
                bind_detail_scroll(child)

        bind_detail_scroll(detail_canvas)

        self._set_report_range(analytics.DateRange.last_days(7), refresh=False)

    def _reflow_report_tiles(self, event=None) -> None:
        """Wrap KPI cards as the report page becomes narrower or wider."""
        width = event.width if event is not None else self.report_tile_frame.winfo_width()
        if width <= 1:
            columns = 3
        elif width >= 1180:
            columns = 6
        elif width >= 620:
            columns = 3
        else:
            columns = 2
        if columns == self._report_tile_columns:
            return
        self._report_tile_columns = columns
        for column in range(6):
            self.report_tile_frame.columnconfigure(column, weight=1 if column < columns else 0)
        for index, tile in enumerate(self.report_tile_widgets):
            row, column = divmod(index, columns)
            tile.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 6, 0),
                pady=(0 if row == 0 else 6, 0),
            )

    _DENY_REASON_LABELS = {
        "blacklist": "Danh sách đen",
        "not_registered": "Chưa đăng ký",
        "expired": "Hết hạn đăng ký",
    }

    @staticmethod
    def _amount(value: float | None) -> str:
        """Money for the report, where zero must read as 0 rather than '-'."""
        return f"{int(round(value or 0)):,}".replace(",", ".")

    @staticmethod
    def _short_money(amount: float) -> str:
        """Compact money for chart axes: 1.250.000 -> 1.25tr, 45.000 -> 45k."""
        if amount >= 1_000_000:
            return f"{amount / 1_000_000:.2f}tr".replace(".00tr", "tr")
        if amount >= 1_000:
            return f"{amount / 1_000:.0f}k"
        return f"{amount:.0f}"

    @staticmethod
    def _parse_report_date(text: str) -> date | None:
        text = text.strip()
        for parser in (date.fromisoformat, lambda value: datetime.strptime(value, "%d/%m/%Y").date()):
            try:
                return parser(text)
            except (ValueError, TypeError):
                continue
        return None

    def _set_report_range(self, span: analytics.DateRange, refresh: bool = True) -> None:
        self.report_start_var.set(span.start.strftime("%d/%m/%Y"))
        self.report_end_var.set(span.end.strftime("%d/%m/%Y"))
        if refresh:
            self._refresh_report()

    def _report_range(self) -> analytics.DateRange | None:
        start = self._parse_report_date(self.report_start_var.get())
        end = self._parse_report_date(self.report_end_var.get())
        if start is None or end is None:
            messagebox.showinfo("Báo cáo", "Ngày phải theo dạng dd/mm/yyyy.")
            return None
        if start > end:
            start, end = end, start
        return analytics.DateRange(start, end)

    def _refresh_report(self) -> None:
        span = self._report_range()
        if span is None:
            return
        capacity = int(self.app_config.parking_capacity or 0)
        summary = analytics.summarize(self.event_store, span, capacity=capacity)
        self.report_range_label.configure(text=f"{span.label()}  ·  {span.days} ngày")

        sales = analytics.subscription_sales(self.event_store, span)
        self.report_tiles["revenue"].set(
            f"{self._amount(summary.revenue_paid + sales['total'])}đ",
            f"{summary.paid_count} lượt ({self._amount(summary.revenue_paid)}đ) · "
            f"{sales['tickets']} vé tháng ({self._amount(sales['total'])}đ)",
        )
        self.report_tiles["unpaid"].set(
            f"{self._amount(summary.revenue_unpaid)}đ",
            f"{summary.unpaid_count} lượt chưa thu",
        )
        self.report_tiles["visits"].set(
            f"{summary.entries} vào / {summary.exits} ra",
            f"{summary.completed_visits} lượt hoàn tất · {summary.unique_plates} biển số",
        )
        peak = f"cao điểm {summary.peak_hour:02d}h" if summary.peak_hour is not None else "chưa có cao điểm"
        self.report_tiles["duration"].set(
            self._duration(summary.avg_duration_seconds),
            f"{peak} · lâu nhất {self._duration(summary.max_duration_seconds)}",
        )
        if capacity:
            occupancy_hint = (
                f"{summary.occupancy_rate:.0%} sức chứa · vòng quay {summary.turnover(span.days):.1f}"
            )
        else:
            occupancy_hint = f"đang trong bãi: {summary.currently_inside} xe"
        self.report_tiles["occupancy"].set(f"{summary.peak_occupancy} xe", occupancy_hint)
        self.report_tiles["quality"].set(
            f"{summary.avg_confidence:.0%}",
            f"{summary.low_confidence_events} lượt độ tin cậy thấp · {summary.review_visits} cần kiểm tra",
        )

        days = analytics.revenue_by_day(self.event_store, span)
        self.revenue_chart.set_data(
            [f"{row['day'][8:10]}/{row['day'][5:7]}" for row in days],
            [
                ("Đã thu", [row["paid_total"] for row in days]),
                ("Chưa thu", [row["unpaid_total"] for row in days]),
            ],
        )

        mix = analytics.payment_mix(self.event_store, span)
        self.payment_chart.set_data([(row["method"], row["total"]) for row in mix])

        self.collection_tree.delete(*self.collection_tree.get_children())
        for row in analytics.collections_by_user(self.event_store, span):
            self.collection_tree.insert(
                "",
                tk.END,
                values=(row["user"], row["method"], row["visits"], f"{self._amount(row['total'])}đ"),
            )

        hours = analytics.traffic_by_hour(self.event_store, span)
        self.hour_chart.set_data(
            [f"{row['hour']:02d}" for row in hours],
            [
                ("Vào", [row["entries"] for row in hours]),
                ("Ra", [row["exits"] for row in hours]),
            ],
        )

        weekdays = analytics.traffic_by_weekday(self.event_store, span)
        self.weekday_chart.set_data(
            [row["label"] for row in weekdays],
            [("Lượt vào", [row["entries"] for row in weekdays])],
        )

        self.duration_chart.set_data(
            [(row["label"], row["visits"]) for row in analytics.duration_histogram(self.event_store, span)]
        )

        self.top_plate_tree.delete(*self.top_plate_tree.get_children())
        for row in analytics.top_plates(self.event_store, span, limit=15):
            self.top_plate_tree.insert(
                "",
                tk.END,
                values=(
                    row["plate"],
                    row["visits"],
                    f"{self._amount(row['fee_total'])}đ",
                    self._duration(row["total_seconds"]),
                ),
            )

        self.denied_chart.set_data(
            [
                (self._DENY_REASON_LABELS.get(row["reason"], row["reason"]), row["events"])
                for row in analytics.denied_reasons(self.event_store, span)
            ]
        )

        self.shift_tree.delete(*self.shift_tree.get_children())
        for shift in self.event_store.list_shifts(30):
            if shift["closed_at"] is None:
                totals = self.event_store.shift_totals(shift["id"])
                expected, counted, difference = totals["expected_cash"], None, None
                tags = ("OPEN",)
            else:
                expected = shift["expected_cash"] or 0
                counted = shift["counted_cash"] or 0
                difference = counted - expected
                tags = ("SHORT",) if abs(difference) >= 1 else ()
            self.shift_tree.insert(
                "",
                tk.END,
                values=(
                    (shift["opened_at"] or "")[:16].replace("T", " "),
                    (shift["closed_at"] or "đang mở")[:16].replace("T", " "),
                    shift["username"] or "-",
                    f"{self._amount(expected)}đ",
                    "-" if counted is None else f"{self._amount(counted)}đ",
                    "-" if difference is None else f"{self._amount(difference)}đ",
                    shift["note"] or "",
                ),
                tags=tags,
            )

        self.audit_tree.delete(*self.audit_tree.get_children())
        for entry in self.event_store.list_audit(200):
            timestamp = (entry["ts"] or "")[:19].replace("T", " ")
            self.audit_tree.insert(
                "",
                tk.END,
                values=(timestamp, entry["username"] or "-", entry["action"], entry["detail"] or ""),
            )
        self.status_var.set(f"Báo cáo {span.label()}")

    def _export_report(self, file_format: str = "csv") -> None:
        span = self._report_range()
        if span is None:
            return
        file_format = file_format.lower()
        if file_format not in {"csv", "pdf"}:
            raise ValueError(f"Định dạng báo cáo không hỗ trợ: {file_format}")
        extension = f".{file_format}"
        format_label = file_format.upper()
        path = filedialog.asksaveasfilename(
            title=f"Xuất báo cáo {format_label}",
            defaultextension=extension,
            initialfile=f"bao_cao_{span.start:%Y%m%d}_{span.end:%Y%m%d}{extension}",
            filetypes=[(format_label, f"*{extension}")],
        )
        if not path:
            return
        output = Path(path)
        if output.suffix.lower() != extension:
            output = output.with_suffix(extension)
        try:
            exporter = analytics.export_report_pdf if file_format == "pdf" else analytics.export_report
            exporter(
                self.event_store,
                span,
                output,
                capacity=int(self.app_config.parking_capacity or 0),
            )
        except Exception as exc:
            messagebox.showerror("Xuất báo cáo", f"Không thể tạo file {format_label}:\n{exc}")
            return
        self._show_export_complete(output, f"Đã xuất báo cáo {format_label} thành công.")

    def _show_export_complete(self, path: Path, detail: str = "Đã xuất file thành công.") -> None:
        """Show the exported path and let the user launch its associated app."""
        path = path.resolve()
        dialog = tk.Toplevel(self)
        dialog.title("Xuất file thành công")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(True, False)

        body = ttk.Frame(dialog, padding=18)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="✓ Xuất file thành công", font=("Segoe UI", 12, "bold")).pack(
            anchor=tk.W
        )
        ttk.Label(body, text=detail, foreground=self._palette["muted"]).pack(
            anchor=tk.W, pady=(4, 10)
        )
        ttk.Label(body, text="Đường dẫn file:").pack(anchor=tk.W)
        path_var = tk.StringVar(dialog, value=str(path))
        path_entry = ttk.Entry(body, textvariable=path_var, state="readonly", width=78)
        path_entry.pack(fill=tk.X, pady=(4, 14))

        def open_file() -> None:
            try:
                open_with_default_app(path)
            except OSError as exc:
                messagebox.showerror(
                    "Mở file", f"Không thể mở file bằng ứng dụng mặc định:\n{exc}", parent=dialog
                )

        actions = ttk.Frame(body)
        actions.pack(fill=tk.X)
        open_button = ttk.Button(
            actions, text="Mở file", command=open_file, style="Accent.TButton"
        )
        open_button.pack(side=tk.RIGHT)
        ttk.Button(actions, text="Đóng", command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 6))

        dialog.bind("<Return>", lambda _event: open_file())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.update_idletasks()
        x = max(0, self.winfo_rootx() + (self.winfo_width() - dialog.winfo_reqwidth()) // 2)
        y = max(0, self.winfo_rooty() + (self.winfo_height() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f"+{x}+{y}")
        open_button.focus_set()

    def _on_tab_changed(self, _event=None) -> None:
        """Recompute the report only when its tab is actually shown."""
        try:
            current = self.notebook.tab(self.notebook.select(), "text")
        except tk.TclError:
            return
        if "Báo cáo" in current:
            self._refresh_report()

    # --- QR payment ---

    def _qr_image(self, payload: str):
        try:
            import qrcode

            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(payload)
            qr.make(fit=True)
            return qr.make_image(fill_color="black", back_color="white").convert("RGB")
        except Exception:
            return None

    def _show_momo_window(self, visit_id: int, amount: float, plate: str) -> None:
        client = self.app_config.momo()
        if client.problem:
            messagebox.showinfo(
                "Thu MoMo", f"{client.problem}.\nKhai báo thông tin merchant trong tab Cài đặt."
            )
            return

        window = tk.Toplevel(self)
        window.title("Thu tiền MoMo")
        window.transient(self)
        window.grab_set()
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=16)
        frame.pack()
        ttk.Label(frame, text=f"Phí gửi xe: {self._money(amount)}đ", style="Heading.TLabel").pack()
        ttk.Label(frame, text=f"Biển số: {plate}", foreground="#5b6b7b").pack(pady=(0, 8))
        qr_label = ttk.Label(frame, text="Đang tạo giao dịch MoMo…", justify=tk.CENTER)
        qr_label.pack(padx=20, pady=12)
        hint = ttk.Label(
            frame, text="Vui lòng chờ", foreground="#5b6b7b", wraplength=320, justify=tk.CENTER
        )
        hint.pack()
        ttk.Button(frame, text="Đóng", command=window.destroy).pack(fill=tk.X, pady=(12, 0))

        def window_exists() -> bool:
            try:
                return bool(window.winfo_exists())
            except tk.TclError:
                return False

        def schedule_query(payment) -> None:
            if not window_exists():
                return

            def query_worker() -> None:
                try:
                    result, error = client.query(payment.order_id), ""
                except Exception as exc:
                    result, error = {}, str(exc)
                self.after(0, handle_query, payment, result, error)

            threading.Thread(target=query_worker, name="momo-query", daemon=True).start()

        def handle_query(payment, result: dict, error: str) -> None:
            if not window_exists():
                return
            if error:
                hint.configure(text=f"⚠ {error}\nSẽ thử lại…", foreground="#e05260")
                window.after(10_000, schedule_query, payment)
                return
            try:
                result_code = int(result.get("resultCode", -1))
            except (TypeError, ValueError):
                result_code = -1
            received = float(result.get("amount") or 0)
            if result_code == 0 and received >= float(amount):
                user = self.current_user.username if self.current_user else "(tự động)"
                rows = self.event_store.mark_paid(
                    visit_id, "MOMO", username=user,
                    shift_id=self.active_shift["id"] if self.active_shift else None,
                    reference=str(result.get("transId") or payment.order_id),
                )
                if rows:
                    self.event_store.write_audit(
                        user, "PAYMENT_MOMO",
                        f"visit {visit_id} {int(received)} {result.get('transId', '')}",
                    )
                    self._refresh_visit_views()
                hint.configure(text="✅ MoMo đã xác nhận thanh toán", foreground="#2f9e6b")
                self.status_var.set(f"Đã thu MoMo {self._money(amount)}đ")
                window.after(1200, window.destroy)
                return
            message = str(result.get("message") or "Đang chờ khách thanh toán")
            hint.configure(text=f"⏳ {message}", foreground="#5b6b7b")
            window.after(5_000, schedule_query, payment)

        def payment_created(payment, error: str) -> None:
            if not window_exists():
                return
            if error:
                qr_label.configure(text="Không tạo được mã MoMo")
                hint.configure(text=f"⚠ {error}", foreground="#e05260")
                return
            image = self._qr_image(payment.qr_data)
            if image is None:
                qr_label.configure(text=payment.pay_url or payment.qr_data, wraplength=320)
            else:
                photo = ImageTk.PhotoImage(image)
                window._momo_qr_photo = photo
                qr_label.configure(image=photo, text="")
            hint.configure(text="⏳ Quét bằng ứng dụng MoMo để thanh toán")
            window.after(5_000, schedule_query, payment)

        def create_worker() -> None:
            try:
                payment, error = client.create_payment(visit_id, amount, plate), ""
            except Exception as exc:
                payment, error = None, str(exc)
            self.after(0, payment_created, payment, error)

        threading.Thread(target=create_worker, name="momo-create", daemon=True).start()

    def _show_qr_window(self, visit_id: int, amount: float, plate: str) -> None:
        if visit_id in self._open_payment_visits:
            return
        self._open_payment_visits.add(visit_id)
        bank = self.app_config.bank()
        window = tk.Toplevel(self)
        window.title("Thu tiền QR")
        window.transient(self)
        window.grab_set()
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=16)
        frame.pack()

        def close_window() -> None:
            self._open_payment_visits.discard(visit_id)
            try:
                window.destroy()
            except tk.TclError:
                pass

        window.protocol("WM_DELETE_WINDOW", close_window)
        ttk.Label(frame, text=f"Phí gửi xe: {self._money(amount)}đ", style="Heading.TLabel").pack()
        ttk.Label(frame, text=f"Biển số: {plate}", foreground="#5b6b7b").pack(pady=(0, 8))
        problem = bank.problem
        if not problem:
            # Only transfers arriving after this moment may settle the visit.
            # Without this guard an archived GX transaction could be replayed
            # when visit IDs are reused after clearing the local database.
            self.event_store.record_bank_payment_request(visit_id)
            note = transfer_note(visit_id, plate)
            payload = build_vietqr(bank, amount=amount or None, description=note)
            image = self._qr_image(payload)
            if image is not None:
                photo = ImageTk.PhotoImage(image)
                window._qr_photo = photo  # keep a reference per window
                ttk.Label(frame, image=photo).pack(pady=6)
            else:
                ttk.Label(
                    frame,
                    text="Chưa cài gói tạo ảnh QR.\nChạy:  pip install qrcode\n\n"
                         "Tạm thời dùng chuỗi VietQR bên dưới:",
                    foreground="#e05260",
                    justify=tk.CENTER,
                ).pack(pady=(6, 2))
                ttk.Label(frame, text=payload, wraplength=280, foreground="#5b6b7b").pack(pady=(0, 6))
            ttk.Label(
                frame,
                text=f"{bank.account_name or ''}  {bank.account_number}\nNội dung: {note}",
                foreground="#5b6b7b",
                justify=tk.CENTER,
            ).pack()
        else:
            ttk.Label(
                frame,
                text=f"Không tạo được mã VietQR.\n{problem}\n\n"
                     "Khai báo bank_bin + bank_account trong tab Cài đặt.",
                foreground="#e05260",
                wraplength=280,
                justify=tk.CENTER,
            ).pack(pady=8)

        def confirm() -> None:
            user = self.current_user.username if self.current_user else None
            rows = self.event_store.mark_paid(
                visit_id, "CASH", username=user,
                shift_id=self.active_shift["id"] if self.active_shift else None,
            )
            if rows:
                self.event_store.write_audit(
                    user, "PAYMENT_CASH", f"{plate} {int(amount or 0)}"
                )
            self._refresh_visit_views()
            self.status_var.set(
                f"Đã thu tiền mặt {self._money(amount)}đ" if rows
                else f"Lượt {plate} đã được thanh toán trước đó"
            )
            close_window()

        confirm_button = ttk.Button(
            frame, text="💵 Đã thu tiền mặt", command=confirm, style="Accent.TButton"
        )
        confirm_button.pack(fill=tk.X, pady=(12, 0))

        watching = self._bank_feed is not None
        hint = ttk.Label(
            frame,
            text="⏳ Đang chờ báo có từ ngân hàng…" if watching
                 else "Quét QR để chuyển khoản hoặc bấm nút khi khách trả tiền mặt.",
            foreground="#5b6b7b",
        )
        hint.pack(pady=(4, 0))
        if not watching or problem:
            return

        def watch() -> None:
            """Close the window by itself once the feed settles this visit."""
            if not window.winfo_exists():
                return
            if visit_id in self._paid_visit_ids:
                self._paid_visit_ids.discard(visit_id)
                hint.configure(text="✅ Đã nhận đủ tiền", foreground="#2f9e6b")
                window.after(1200, close_window)
                return
            window.after(1000, watch)

        window.after(1000, watch)

    def _export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export events",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            count = self.event_store.export_csv(Path(path))
            self._show_export_complete(Path(path), f"Đã xuất {count} sự kiện CSV.")

    def _export_visits_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export visits",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            count = self.event_store.export_visits_csv(Path(path))
            self._show_export_complete(Path(path), f"Đã xuất {count} lượt gửi xe CSV.")

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
        self._stop_bank_feed()
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
