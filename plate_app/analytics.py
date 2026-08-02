"""Reporting queries for the parking operation.

Kept separate from the UI so every number on the report screen can be unit
tested against a temporary database. Aggregation happens in Python rather than
in SQL: a barrier lane produces a few thousand rows a month, and the rules
(peak occupancy, turnover, shift reconciliation) read far more clearly as
straight code than as window functions.

Timestamps are stored as local ISO strings ("2026-07-25T22:38:00.123+07:00"),
so ranges are filtered on the date prefix — comparing whole ISO strings would
misbehave the moment two rows carry different UTC offsets.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

# Duration buckets used by the "how long do vehicles stay" histogram.
DURATION_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("< 30 phút", 0, 30 * 60),
    ("30 - 60 phút", 30 * 60, 60 * 60),
    ("1 - 2 giờ", 60 * 60, 2 * 3600),
    ("2 - 4 giờ", 2 * 3600, 4 * 3600),
    ("4 - 8 giờ", 4 * 3600, 8 * 3600),
    ("> 8 giờ", 8 * 3600, 10**9),
)

WEEKDAY_LABELS = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")


@dataclass(frozen=True)
class DateRange:
    """Inclusive range of calendar days."""

    start: date
    end: date

    @classmethod
    def last_days(cls, days: int, today: date | None = None) -> "DateRange":
        end = today or date.today()
        return cls(end - timedelta(days=max(0, days - 1)), end)

    @classmethod
    def today_only(cls, today: date | None = None) -> "DateRange":
        end = today or date.today()
        return cls(end, end)

    @classmethod
    def this_month(cls, today: date | None = None) -> "DateRange":
        end = today or date.today()
        return cls(end.replace(day=1), end)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def bounds(self) -> tuple[str, str]:
        """Date-prefix bounds for `substr(<timestamp>, 1, 10) BETWEEN ? AND ?`."""
        return self.start.isoformat(), self.end.isoformat()

    def label(self) -> str:
        if self.start == self.end:
            return self.start.strftime("%d/%m/%Y")
        return f"{self.start.strftime('%d/%m/%Y')} - {self.end.strftime('%d/%m/%Y')}"

    def each_day(self) -> list[date]:
        return [self.start + timedelta(days=offset) for offset in range(self.days)]


@dataclass(frozen=True)
class Summary:
    """Headline numbers for the selected range."""

    revenue_paid: float = 0.0
    revenue_unpaid: float = 0.0
    paid_count: int = 0
    unpaid_count: int = 0
    exempt_count: int = 0
    entries: int = 0
    exits: int = 0
    completed_visits: int = 0
    review_visits: int = 0
    unique_plates: int = 0
    avg_duration_seconds: int = 0
    max_duration_seconds: int = 0
    denied_events: int = 0
    guest_events: int = 0
    allowed_events: int = 0
    avg_confidence: float = 0.0
    low_confidence_events: int = 0
    currently_inside: int = 0
    peak_hour: int | None = None
    peak_hour_entries: int = 0
    peak_occupancy: int = 0
    capacity: int = 0

    @property
    def revenue_expected(self) -> float:
        return self.revenue_paid + self.revenue_unpaid

    @property
    def collection_rate(self) -> float:
        """Share of billed money actually collected — the anti-leak number."""
        expected = self.revenue_expected
        return self.revenue_paid / expected if expected else 0.0

    @property
    def occupancy_rate(self) -> float:
        return self.peak_occupancy / self.capacity if self.capacity else 0.0

    def turnover(self, days: int) -> float:
        """Completed visits per space per day."""
        if not self.capacity or days <= 0:
            return 0.0
        return self.completed_visits / self.capacity / days


@dataclass
class Series:
    """One labelled chart series."""

    name: str
    values: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class ReportSection:
    """A table shared by the CSV and PDF report exporters."""

    title: str
    headers: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


def _rows(store, query: str, parameters: tuple) -> list:
    with store.open_connection() as connection:
        return connection.execute(query, parameters).fetchall()


def _visits(store, span: DateRange) -> list:
    """Completed/review visits whose *exit* falls inside the range."""
    return _rows(
        store,
        """
        SELECT plate, entry_at, exit_at, duration_seconds, fee,
               payment_status, payment_method, paid_at, status
        FROM vehicle_visits
        WHERE exit_at IS NOT NULL AND substr(exit_at, 1, 10) BETWEEN ? AND ?
        ORDER BY exit_at
        """,
        span.bounds,
    )


def _events(store, span: DateRange) -> list:
    return _rows(
        store,
        """
        SELECT plate, direction, confidence, detected_at, access_status, access_reason
        FROM plate_events
        WHERE substr(detected_at, 1, 10) BETWEEN ? AND ?
        ORDER BY detected_at
        """,
        span.bounds,
    )


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return None


def _hour_of(timestamp: str | None) -> int | None:
    """Hour slot without parsing: characters 12-13 of an ISO timestamp."""
    if not timestamp or len(timestamp) < 13:
        return None
    try:
        return int(timestamp[11:13])
    except ValueError:
        return None


def peak_occupancy(visits: Iterable) -> int:
    """Highest number of vehicles inside at the same moment.

    A sweep over entry/exit instants: +1 on every entry, -1 on every exit, in
    chronological order. Exits are processed before entries at the same instant
    so a space freed and refilled on the same second is not double counted.
    """
    moments: list[tuple[datetime, int]] = []
    for visit in visits:
        entered = _parse(visit["entry_at"])
        left = _parse(visit["exit_at"])
        if entered is None or left is None:
            continue
        moments.append((entered, 1))
        moments.append((left, -1))
    if not moments:
        return 0
    moments.sort(key=lambda item: (item[0], item[1]))
    current = peak = 0
    for _, delta in moments:
        current += delta
        peak = max(peak, current)
    return peak


def summarize(store, span: DateRange, capacity: int = 0, low_confidence: float = 0.6) -> Summary:
    visits = _visits(store, span)
    events = _events(store, span)

    paid = [v for v in visits if v["payment_status"] == "PAID"]
    unpaid = [v for v in visits if v["payment_status"] == "UNPAID"]
    exempt = [v for v in visits if v["payment_status"] == "EXEMPT"]
    completed = [v for v in visits if v["status"] == "COMPLETED"]
    durations = [v["duration_seconds"] for v in completed if v["duration_seconds"]]
    confidences = [event["confidence"] for event in events if event["confidence"] is not None]
    entry_hours = Counter(
        hour
        for hour in (_hour_of(event["detected_at"]) for event in events if event["direction"] == "IN")
        if hour is not None
    )
    peak_hour, peak_hour_entries = (None, 0)
    if entry_hours:
        peak_hour, peak_hour_entries = entry_hours.most_common(1)[0]

    inside = _rows(
        store,
        "SELECT COUNT(*) AS total FROM vehicle_visits WHERE status = 'INSIDE'",
        (),
    )

    return Summary(
        revenue_paid=sum(v["fee"] or 0 for v in paid),
        revenue_unpaid=sum(v["fee"] or 0 for v in unpaid),
        paid_count=len(paid),
        unpaid_count=len(unpaid),
        exempt_count=len(exempt),
        entries=sum(1 for event in events if event["direction"] == "IN"),
        exits=sum(1 for event in events if event["direction"] == "OUT"),
        completed_visits=len(completed),
        review_visits=sum(1 for v in visits if v["status"] == "REVIEW"),
        unique_plates=len({event["plate"] for event in events}),
        avg_duration_seconds=int(sum(durations) / len(durations)) if durations else 0,
        max_duration_seconds=max(durations) if durations else 0,
        denied_events=sum(1 for event in events if event["access_status"] == "DENY"),
        guest_events=sum(1 for event in events if event["access_status"] == "GUEST"),
        allowed_events=sum(1 for event in events if event["access_status"] == "ALLOW"),
        avg_confidence=sum(confidences) / len(confidences) if confidences else 0.0,
        low_confidence_events=sum(1 for value in confidences if value < low_confidence),
        currently_inside=int(inside[0]["total"]) if inside else 0,
        peak_hour=peak_hour,
        peak_hour_entries=peak_hour_entries,
        peak_occupancy=peak_occupancy(visits),
        capacity=max(0, int(capacity)),
    )


def revenue_by_day(store, span: DateRange) -> list[dict]:
    """One row per calendar day in the range, including days with no traffic."""
    visits = _visits(store, span)
    paid: dict[str, float] = defaultdict(float)
    unpaid: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for visit in visits:
        day = (visit["exit_at"] or "")[:10]
        if visit["payment_status"] == "PAID":
            paid[day] += visit["fee"] or 0
            counts[day] += 1
        elif visit["payment_status"] == "UNPAID":
            unpaid[day] += visit["fee"] or 0
            counts[day] += 1
        else:
            counts[day] += 1
    return [
        {
            "day": day.isoformat(),
            "paid_total": paid.get(day.isoformat(), 0.0),
            "unpaid_total": unpaid.get(day.isoformat(), 0.0),
            "visits": counts.get(day.isoformat(), 0),
        }
        for day in span.each_day()
    ]


def traffic_by_hour(store, span: DateRange) -> list[dict]:
    """Entries and exits per hour of day — the peak-hour view."""
    events = _events(store, span)
    entries = Counter()
    exits = Counter()
    for event in events:
        hour = _hour_of(event["detected_at"])
        if hour is None:
            continue
        if event["direction"] == "IN":
            entries[hour] += 1
        else:
            exits[hour] += 1
    return [
        {"hour": hour, "entries": entries.get(hour, 0), "exits": exits.get(hour, 0)}
        for hour in range(24)
    ]


def traffic_by_weekday(store, span: DateRange) -> list[dict]:
    events = _events(store, span)
    entries = Counter()
    for event in events:
        if event["direction"] != "IN":
            continue
        moment = _parse(event["detected_at"])
        if moment is not None:
            entries[moment.weekday()] += 1
    return [
        {"weekday": index, "label": WEEKDAY_LABELS[index], "entries": entries.get(index, 0)}
        for index in range(7)
    ]


def duration_histogram(store, span: DateRange) -> list[dict]:
    visits = _visits(store, span)
    buckets = [{"label": label, "visits": 0} for label, _, _ in DURATION_BUCKETS]
    for visit in visits:
        seconds = visit["duration_seconds"]
        if seconds is None:
            continue
        for index, (_, low, high) in enumerate(DURATION_BUCKETS):
            if low <= seconds < high:
                buckets[index]["visits"] += 1
                break
    return buckets


def payment_mix(store, span: DateRange) -> list[dict]:
    visits = _visits(store, span)
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for visit in visits:
        if visit["payment_status"] != "PAID":
            continue
        method = visit["payment_method"] or "KHÁC"
        totals[method] += visit["fee"] or 0
        counts[method] += 1
    return [
        {"method": method, "visits": counts[method], "total": totals[method]}
        for method in sorted(totals, key=lambda name: totals[name], reverse=True)
    ]


def top_plates(store, span: DateRange, limit: int = 10) -> list[dict]:
    visits = _visits(store, span)
    counts: dict[str, int] = defaultdict(int)
    fees: dict[str, float] = defaultdict(float)
    seconds: dict[str, int] = defaultdict(int)
    for visit in visits:
        plate = visit["plate"]
        counts[plate] += 1
        fees[plate] += visit["fee"] or 0
        seconds[plate] += visit["duration_seconds"] or 0
    ranked = sorted(counts, key=lambda plate: (counts[plate], fees[plate]), reverse=True)
    return [
        {
            "plate": plate,
            "visits": counts[plate],
            "fee_total": fees[plate],
            "total_seconds": seconds[plate],
        }
        for plate in ranked[:limit]
    ]


def denied_reasons(store, span: DateRange) -> list[dict]:
    events = _events(store, span)
    counts = Counter(
        event["access_reason"] or "khác"
        for event in events
        if event["access_status"] == "DENY"
    )
    return [
        {"reason": reason, "events": count}
        for reason, count in counts.most_common()
    ]


def collections_by_user(store, span: DateRange) -> list[dict]:
    """Cash/QR collected per operator, for end-of-shift reconciliation.

    Reconstructed from the audit log, which is the only place the collecting
    user is recorded. `detail` is written as "<plate> <amount>".
    """
    rows = _rows(
        store,
        """
        SELECT username, action, detail
        FROM audit_log
        WHERE action IN ('PAYMENT_CASH', 'PAYMENT_QR')
          AND substr(ts, 1, 10) BETWEEN ? AND ?
        """,
        span.bounds,
    )
    totals: dict[tuple[str, str], float] = defaultdict(float)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        method = "CASH" if row["action"] == "PAYMENT_CASH" else "QR"
        key = (row["username"] or "(không đăng nhập)", method)
        parts = (row["detail"] or "").split()
        amount = 0.0
        if parts:
            try:
                amount = float(parts[-1])
            except ValueError:
                amount = 0.0
        totals[key] += amount
        counts[key] += 1
    return [
        {"user": user, "method": method, "visits": counts[(user, method)], "total": totals[(user, method)]}
        for user, method in sorted(totals, key=lambda key: totals[key], reverse=True)
    ]


def subscription_sales(store, span: DateRange) -> dict:
    """Monthly passes sold in the period — revenue that never touches a visit."""
    rows = _rows(
        store,
        """
        SELECT vehicle_type, COUNT(*) AS tickets, COALESCE(SUM(amount), 0) AS total
        FROM subscriptions
        WHERE substr(paid_at, 1, 10) BETWEEN ? AND ?
        GROUP BY vehicle_type
        """,
        span.bounds,
    )
    by_type = [
        {"vehicle_type": row["vehicle_type"], "tickets": int(row["tickets"]), "total": float(row["total"])}
        for row in rows
    ]
    return {
        "tickets": sum(item["tickets"] for item in by_type),
        "total": sum(item["total"] for item in by_type),
        "by_type": by_type,
    }


def shifts_in_range(store, span: DateRange) -> list[dict]:
    """Closed shifts with their cash difference, for end-of-day reconciliation."""
    rows = _rows(
        store,
        """
        SELECT * FROM shifts
        WHERE substr(opened_at, 1, 10) BETWEEN ? AND ?
        ORDER BY id DESC
        """,
        span.bounds,
    )
    result = []
    for row in rows:
        expected = float(row["expected_cash"] or 0)
        counted = row["counted_cash"]
        result.append(
            {
                "id": row["id"],
                "username": row["username"],
                "opened_at": row["opened_at"],
                "closed_at": row["closed_at"],
                "expected_cash": expected,
                "counted_cash": None if counted is None else float(counted),
                "difference": None if counted is None else float(counted) - expected,
                "note": row["note"] or "",
            }
        )
    return result


def _report_sections(
    store, span: DateRange, capacity: int = 0,
) -> tuple[ReportSection, ...]:
    """Collect report data once so CSV and PDF always contain the same figures."""
    summary = summarize(store, span, capacity=capacity)
    overview: list[tuple[object, ...]] = [
        ("Doanh thu đã thu", round(summary.revenue_paid)),
        ("Chưa thu", round(summary.revenue_unpaid)),
        ("Tỷ lệ thu được (%)", round(summary.collection_rate * 100, 1)),
        ("Lượt vào", summary.entries),
        ("Lượt ra", summary.exits),
        ("Lượt hoàn tất", summary.completed_visits),
        ("Lượt cần kiểm tra", summary.review_visits),
        ("Biển số khác nhau", summary.unique_plates),
        ("Thời gian gửi TB (phút)", round(summary.avg_duration_seconds / 60, 1)),
        ("Cao điểm (giờ)", summary.peak_hour if summary.peak_hour is not None else "-"),
        ("Đỉnh xe trong bãi", summary.peak_occupancy),
    ]
    if summary.capacity:
        overview.extend(
            (
                ("Sức chứa", summary.capacity),
                ("Tỷ lệ lấp đầy đỉnh (%)", round(summary.occupancy_rate * 100, 1)),
                ("Vòng quay (lượt/chỗ/ngày)", round(summary.turnover(span.days), 2)),
            )
        )
    overview.extend(
        (
            ("Lượt bị từ chối", summary.denied_events),
            ("Độ tin cậy nhận dạng TB (%)", round(summary.avg_confidence * 100, 1)),
        )
    )

    sales = subscription_sales(store, span)
    subscription_rows = [
        (row["vehicle_type"], row["tickets"], round(row["total"]))
        for row in sales["by_type"]
    ]
    subscription_rows.append(("Tổng", sales["tickets"], round(sales["total"])))

    return (
        ReportSection("TỔNG QUAN", ("Chỉ tiêu", "Giá trị"), tuple(overview)),
        ReportSection(
            "DOANH THU THEO NGÀY",
            ("Ngày", "Đã thu", "Chưa thu", "Số lượt"),
            tuple(
                (row["day"], round(row["paid_total"]), round(row["unpaid_total"]), row["visits"])
                for row in revenue_by_day(store, span)
            ),
        ),
        ReportSection(
            "LƯU LƯỢNG THEO GIỜ",
            ("Giờ", "Lượt vào", "Lượt ra"),
            tuple(
                (f"{row['hour']:02d}:00", row["entries"], row["exits"])
                for row in traffic_by_hour(store, span)
            ),
        ),
        ReportSection(
            "LƯU LƯỢNG THEO THỨ",
            ("Thứ", "Lượt vào"),
            tuple((row["label"], row["entries"]) for row in traffic_by_weekday(store, span)),
        ),
        ReportSection(
            "THỜI GIAN GỬI",
            ("Khoảng", "Số lượt"),
            tuple((row["label"], row["visits"]) for row in duration_histogram(store, span)),
        ),
        ReportSection(
            "HÌNH THỨC THANH TOÁN",
            ("Hình thức", "Số lượt", "Số tiền"),
            tuple(
                (row["method"], row["visits"], round(row["total"]))
                for row in payment_mix(store, span)
            ),
        ),
        ReportSection(
            "THU THEO NHÂN VIÊN",
            ("Tài khoản", "Hình thức", "Số lượt", "Số tiền"),
            tuple(
                (row["user"], row["method"], row["visits"], round(row["total"]))
                for row in collections_by_user(store, span)
            ),
        ),
        ReportSection(
            "VÉ THÁNG", ("Loại xe", "Số vé", "Doanh thu"), tuple(subscription_rows)
        ),
        ReportSection(
            "CA TRỰC",
            ("Mở ca", "Đóng ca", "Nhân viên", "Phải có", "Đếm được", "Lệch", "Ghi chú"),
            tuple(
                (
                    row["opened_at"],
                    row["closed_at"] or "đang mở",
                    row["username"],
                    round(row["expected_cash"]),
                    "" if row["counted_cash"] is None else round(row["counted_cash"]),
                    "" if row["difference"] is None else round(row["difference"]),
                    row["note"],
                )
                for row in shifts_in_range(store, span)
            ),
        ),
        ReportSection(
            "XE RA VÀO NHIỀU NHẤT",
            ("Biển số", "Số lượt", "Tổng phí", "Tổng thời gian (phút)"),
            tuple(
                (
                    row["plate"],
                    row["visits"],
                    round(row["fee_total"]),
                    round(row["total_seconds"] / 60),
                )
                for row in top_plates(store, span, limit=20)
            ),
        ),
        ReportSection(
            "LÝ DO TỪ CHỐI",
            ("Lý do", "Số lượt"),
            tuple((row["reason"], row["events"]) for row in denied_reasons(store, span)),
        ),
    )


def export_report(store, span: DateRange, path: Path, capacity: int = 0) -> Path:
    """Write every report section into one Excel-friendly CSV."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["BÁO CÁO BÃI XE", span.label()])
        writer.writerow(["Xuất lúc", datetime.now().strftime("%d/%m/%Y %H:%M")])
        writer.writerow([])
        for section in _report_sections(store, span, capacity=capacity):
            writer.writerow([section.title])
            writer.writerow(section.headers)
            writer.writerows(section.rows)
            writer.writerow([])
    return path


