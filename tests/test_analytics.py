import csv
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from plate_app import analytics
from plate_app.parking import RegisteredVehicle, Tariff
from plate_app.storage import EventStore

FRAME = np.zeros((20, 30, 3), dtype=np.uint8)
# A fixed local timezone keeps the ISO strings (and therefore the date-prefix
# range filter) stable wherever the tests run.
TZ = timezone(timedelta(hours=7))


def _visit(store, plate, entry: datetime, minutes: int, camera_in="in", camera_out="out"):
    store.record(camera_in, "Cong vao", plate, 0.9, FRAME, entry, direction="IN")
    return store.record(
        camera_out, "Cong ra", plate, 0.88, FRAME, entry + timedelta(minutes=minutes), direction="OUT"
    )


class DateRangeTests(unittest.TestCase):
    def test_last_days_is_inclusive(self):
        span = analytics.DateRange.last_days(7, today=date(2026, 7, 25))
        self.assertEqual(span.start, date(2026, 7, 19))
        self.assertEqual(span.end, date(2026, 7, 25))
        self.assertEqual(span.days, 7)
        self.assertEqual(len(span.each_day()), 7)

    def test_today_only_is_a_single_day(self):
        span = analytics.DateRange.today_only(today=date(2026, 7, 25))
        self.assertEqual(span.days, 1)
        self.assertEqual(span.label(), "25/07/2026")

    def test_this_month_starts_on_the_first(self):
        span = analytics.DateRange.this_month(today=date(2026, 7, 25))
        self.assertEqual(span.start, date(2026, 7, 1))
        self.assertEqual(span.days, 25)


