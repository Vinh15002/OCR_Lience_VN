"""Small chart widgets drawn directly on a tk.Canvas.

Deliberately dependency-free: the packaged .exe already carries torch and
paddle, and pulling a plotting library into the bundle to draw a few bars would
add weight and another thing to break at freeze time. Every widget redraws on
<Configure>, so charts follow the window instead of being fixed bitmaps.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence

# Series colours, in the order they are added.
SERIES_COLOURS = ("#2d7ff9", "#3ecf8e", "#f2a33c", "#e05260")
AXIS_COLOUR = "#c8d1da"
GRID_COLOUR = "#e6ebf0"
TEXT_COLOUR = "#5b6b7b"
SURFACE = "#ffffff"


def _nice_ceiling(value: float) -> float:
    """Round an axis maximum up to a readable step (1, 2, 5 x 10^n)."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for factor in (1, 2, 2.5, 5, 10):
        candidate = magnitude * factor
        if candidate >= value:
            return candidate
    return magnitude * 10


class StatTile(ttk.Frame):
    """A single headline number with a caption, as used on the report header."""

    def __init__(self, parent: tk.Misc, title: str, accent: str = "#2d7ff9") -> None:
        super().__init__(parent, style="Card.TFrame", padding=(12, 8))
        self.value_var = tk.StringVar(value="-")
        self.hint_var = tk.StringVar(value="")
        ttk.Label(self, text=title.upper(), style="TileTitle.TLabel").pack(anchor=tk.W)
        self._value = ttk.Label(
            self, textvariable=self.value_var, style="TileValue.TLabel", foreground=accent
        )
        self._value.pack(anchor=tk.W, pady=(2, 0))
        ttk.Label(self, textvariable=self.hint_var, style="TileHint.TLabel").pack(anchor=tk.W)

    def set(self, value: str, hint: str = "") -> None:
        self.value_var.set(value)
        self.hint_var.set(hint)


class BarChart(tk.Canvas):
    """Vertical bar chart supporting one or more series side by side."""

    def __init__(
        self,
        parent: tk.Misc,
        height: int = 190,
        value_format: Callable[[float], str] | None = None,
        legend: bool = True,
    ) -> None:
        super().__init__(parent, height=height, bg=SURFACE, highlightthickness=0)
        self._labels: list[str] = []
        self._series: list[tuple[str, Sequence[float]]] = []
        self._value_format = value_format or (lambda value: f"{value:,.0f}".replace(",", "."))
        self._legend = legend
        self._empty_text = "Chưa có dữ liệu trong khoảng đã chọn"
        self.bind("<Configure>", lambda _event: self._redraw())

    def set_data(self, labels: Sequence[str], series: Sequence[tuple[str, Sequence[float]]]) -> None:
        self._labels = list(labels)
        self._series = [(name, list(values)) for name, values in series]
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 40 or height < 40:
            return
        total = sum(sum(values) for _, values in self._series)
        if not self._labels or not self._series or total <= 0:
            self.create_text(
                width / 2, height / 2, text=self._empty_text, fill=TEXT_COLOUR, font=("Segoe UI", 9)
            )
            return

        legend_height = 18 if (self._legend and len(self._series) > 1) else 0
        left, right, top, bottom = 54, 10, 8 + legend_height, 26
        plot_width = max(10, width - left - right)
        plot_height = max(10, height - top - bottom)
        maximum = _nice_ceiling(max(max(values) for _, values in self._series))

        # horizontal grid + axis labels
        for step in range(5):
            value = maximum * step / 4
            y = top + plot_height - plot_height * step / 4
            self.create_line(left, y, left + plot_width, y, fill=GRID_COLOUR)
            self.create_text(
                left - 6, y, text=self._value_format(value), anchor="e",
                fill=TEXT_COLOUR, font=("Segoe UI", 8),
            )
        self.create_line(left, top, left, top + plot_height, fill=AXIS_COLOUR)

        slot = plot_width / max(1, len(self._labels))
        series_count = len(self._series)
        bar_width = max(2.0, min(26.0, (slot - 4) / series_count))
        for index, label in enumerate(self._labels):
            slot_x = left + slot * index
            for series_index, (_, values) in enumerate(self._series):
                value = values[index] if index < len(values) else 0
                bar_height = plot_height * (value / maximum) if maximum else 0
                x0 = slot_x + (slot - bar_width * series_count) / 2 + bar_width * series_index
                y0 = top + plot_height - bar_height
                if bar_height >= 1:
                    self.create_rectangle(
                        x0, y0, x0 + bar_width - 1, top + plot_height,
                        fill=SERIES_COLOURS[series_index % len(SERIES_COLOURS)], width=0,
                    )
            # Thin out x labels so they never overlap on a narrow window.
            every = max(1, int(len(self._labels) / max(1, plot_width / 46)))
            if index % every == 0:
                self.create_text(
                    slot_x + slot / 2, top + plot_height + 12, text=label,
                    fill=TEXT_COLOUR, font=("Segoe UI", 8),
                )

        if legend_height:
            x = left
            for series_index, (name, _) in enumerate(self._series):
                self.create_rectangle(
                    x, 4, x + 10, 12,
                    fill=SERIES_COLOURS[series_index % len(SERIES_COLOURS)], width=0,
                )
                self.create_text(
                    x + 15, 8, text=name, anchor="w", fill=TEXT_COLOUR, font=("Segoe UI", 8)
                )
                x += 22 + len(name) * 6


class HBarChart(tk.Canvas):
    """Horizontal bars for ranked categories (durations, payment mix, plates)."""

    def __init__(
        self,
        parent: tk.Misc,
        height: int = 180,
        value_format: Callable[[float], str] | None = None,
        colour: str = "#2d7ff9",
    ) -> None:
        super().__init__(parent, height=height, bg=SURFACE, highlightthickness=0)
        self._rows: list[tuple[str, float]] = []
        self._value_format = value_format or (lambda value: f"{value:,.0f}".replace(",", "."))
        self._colour = colour
        self.bind("<Configure>", lambda _event: self._redraw())

    def set_data(self, rows: Sequence[tuple[str, float]]) -> None:
        self._rows = list(rows)
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 40 or height < 30:
            return
        if not self._rows or all(value <= 0 for _, value in self._rows):
            self.create_text(
                width / 2, height / 2, text="Chưa có dữ liệu", fill=TEXT_COLOUR, font=("Segoe UI", 9)
            )
            return
        maximum = max(value for _, value in self._rows) or 1
        label_width = 96
        value_width = 74
        track = max(20, width - label_width - value_width - 12)
        row_height = min(28, max(16, height / max(1, len(self._rows))))
        for index, (label, value) in enumerate(self._rows):
            y = index * row_height + row_height / 2
            if y > height:
                break
            self.create_text(
                label_width - 8, y, text=label, anchor="e", fill=TEXT_COLOUR, font=("Segoe UI", 9)
            )
            bar = track * (value / maximum)
            self.create_rectangle(
                label_width, y - row_height * 0.3, label_width + track, y + row_height * 0.3,
                fill="#f1f4f7", width=0,
            )
            if bar >= 1:
                self.create_rectangle(
                    label_width, y - row_height * 0.3, label_width + bar, y + row_height * 0.3,
                    fill=self._colour, width=0,
                )
            self.create_text(
                label_width + track + 8, y, text=self._value_format(value), anchor="w",
                fill=TEXT_COLOUR, font=("Segoe UI", 9),
            )