def _register_pdf_fonts() -> tuple[str, str]:
    """Register a Unicode font pair that contains Vietnamese glyphs."""
    import os

    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular_name = "ParkingReport"
    bold_name = "ParkingReportBold"
    registered = set(pdfmetrics.getRegisteredFontNames())
    if regular_name in registered and bold_name in registered:
        return regular_name, bold_name

    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    reportlab_fonts = Path(reportlab.__file__).resolve().parent / "fonts"
    candidates = (
        (windows_fonts / "arial.ttf", windows_fonts / "arialbd.ttf"),
        (windows_fonts / "segoeui.ttf", windows_fonts / "segoeuib.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (reportlab_fonts / "Vera.ttf", reportlab_fonts / "VeraBd.ttf"),
    )
    required_glyphs = (
        "ĂÂĐÊÔƠƯăâđêôơư"
        "ẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỊỈỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ"
        "ạảấầẩẫậắằẳẵặẹẻẽếềểễệịỉọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
    )
    for regular_path, bold_path in candidates:
        if not regular_path.is_file() or not bold_path.is_file():
            continue
        try:
            regular_font = TTFont(regular_name, str(regular_path))
            bold_font = TTFont(bold_name, str(bold_path))
            required_codes = {ord(character) for character in required_glyphs}
            if not required_codes.issubset(regular_font.face.charWidths):
                continue
            if not required_codes.issubset(bold_font.face.charWidths):
                continue
            if regular_name not in registered:
                pdfmetrics.registerFont(regular_font)
            if bold_name not in registered:
                pdfmetrics.registerFont(bold_font)
            return regular_name, bold_name
        except Exception:
            continue
    raise RuntimeError("Không tìm thấy font Unicode để xuất báo cáo PDF.")


def export_report_pdf(store, span: DateRange, path: Path, capacity: int = 0) -> Path:
    """Write a readable, multi-page PDF containing every report section."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
        from reportlab.graphics.charts.legends import Legend
        from reportlab.graphics.shapes import Drawing, String
        from reportlab.platypus import (
            LongTable,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - dependency is installed in production
        raise RuntimeError("Cần cài thư viện reportlab để xuất PDF.") from exc
    from xml.sax.saxutils import escape

    regular_font, bold_font = _register_pdf_fonts()
    page_size = landscape(A4)
    exported_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    document = SimpleDocTemplate(
        str(path),
        pagesize=page_size,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=f"Báo cáo bãi xe {span.label()}",
        author="OCR Plate",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ParkingTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#1f2933"),
        spaceAfter=4 * mm,
    )
    subtitle_style = ParagraphStyle(
        "ParkingSubtitle",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#5b6b7b"),
        spaceAfter=5 * mm,
    )
    section_style = ParagraphStyle(
        "ParkingSection",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1f6fe0"),
        spaceBefore=2 * mm,
        spaceAfter=2 * mm,
        keepWithNext=True,
    )
    cell_style = ParagraphStyle(
        "ParkingCell", parent=styles["BodyText"], fontName=regular_font, fontSize=7.5, leading=10
    )
    header_style = ParagraphStyle(
        "ParkingHeader", parent=cell_style, fontName=bold_font, textColor=colors.white
    )

    def cell(value: object, style=cell_style) -> Paragraph:
        rendered = "-" if value is None or value == "" else str(value)
        return Paragraph(escape(rendered).replace("\n", "<br/>"), style)

    chart_colours = (
        colors.HexColor("#2d7ff9"),
        colors.HexColor("#3ecf8e"),
        colors.HexColor("#f2a33c"),
        colors.HexColor("#e05260"),
        colors.HexColor("#7c5ce7"),
    )

    def compact_number(value: float) -> str:
        value = float(value or 0)
        if abs(value) >= 1_000_000:
            return f"{value / 1_000_000:.1f}tr".replace(".0tr", "tr")
        if abs(value) >= 1_000:
            return f"{value / 1_000:.0f}k"
        return f"{value:.0f}"

    def chart_title(drawing: Drawing, title: str) -> None:
        drawing.add(
            String(
                drawing.width / 2,
                drawing.height - 12,
                title,
                fontName=bold_font,
                fontSize=9,
                fillColor=colors.HexColor("#1f2933"),
                textAnchor="middle",
            )
        )

    def no_chart_data(drawing: Drawing) -> None:
        drawing.add(
            String(
                drawing.width / 2,
                drawing.height / 2,
                "Chưa có dữ liệu",
                fontName=regular_font,
                fontSize=8,
                fillColor=colors.HexColor("#64748b"),
                textAnchor="middle",
            )
        )

    def use_integer_axis(axis, maximum_value: float) -> None:
        """Keep count charts from displaying fractional vehicles/visits."""
        if maximum_value <= 5:
            step = 1
        elif maximum_value <= 10:
            step = 2
        elif maximum_value <= 25:
            step = 5
        elif maximum_value <= 50:
            step = 10
        elif maximum_value <= 100:
            step = 20
        else:
            step = max(1, round(maximum_value / 5))
        axis.valueMin = 0
        axis.valueMax = max(step, ((int(maximum_value) + step - 1) // step) * step)
        axis.valueStep = step
        axis.labelTextFormat = lambda value: f"{value:.0f}"

    def vertical_chart(
        title: str,
        categories: list[str],
        series: list[tuple[str, list[float]]],
        width: float,
        height: float,
        formatter=None,
        integer_axis: bool = False,
    ) -> Drawing:
        drawing = Drawing(width, height)
        chart_title(drawing, title)
        if not categories or not any(any(value > 0 for value in values) for _, values in series):
            no_chart_data(drawing)
            return drawing

        chart = VerticalBarChart()
        chart.x = 43
        chart.y = 28
        chart.width = width - 55
        chart.height = height - 72
        chart.data = tuple(tuple(float(value) for value in values) for _, values in series)
        chart.categoryAxis.categoryNames = categories
        chart.categoryAxis.labels.fontName = regular_font
        chart.categoryAxis.labels.fontSize = 5.8
        chart.categoryAxis.labels.angle = 35
        chart.categoryAxis.labels.dx = 3
        chart.categoryAxis.labels.dy = -2
        chart.categoryAxis.strokeColor = colors.HexColor("#94a3b8")
        chart.valueAxis.forceZero = 1
        chart.valueAxis.labels.fontName = regular_font
        chart.valueAxis.labels.fontSize = 6
        chart.valueAxis.strokeColor = colors.HexColor("#94a3b8")
        chart.valueAxis.visibleGrid = 1
        chart.valueAxis.gridStrokeColor = colors.HexColor("#e2e8f0")
        chart.valueAxis.gridStrokeWidth = 0.35
        if formatter is not None:
            chart.valueAxis.labelTextFormat = formatter
        if integer_axis:
            use_integer_axis(
                chart.valueAxis,
                max(value for _name, values in series for value in values),
            )
        chart.groupSpacing = 5
        for index, _item in enumerate(series):
            chart.bars[index].fillColor = chart_colours[index % len(chart_colours)]
            chart.bars[index].strokeColor = None
        drawing.add(chart)

        if len(series) > 1:
            legend = Legend()
            legend.x = 47
            legend.y = height - 29
            legend.fontName = regular_font
            legend.fontSize = 6.2
            legend.dx = 6
            legend.dy = 6
            legend.dxTextSpace = 3
            legend.deltax = 76
            legend.columnMaximum = 1
            legend.colorNamePairs = [
                (chart_colours[index % len(chart_colours)], name)
                for index, (name, _values) in enumerate(series)
            ]
            drawing.add(legend)
        return drawing

    def horizontal_chart(
        title: str,
        categories: list[str],
        values: list[float],
        width: float,
        height: float,
        formatter=None,
        integer_axis: bool = False,
    ) -> Drawing:
        drawing = Drawing(width, height)
        chart_title(drawing, title)
        if not categories or not any(value > 0 for value in values):
            no_chart_data(drawing)
            return drawing

        # HorizontalBarChart places the first category at the bottom; reversing
        # keeps the report's natural order from top to bottom.
        categories = list(reversed(categories))
        values = list(reversed(values))
        chart = HorizontalBarChart()
        chart.x = 88
        chart.y = 22
        chart.width = width - 105
        chart.height = height - 55
        chart.data = (tuple(float(value) for value in values),)
        chart.categoryAxis.categoryNames = categories
        chart.categoryAxis.labels.fontName = regular_font
        chart.categoryAxis.labels.fontSize = 6.2
        chart.categoryAxis.labels.dx = -3
        chart.categoryAxis.strokeColor = colors.HexColor("#94a3b8")
        chart.valueAxis.forceZero = 1
        chart.valueAxis.labels.fontName = regular_font
        chart.valueAxis.labels.fontSize = 6
        chart.valueAxis.strokeColor = colors.HexColor("#94a3b8")
        chart.valueAxis.visibleGrid = 1
        chart.valueAxis.gridStrokeColor = colors.HexColor("#e2e8f0")
        chart.valueAxis.gridStrokeWidth = 0.35
        if formatter is not None:
            chart.valueAxis.labelTextFormat = formatter
        if integer_axis:
            use_integer_axis(chart.valueAxis, max(values))
        chart.bars[0].fillColor = chart_colours[0]
        chart.bars[0].strokeColor = None
        drawing.add(chart)
        return drawing

    def bucket_series(
        categories: list[str], series: list[list[float]], maximum: int = 16,
    ) -> tuple[list[str], list[list[float]]]:
        """Group long daily ranges into readable consecutive buckets."""
        if len(categories) <= maximum:
            return categories, series
        size = (len(categories) + maximum - 1) // maximum
        grouped_categories: list[str] = []
        grouped_series = [[] for _values in series]
        for start in range(0, len(categories), size):
            end = min(len(categories), start + size)
            first, last = categories[start], categories[end - 1]
            grouped_categories.append(first if first == last else f"{first}-{last}")
            for index, values in enumerate(series):
                grouped_series[index].append(sum(values[start:end]))
        return grouped_categories, grouped_series

    sections = _report_sections(store, span, capacity=capacity)
    by_title = {section.title: section for section in sections}
    revenue_rows = by_title["DOANH THU THEO NGÀY"].rows
    revenue_categories = [f"{str(row[0])[8:10]}/{str(row[0])[5:7]}" for row in revenue_rows]
    revenue_values = [
        [float(row[1]) for row in revenue_rows],
        [float(row[2]) for row in revenue_rows],
    ]
    revenue_categories, revenue_values = bucket_series(revenue_categories, revenue_values)

    traffic_rows = by_title["LƯU LƯỢNG THEO GIỜ"].rows
    traffic_categories = [str(row[0]) if index % 3 == 0 else "" for index, row in enumerate(traffic_rows)]
    payment_rows = by_title["HÌNH THỨC THANH TOÁN"].rows
    payment_labels = {
        "CASH": "Tiền mặt",
        "QR": "QR",
        "BANK": "Ngân hàng",
        "MOMO": "MoMo",
    }
    duration_rows = by_title["THỜI GIAN GỬI"].rows

    available_width = page_size[0] - document.leftMargin - document.rightMargin
    chart_gap = 4 * mm
    chart_column_width = available_width / 2
    chart_width = chart_column_width - chart_gap
    chart_height = 64 * mm
    chart_grid = Table(
        [
            [
                vertical_chart(
                    "Doanh thu theo ngày (đồng)",
                    revenue_categories,
                    [("Đã thu", revenue_values[0]), ("Chưa thu", revenue_values[1])],
                    chart_width,
                    chart_height,
                    compact_number,
                ),
                vertical_chart(
                    "Lưu lượng theo giờ",
                    traffic_categories,
                    [
                        ("Lượt vào", [float(row[1]) for row in traffic_rows]),
                        ("Lượt ra", [float(row[2]) for row in traffic_rows]),
                    ],
                    chart_width,
                    chart_height,
                    integer_axis=True,
                ),
            ],
            [
                horizontal_chart(
                    "Doanh thu theo hình thức thanh toán",
                    [payment_labels.get(str(row[0]), str(row[0])) for row in payment_rows],
                    [float(row[2]) for row in payment_rows],
                    chart_width,
                    chart_height,
                    compact_number,
                ),
                horizontal_chart(
                    "Phân bố thời gian gửi xe",
                    [str(row[0]) for row in duration_rows],
                    [float(row[1]) for row in duration_rows],
                    chart_width,
                    chart_height,
                    integer_axis=True,
                ),
            ],
        ],
        colWidths=(chart_column_width, chart_column_width),
    )
    chart_grid.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#cbd5e1")),
                ("LEFTPADDING", (0, 0), (-1, -1), chart_gap / 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), chart_gap / 2),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    story = [
        Paragraph("BÁO CÁO BÃI XE", title_style),
        Paragraph(f"Thời gian: {escape(span.label())} &nbsp;&nbsp;•&nbsp;&nbsp; Xuất lúc: {exported_at}", subtitle_style),
        Paragraph("BIỂU ĐỒ TỔNG QUAN", section_style),
        chart_grid,
        PageBreak(),
    ]
    for section in sections:
        story.append(Paragraph(escape(section.title), section_style))
        table_data = [[cell(header, header_style) for header in section.headers]]
        if section.rows:
            table_data.extend([[cell(value) for value in row] for row in section.rows])
        else:
            table_data.append([cell("Không có dữ liệu")] + [cell("")] * (len(section.headers) - 1))

        weights = []
        for header in section.headers:
            if header in {"Chỉ tiêu", "Ghi chú"}:
                weights.append(2.0)
            elif header in {"Mở ca", "Đóng ca", "Tổng thời gian (phút)"}:
                weights.append(1.5)
            else:
                weights.append(1.0)
        total_weight = sum(weights)
        column_widths = [available_width * weight / total_weight for weight in weights]
        table = LongTable(
            table_data,
            colWidths=column_widths,
            repeatRows=1,
            splitByRow=True,
            splitInRow=1,
        )
        table_commands = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6fe0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), (colors.white, colors.HexColor("#f4f7fa"))),
        ]
        if not section.rows:
            table_commands.append(("SPAN", (0, 1), (-1, 1)))
        table.setStyle(TableStyle(table_commands))
        story.extend((table, Spacer(1, 4 * mm)))

    def draw_footer(pdf_canvas, doc) -> None:
        pdf_canvas.saveState()
        pdf_canvas.setTitle(f"Báo cáo bãi xe {span.label()}")
        pdf_canvas.setFont(regular_font, 7.5)
        pdf_canvas.setFillColor(colors.HexColor("#64748b"))
        pdf_canvas.drawString(document.leftMargin, 7 * mm, "OCR Plate · Báo cáo vận hành")
        pdf_canvas.drawRightString(page_size[0] - document.rightMargin, 7 * mm, f"Trang {doc.page}")
        pdf_canvas.restoreState()

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return path