class SummaryTests(unittest.TestCase):
    def _store(self, directory):
        return EventStore(Path(directory), tariff=Tariff(flat_fee=5000))

    def test_summary_counts_money_traffic_and_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            base = datetime(2026, 7, 25, 8, 0, tzinfo=TZ)
            first = _visit(store, "59X312345", base, 30)
            _visit(store, "43K199999", base + timedelta(hours=1), 90)
            store.mark_paid(first.visit_id, "CASH")

            span = analytics.DateRange(date(2026, 7, 25), date(2026, 7, 25))
            summary = analytics.summarize(store, span)

            self.assertEqual(summary.revenue_paid, 5000)
            self.assertEqual(summary.revenue_unpaid, 5000)
            self.assertEqual(summary.paid_count, 1)
            self.assertEqual(summary.unpaid_count, 1)
            self.assertEqual(summary.entries, 2)
            self.assertEqual(summary.exits, 2)
            self.assertEqual(summary.completed_visits, 2)
            self.assertEqual(summary.unique_plates, 2)
            self.assertEqual(summary.avg_duration_seconds, (30 + 90) * 60 / 2)
            self.assertEqual(summary.max_duration_seconds, 90 * 60)
            self.assertEqual(summary.peak_hour, 8)
            self.assertAlmostEqual(summary.collection_rate, 0.5)

    def test_range_excludes_visits_from_other_days(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            _visit(store, "59X312345", datetime(2026, 7, 20, 9, 0, tzinfo=TZ), 20)
            _visit(store, "43K199999", datetime(2026, 7, 25, 9, 0, tzinfo=TZ), 20)

            span = analytics.DateRange(date(2026, 7, 25), date(2026, 7, 25))
            summary = analytics.summarize(store, span)

            self.assertEqual(summary.completed_visits, 1)
            self.assertEqual(summary.entries, 1)

    def test_exempt_registered_vehicle_is_not_counted_as_unpaid(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.upsert_vehicle(RegisteredVehicle("59X312345", owner_name="An"))
            _visit(store, "59X312345", datetime(2026, 7, 25, 8, 0, tzinfo=TZ), 60)

            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            summary = analytics.summarize(store, span)

            self.assertEqual(summary.exempt_count, 1)
            self.assertEqual(summary.revenue_unpaid, 0)

    def test_capacity_derived_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            base = datetime(2026, 7, 25, 8, 0, tzinfo=TZ)
            # Two vehicles overlap between 08:10 and 08:30 -> peak of 2.
            _visit(store, "59X312345", base, 30)
            _visit(store, "43K199999", base + timedelta(minutes=10), 40)

            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            summary = analytics.summarize(store, span, capacity=10)

            self.assertEqual(summary.peak_occupancy, 2)
            self.assertAlmostEqual(summary.occupancy_rate, 0.2)
            self.assertAlmostEqual(summary.turnover(span.days), 0.2)

    def test_peak_occupancy_ignores_back_to_back_visits(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            base = datetime(2026, 7, 25, 8, 0, tzinfo=TZ)
            _visit(store, "59X312345", base, 30)
            # Second vehicle enters exactly when the first leaves.
            _visit(store, "43K199999", base + timedelta(minutes=30), 30)

            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            self.assertEqual(analytics.summarize(store, span).peak_occupancy, 1)


class BreakdownTests(unittest.TestCase):
    def _populated(self, directory):
        store = EventStore(Path(directory), tariff=Tariff(flat_fee=5000))
        base = datetime(2026, 7, 25, 7, 30, tzinfo=TZ)
        paid = _visit(store, "59X312345", base, 20)
        _visit(store, "43K199999", base + timedelta(hours=10), 200)
        store.mark_paid(paid.visit_id, "QR")
        return store

    def test_revenue_by_day_fills_empty_days(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._populated(directory)
            span = analytics.DateRange(date(2026, 7, 24), date(2026, 7, 25))
            rows = analytics.revenue_by_day(store, span)
            self.assertEqual([row["day"] for row in rows], ["2026-07-24", "2026-07-25"])
            self.assertEqual(rows[0]["paid_total"], 0)
            self.assertEqual(rows[1]["paid_total"], 5000)
            self.assertEqual(rows[1]["unpaid_total"], 5000)

    def test_traffic_by_hour_has_all_24_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._populated(directory)
            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            rows = analytics.traffic_by_hour(store, span)
            self.assertEqual(len(rows), 24)
            self.assertEqual(rows[7]["entries"], 1)
            self.assertEqual(rows[7]["exits"], 1)  # 07:30 in, 07:50 out
            self.assertEqual(rows[17]["entries"], 1)

    def test_duration_histogram_buckets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._populated(directory)
            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            buckets = {row["label"]: row["visits"] for row in analytics.duration_histogram(store, span)}
            self.assertEqual(buckets["< 30 phút"], 1)
            self.assertEqual(buckets["2 - 4 giờ"], 1)

    def test_payment_mix_only_counts_collected_money(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._populated(directory)
            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            mix = analytics.payment_mix(store, span)
            self.assertEqual(mix, [{"method": "QR", "visits": 1, "total": 5000}])

    def test_top_plates_ranked_by_visits(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._populated(directory)
            _visit(store, "59X312345", datetime(2026, 7, 25, 20, 0, tzinfo=TZ), 15)
            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            rows = analytics.top_plates(store, span)
            self.assertEqual(rows[0]["plate"], "59X312345")
            self.assertEqual(rows[0]["visits"], 2)

    def test_denied_reasons_grouped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.upsert_vehicle(RegisteredVehicle("99Z999999", access="DENY"))
            store.record("in", "IN", "99Z999999", 0.9, FRAME,
                         datetime(2026, 7, 25, 9, 0, tzinfo=TZ), direction="IN")
            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            self.assertEqual(
                analytics.denied_reasons(store, span), [{"reason": "blacklist", "events": 1}]
            )

    def test_collections_by_user_reads_the_audit_trail(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.write_audit("guard", "PAYMENT_CASH", "59X312345 5000")
            store.write_audit("guard", "PAYMENT_CASH", "43K199999 3000")
            store.write_audit("admin", "PAYMENT_QR", "36B112345 10000")
            store.write_audit("admin", "GATE_OPEN", "36B112345 ALLOW")  # not a payment

            span = analytics.DateRange.today_only()
            rows = analytics.collections_by_user(store, span)
            by_user = {(row["user"], row["method"]): row for row in rows}
            self.assertEqual(by_user[("guard", "CASH")]["total"], 8000)
            self.assertEqual(by_user[("guard", "CASH")]["visits"], 2)
            self.assertEqual(by_user[("admin", "QR")]["total"], 10000)
            self.assertEqual(len(rows), 2)


class ExportTests(unittest.TestCase):
    def test_export_writes_every_section(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory), tariff=Tariff(flat_fee=5000))
            _visit(store, "59X312345", datetime(2026, 7, 25, 8, 0, tzinfo=TZ), 45)
            span = analytics.DateRange.today_only(today=date(2026, 7, 25))
            output = Path(directory) / "report.csv"

            analytics.export_report(store, span, output, capacity=50)

            with output.open(encoding="utf-8-sig", newline="") as handle:
                headings = {row[0] for row in csv.reader(handle) if row}
            for section in (
                "TỔNG QUAN",
                "DOANH THU THEO NGÀY",
                "LƯU LƯỢNG THEO GIỜ",
                "LƯU LƯỢNG THEO THỨ",
                "THỜI GIAN GỬI",
                "HÌNH THỨC THANH TOÁN",
                "THU THEO NHÂN VIÊN",
                "XE RA VÀO NHIỀU NHẤT",
                "LÝ DO TỪ CHỐI",
            ):
                self.assertIn(section, headings)


if __name__ == "__main__":
    unittest.main()
