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


def export_report(store, span: DateRange, path: Path, capacity: int = 0) -> Path:
    """Write every report section into one Excel-friendly CSV."""
    summary = summarize(store, span, capacity=capacity)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["BÁO CÁO BÃI XE", span.label()])
        writer.writerow(["Xuất lúc", datetime.now().strftime("%d/%m/%Y %H:%M")])
        writer.writerow([])

        writer.writerow(["TỔNG QUAN"])
        writer.writerow(["Doanh thu đã thu", round(summary.revenue_paid)])
        writer.writerow(["Chưa thu", round(summary.revenue_unpaid)])
        writer.writerow(["Tỷ lệ thu được (%)", round(summary.collection_rate * 100, 1)])
        writer.writerow(["Lượt vào", summary.entries])
        writer.writerow(["Lượt ra", summary.exits])
        writer.writerow(["Lượt hoàn tất", summary.completed_visits])
        writer.writerow(["Lượt cần kiểm tra", summary.review_visits])
        writer.writerow(["Biển số khác nhau", summary.unique_plates])
        writer.writerow(["Thời gian gửi TB (phút)", round(summary.avg_duration_seconds / 60, 1)])
        writer.writerow(["Cao điểm (giờ)", summary.peak_hour if summary.peak_hour is not None else "-"])
        writer.writerow(["Đỉnh xe trong bãi", summary.peak_occupancy])
        if summary.capacity:
            writer.writerow(["Sức chứa", summary.capacity])
            writer.writerow(["Tỷ lệ lấp đầy đỉnh (%)", round(summary.occupancy_rate * 100, 1)])
            writer.writerow(["Vòng quay (lượt/chỗ/ngày)", round(summary.turnover(span.days), 2)])
        writer.writerow(["Lượt bị từ chối", summary.denied_events])
        writer.writerow(["Độ tin cậy nhận dạng TB (%)", round(summary.avg_confidence * 100, 1)])
        writer.writerow([])

        writer.writerow(["DOANH THU THEO NGÀY"])
        writer.writerow(["Ngày", "Đã thu", "Chưa thu", "Số lượt"])
        for row in revenue_by_day(store, span):
            writer.writerow([row["day"], round(row["paid_total"]), round(row["unpaid_total"]), row["visits"]])
        writer.writerow([])

        writer.writerow(["LƯU LƯỢNG THEO GIỜ"])
        writer.writerow(["Giờ", "Lượt vào", "Lượt ra"])
        for row in traffic_by_hour(store, span):
            writer.writerow([f"{row['hour']:02d}:00", row["entries"], row["exits"]])
        writer.writerow([])

        writer.writerow(["LƯU LƯỢNG THEO THỨ"])
        writer.writerow(["Thứ", "Lượt vào"])
        for row in traffic_by_weekday(store, span):
            writer.writerow([row["label"], row["entries"]])
        writer.writerow([])

        writer.writerow(["THỜI GIAN GỬI"])
        writer.writerow(["Khoảng", "Số lượt"])
        for row in duration_histogram(store, span):
            writer.writerow([row["label"], row["visits"]])
        writer.writerow([])

        writer.writerow(["HÌNH THỨC THANH TOÁN"])
        writer.writerow(["Hình thức", "Số lượt", "Số tiền"])
        for row in payment_mix(store, span):
            writer.writerow([row["method"], row["visits"], round(row["total"])])
        writer.writerow([])

        writer.writerow(["THU THEO NHÂN VIÊN"])
        writer.writerow(["Tài khoản", "Hình thức", "Số lượt", "Số tiền"])
        for row in collections_by_user(store, span):
            writer.writerow([row["user"], row["method"], row["visits"], round(row["total"])])
        writer.writerow([])

        sales = subscription_sales(store, span)
        writer.writerow(["VÉ THÁNG"])
        writer.writerow(["Loại xe", "Số vé", "Doanh thu"])
        for row in sales["by_type"]:
            writer.writerow([row["vehicle_type"], row["tickets"], round(row["total"])])
        writer.writerow(["Tổng", sales["tickets"], round(sales["total"])])
        writer.writerow([])

        writer.writerow(["CA TRỰC"])
        writer.writerow(["Mở ca", "Đóng ca", "Nhân viên", "Phải có", "Đếm được", "Lệch", "Ghi chú"])
        for row in shifts_in_range(store, span):
            writer.writerow([
                row["opened_at"], row["closed_at"] or "đang mở", row["username"],
                round(row["expected_cash"]),
                "" if row["counted_cash"] is None else round(row["counted_cash"]),
                "" if row["difference"] is None else round(row["difference"]),
                row["note"],
            ])
        writer.writerow([])

        writer.writerow(["XE RA VÀO NHIỀU NHẤT"])
        writer.writerow(["Biển số", "Số lượt", "Tổng phí", "Tổng thời gian (phút)"])
        for row in top_plates(store, span, limit=20):
            writer.writerow([row["plate"], row["visits"], round(row["fee_total"]), round(row["total_seconds"] / 60)])
        writer.writerow([])

        writer.writerow(["LÝ DO TỪ CHỐI"])
        writer.writerow(["Lý do", "Số lượt"])
        for row in denied_reasons(store, span):
            writer.writerow([row["reason"], row["events"]])
    return path
