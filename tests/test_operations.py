"""Tests for the day-to-day operations layer: tariffs per vehicle class,
entry/exit reconciliation, manual overrides, monthly passes, shifts and
retention.
"""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from plate_app.config import AppConfig
from plate_app.parking import (
    BICYCLE,
    CAR,
    MOTORBIKE,
    RegisteredVehicle,
    Tariff,
    TariffTable,
    normalize_vehicle_type,
)
from plate_app.storage import EventStore


def frame():
    return np.zeros((20, 30, 3), dtype=np.uint8)


class TariffTests(unittest.TestCase):
    def test_daily_cap_limits_the_hourly_part(self):
        tariff = Tariff(flat_fee=5000, hourly_fee=5000, daily_cap=20000)
        # 10 hours would be 50k of hourly charge, capped at 20k, plus the flat fee.
        self.assertEqual(tariff.fee_for(10 * 3600), 25000)
        # Two started days lift the cap to 2 x 20k.
        self.assertEqual(tariff.fee_for(30 * 3600), 5000 + 40000)

    def test_no_cap_when_daily_cap_is_zero(self):
        tariff = Tariff(hourly_fee=5000)
        self.assertEqual(tariff.fee_for(10 * 3600), 50000)

    def test_nights_between_counts_boundary_crossings(self):
        tariff = Tariff(night_hour=22)
        entry = datetime(2026, 8, 1, 20, 0)
        self.assertEqual(tariff.nights_between(entry, datetime(2026, 8, 1, 21, 0)), 0)
        self.assertEqual(tariff.nights_between(entry, datetime(2026, 8, 2, 7, 0)), 1)
        self.assertEqual(tariff.nights_between(entry, datetime(2026, 8, 3, 7, 0)), 2)

    def test_overnight_fee_is_added_per_night(self):
        tariff = Tariff(flat_fee=3000, overnight_fee=5000, night_hour=22)
        entry = datetime(2026, 8, 1, 20, 0)
        exit_at = datetime(2026, 8, 2, 8, 0)
        self.assertEqual(tariff.fee_for_period(entry, exit_at), 8000)
        # Same duration inside one day: no surcharge.
        self.assertEqual(
            tariff.fee_for_period(datetime(2026, 8, 1, 8, 0), datetime(2026, 8, 1, 20, 0)),
            3000,
        )

    def test_tariff_table_picks_the_price_of_the_class(self):
        table = TariffTable(
            default=Tariff(flat_fee=3000),
            by_type={CAR: Tariff(flat_fee=20000)},
        )
        self.assertEqual(table.for_type(CAR).flat_fee, 20000)
        self.assertEqual(table.for_type(MOTORBIKE).flat_fee, 3000)
        self.assertEqual(table.for_type("nonsense").flat_fee, 3000)

    def test_config_builds_the_table_from_overrides(self):
        config = AppConfig(
            parking_flat_fee=3000,
            parking_hourly_fee=2000,
            parking_tariffs={"CAR": {"flat_fee": 20000, "daily_cap": 100000}},
        )
        table = config.tariff_table()
        self.assertEqual(table.for_type(CAR).flat_fee, 20000)
        # Unspecified keys are inherited from the base price list.
        self.assertEqual(table.for_type(CAR).hourly_fee, 2000)
        self.assertEqual(table.for_type(MOTORBIKE).flat_fee, 3000)

    def test_normalize_vehicle_type(self):
        self.assertEqual(normalize_vehicle_type("car"), CAR)
        self.assertEqual(normalize_vehicle_type(None), MOTORBIKE)
        self.assertEqual(normalize_vehicle_type("", default=BICYCLE), BICYCLE)


class VisitTypingTests(unittest.TestCase):
    def test_visit_is_priced_with_the_registered_class(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(
                Path(directory),
                tariff_table=TariffTable(
                    default=Tariff(flat_fee=3000), by_type={CAR: Tariff(flat_fee=20000)}
                ),
            )
            store.upsert_vehicle(
                RegisteredVehicle("51A12345", owner_name="Bình", vehicle_type=CAR)
            )
            entry = datetime.now().astimezone() - timedelta(hours=2)
            store.record("in", "IN", "51A12345", 0.9, frame(), detected_at=entry, direction="IN")
            store.record("out", "OUT", "51A12345", 0.9, frame(), direction="OUT")
            visit = store.latest_visits(limit=1)[0]
            self.assertEqual(visit.vehicle_type, CAR)
            # Registered vehicles do not pay per visit, but the fee is still priced.
            self.assertEqual(visit.fee, 20000)
            self.assertEqual(visit.payment_status, "EXEMPT")

    def test_retyping_a_visit_reprices_it(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(
                Path(directory),
                tariff_table=TariffTable(
                    default=Tariff(flat_fee=3000), by_type={CAR: Tariff(flat_fee=20000)}
                ),
            )
            entry = datetime.now().astimezone() - timedelta(hours=1)
            store.record("in", "IN", "51A12345", 0.9, frame(), detected_at=entry, direction="IN")
            store.record("out", "OUT", "51A12345", 0.9, frame(), direction="OUT")
            visit = store.latest_visits(limit=1)[0]
            self.assertEqual(visit.fee, 3000)
            fee = store.set_visit_vehicle_type(visit.id, CAR, username="admin")
            self.assertEqual(fee, 20000)
            self.assertEqual(store.latest_visits(limit=1)[0].vehicle_type, CAR)

    def test_paid_visit_is_not_repriced(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory), tariff=Tariff(flat_fee=3000))
            entry = datetime.now().astimezone() - timedelta(hours=1)
            store.record("in", "IN", "59X312345", 0.9, frame(), detected_at=entry, direction="IN")
            store.record("out", "OUT", "59X312345", 0.9, frame(), direction="OUT")
            visit = store.latest_visits(limit=1)[0]
            store.mark_paid(visit.id, "CASH", username="thu")
            store.set_visit_vehicle_type(visit.id, CAR)
            self.assertEqual(store.latest_visits(limit=1)[0].fee, 3000)


class ReconciliationTests(unittest.TestCase):
    def test_short_stay_and_low_confidence_are_flagged(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.record("in", "IN", "59X312345", 0.9, frame(), direction="IN")
            store.record("out", "OUT", "59X312345", 0.4, frame(), direction="OUT")
            visit = store.latest_visits(limit=1)[0]
            self.assertIn("low_confidence", visit.review_flag)
            self.assertIn("short_stay", visit.review_flag)

    def test_clean_visit_has_no_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            entry = datetime.now().astimezone() - timedelta(hours=3)
            store.record("in", "IN", "59X312345", 0.95, frame(), detected_at=entry, direction="IN")
            store.record("out", "OUT", "59X312345", 0.92, frame(), direction="OUT")
            self.assertEqual(store.latest_visits(limit=1)[0].review_flag, "")

    def test_exit_without_entry_is_flagged_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.record("out", "OUT", "59X312345", 0.9, frame(), direction="OUT")
            visit = store.latest_visits(limit=1)[0]
            self.assertEqual(visit.status, "REVIEW")
            self.assertEqual(visit.review_flag, "no_entry")

    def test_visit_detail_returns_both_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            entry = datetime.now().astimezone() - timedelta(hours=1)
            store.record("in", "Cổng vào", "59X312345", 0.9, frame(), detected_at=entry, direction="IN")
            store.record("out", "Cổng ra", "59X312345", 0.9, frame(), direction="OUT")
            visit = store.latest_visits(limit=1)[0]
            detail = store.visit_detail(visit.id)
            self.assertEqual(detail["plate"], "59X312345")
            self.assertTrue(Path(detail["entry_event"]["snapshot_path"]).exists())
            self.assertTrue(Path(detail["exit_event"]["snapshot_path"]).exists())
            self.assertEqual(detail["entry_event"]["camera_name"], "Cổng vào")

    def test_clearing_a_flag_marks_the_visit_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.record("in", "IN", "59X312345", 0.4, frame(), direction="IN")
            store.record("out", "OUT", "59X312345", 0.4, frame(), direction="OUT")
            visit = store.latest_visits(limit=1)[0]
            self.assertEqual(store.clear_visit_flag(visit.id, username="admin"), 1)
            self.assertEqual(store.latest_visits(limit=1)[0].review_flag, "")
            self.assertTrue(
                any(row["action"] == "VISIT_VERIFIED" for row in store.list_audit())
            )


class ManualOverrideTests(unittest.TestCase):
    def test_manual_entry_creates_a_flagged_visit_without_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            event = store.record_manual("59-X3 123.45", "IN", username="admin", note="mất vé")
            self.assertEqual(event.plate, "59X312345")
            self.assertEqual(event.source, "MANUAL")
            self.assertEqual(event.snapshot_path, "")
            visit = store.latest_visits(limit=1)[0]
            self.assertEqual(visit.status, "INSIDE")
            self.assertIn("manual", visit.review_flag)
            self.assertIn("mất vé", visit.note)
            self.assertTrue(any(row["action"] == "MANUAL_IN" for row in store.list_audit()))

    def test_manual_exit_closes_an_open_visit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory), tariff=Tariff(flat_fee=3000))
            entry = datetime.now().astimezone() - timedelta(hours=1)
            store.record("in", "IN", "59X312345", 0.9, frame(), detected_at=entry, direction="IN")
            store.record_manual("59X312345", "OUT", username="admin")
            visit = store.latest_visits(limit=1)[0]
            self.assertEqual(visit.status, "COMPLETED")
            self.assertEqual(visit.fee, 3000)

    def test_correcting_a_plate_rewrites_visit_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.record("in", "IN", "S9X312345", 0.7, frame(), direction="IN")
            visit = store.latest_visits(limit=1)[0]
            store.update_visit_plate(visit.id, "59X3-123.45", username="admin")
            self.assertEqual(store.latest_visits(limit=1)[0].plate, "59X312345")
            self.assertEqual(store.latest(limit=1)[0].plate, "59X312345")
            self.assertTrue(
                any(row["action"] == "PLATE_CORRECTED" for row in store.list_audit())
            )

    def test_correcting_into_a_plate_already_inside_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.record("in", "IN", "59X312345", 0.9, frame(), direction="IN")
            store.record("in", "IN", "59X399999", 0.9, frame(), direction="IN")
            visit = store.latest_visits(limit=1)[0]
            with self.assertRaises(ValueError):
                store.update_visit_plate(visit.id, "59X312345")


class SubscriptionTests(unittest.TestCase):
    def test_new_pass_whitelists_the_plate_for_one_month(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            vehicle = store.add_subscription(
                "59X312345", months=1, amount=100000, owner_name="An",
                username="admin", today=date(2026, 8, 2),
            )
            self.assertEqual(vehicle.valid_until, "2026-09-01")
            self.assertTrue(store.find_vehicle("59X312345").is_valid(datetime(2026, 8, 20)))
            self.assertEqual(store.subscription_revenue()["total"], 100000)

    def test_renewing_early_stacks_onto_the_remaining_days(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.add_subscription("59X312345", months=1, today=date(2026, 8, 2))
            vehicle = store.add_subscription(
                "59X312345", months=1, amount=100000, today=date(2026, 8, 20)
            )
            # Old pass ended 01/09, so the new one runs to 01/10.
            self.assertEqual(vehicle.valid_until, "2026-10-01")
            self.assertEqual(len(store.list_subscriptions()), 2)

    def test_month_end_start_is_clamped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            vehicle = store.add_subscription("59X312345", months=1, today=date(2026, 1, 31))
            self.assertEqual(vehicle.valid_until, "2026-02-27")

    def test_expiring_vehicles_lists_soon_and_already_expired(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            today = date(2026, 8, 2)
            store.upsert_vehicle(
                RegisteredVehicle("59X311111", valid_until=(today + timedelta(days=3)).isoformat())
            )
            store.upsert_vehicle(
                RegisteredVehicle("59X322222", valid_until=(today - timedelta(days=1)).isoformat())
            )
            store.upsert_vehicle(
                RegisteredVehicle("59X333333", valid_until=(today + timedelta(days=40)).isoformat())
            )
            plates = [item.plate for item in store.expiring_vehicles(days=7, today=today)]
            self.assertEqual(plates, ["59X322222", "59X311111"])

    def test_days_left(self):
        vehicle = RegisteredVehicle("59X312345", valid_until="2026-08-10")
        self.assertEqual(vehicle.days_left(date(2026, 8, 2)), 8)
        self.assertIsNone(RegisteredVehicle("59X312345").days_left(date(2026, 8, 2)))


class ShiftTests(unittest.TestCase):
    def _paid_visit(self, store, plate, method="CASH", shift_id=None, username="thu"):
        entry = datetime.now().astimezone() - timedelta(hours=1)
        store.record("in", "IN", plate, 0.9, frame(), detected_at=entry, direction="IN")
        store.record("out", "OUT", plate, 0.9, frame(), direction="OUT")
        visit = store.latest_visits(limit=1)[0]
        store.mark_paid(visit.id, method, username=username, shift_id=shift_id)
        return visit

    def test_only_one_shift_can_be_open(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.open_shift("thu", opening_cash=200000)
            with self.assertRaises(ValueError):
                store.open_shift("thu2")
            self.assertEqual(store.current_shift()["username"], "thu")

    def test_totals_split_cash_qr_and_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory), tariff=Tariff(flat_fee=3000))
            shift = store.open_shift("thu", opening_cash=50000)
            self._paid_visit(store, "59X311111", "CASH", shift["id"])
            self._paid_visit(store, "59X322222", "QR", shift["id"])
            store.add_subscription(
                "59X333333", months=1, amount=100000, username="thu", shift_id=shift["id"]
            )
            totals = store.shift_totals(shift["id"])
            self.assertEqual(totals["cash_total"], 3000)
            self.assertEqual(totals["qr_total"], 3000)
            self.assertEqual(totals["subscription_total"], 100000)
            self.assertEqual(totals["expected_cash"], 50000 + 3000 + 100000)

    def test_closing_reports_the_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory), tariff=Tariff(flat_fee=3000))
            shift = store.open_shift("thu", opening_cash=0)
            self._paid_visit(store, "59X311111", "CASH", shift["id"])
            result = store.close_shift(shift["id"], counted_cash=2000, note="thiếu")
            self.assertEqual(result["expected_cash"], 3000)
            self.assertEqual(result["difference"], -1000)
            self.assertIsNone(store.current_shift())
            self.assertTrue(any(row["action"] == "SHIFT_CLOSE" for row in store.list_audit()))


class RetentionTests(unittest.TestCase):
    def test_purge_removes_old_visits_events_and_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            old = datetime.now().astimezone() - timedelta(days=100)
            store.record("in", "IN", "59X311111", 0.9, frame(), detected_at=old, direction="IN")
            store.record(
                "out", "OUT", "59X311111", 0.9, frame(),
                detected_at=old + timedelta(hours=1), direction="OUT",
            )
            snapshots = list(store.snapshot_dir.glob("*.jpg"))
            self.assertEqual(len(snapshots), 2)
            result = store.purge_older_than(30, username="admin")
            self.assertEqual(result["visits"], 1)
            self.assertEqual(result["events"], 2)
            self.assertEqual(result["snapshots"], 2)
            self.assertEqual(store.count_events(), 0)
            self.assertEqual(list(store.snapshot_dir.glob("*.jpg")), [])

    def test_purge_keeps_recent_data_and_vehicles_still_inside(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            old = datetime.now().astimezone() - timedelta(days=100)
            store.record("in", "IN", "59X311111", 0.9, frame(), detected_at=old, direction="IN")
            store.record("in", "IN", "59X322222", 0.9, frame(), direction="IN")
            result = store.purge_older_than(30)
            self.assertEqual(result["visits"], 0)
            self.assertEqual(result["events"], 0)
            self.assertEqual(store.count_events(), 2)

    def test_purge_is_a_no_op_without_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.record("in", "IN", "59X311111", 0.9, frame(), direction="IN")
            self.assertEqual(store.purge_older_than(0)["events"], 0)
            self.assertEqual(store.count_events(), 1)


class PaymentTests(unittest.TestCase):
    def test_crc_matches_a_published_vietqr_sample(self):
        from plate_app.payment import crc16_ccitt

        sample = (
            "00020001021238570010A00000072701270006970436011308810004580860208"
            "QRIBFTTA53037045802VN6304"
        )
        self.assertEqual(f"{crc16_ccitt(sample):04X}", "CD60")

    def test_dynamic_payload_carries_amount_and_note(self):
        from plate_app.payment import BankAccount, build_vietqr, crc16_ccitt

        account = BankAccount("970418", "0011012345678", "NGUYEN VAN A")
        payload = build_vietqr(account, amount=3000, description="GX7 59X312345")
        self.assertIn("010212", payload)         # dynamic QR
        self.assertIn("54043000", payload)       # amount 3000
        self.assertIn("5303704", payload)        # VND
        self.assertIn("0813GX7 59X312345", payload)  # tag 62 -> sub-tag 08 purpose
        self.assertEqual(f"{crc16_ccitt(payload[:-4]):04X}", payload[-4:])

    def test_static_payload_has_no_amount(self):
        from plate_app.payment import BankAccount, build_vietqr

        payload = build_vietqr(BankAccount("970418", "0011012345678"))
        self.assertIn("010211", payload)
        self.assertNotIn("5404", payload)

    def test_account_problems_are_reported(self):
        from plate_app.payment import BankAccount

        self.assertEqual(BankAccount("970418", "0011012345678").problem, "")
        self.assertIn("6 chữ số", BankAccount("9704", "123").problem)
        self.assertIn("Chưa khai báo", BankAccount("", "").problem)
        self.assertIn("chữ và số", BankAccount("970418", "0011 0123").problem)
        self.assertIn("19 ký tự", BankAccount("970418", "1" * 20).problem)

    def test_transfer_note_is_short_and_identifies_the_visit(self):
        from plate_app.payment import transfer_note

        self.assertEqual(transfer_note(12, "59X312345"), "GX12 59X312345")
        self.assertLessEqual(len(transfer_note(999999, "5 9 X 3 1 2 3 4 5 6 7 8 9")), 25)


class BankFeedTests(unittest.TestCase):
    def _tx(self, **kwargs):
        from plate_app.bankfeed import BankTransaction

        base = {"id": "1", "amount": 5000.0, "content": "", "when": "", "reference": "FT01"}
        base.update(kwargs)
        return BankTransaction(**base)

    def test_note_carries_the_visit_id(self):
        self.assertEqual(self._tx(content="CT DEN GX12 59X312345").visit_hint, 12)
        self.assertEqual(self._tx(content="chuyen tien gx7").visit_hint, 7)
        self.assertIsNone(self._tx(content="tien nuoc thang 8").visit_hint)

    def test_match_by_note_even_when_amounts_are_equal(self):
        from plate_app.bankfeed import match_transaction

        pending = {11: 5000.0, 12: 5000.0}
        self.assertEqual(match_transaction(self._tx(content="GX12 59X"), pending), 12)

    def test_match_by_amount_only_when_unambiguous(self):
        from plate_app.bankfeed import match_transaction

        self.assertEqual(match_transaction(self._tx(content="khong ghi gi"), {11: 5000.0}), 11)
        # Two visits owe the same amount -> refuse to guess.
        self.assertIsNone(
            match_transaction(self._tx(content="khong ghi gi"), {11: 5000.0, 12: 5000.0})
        )

    def test_underpayment_and_outgoing_transfers_are_ignored(self):
        from plate_app.bankfeed import match_transaction

        self.assertIsNone(match_transaction(self._tx(content="GX11", amount=3000), {11: 5000.0}))
        self.assertIsNone(match_transaction(self._tx(amount=-5000), {11: 5000.0}))

    def test_overpayment_still_settles_the_visit(self):
        from plate_app.bankfeed import match_transaction

        self.assertEqual(match_transaction(self._tx(content="GX11", amount=10000), {11: 5000.0}), 11)

    def test_batch_never_settles_one_visit_twice(self):
        from plate_app.bankfeed import match_all

        transactions = [self._tx(id="1", content="GX11"), self._tx(id="2", content="GX11")]
        matches = match_all(transactions, {11: 5000.0})
        self.assertEqual([visit for visit, _ in matches], [11])

    def test_sepay_response_is_normalised(self):
        from plate_app.bankfeed import SePayFeed

        row = {
            "id": 907,
            "transaction_date": "2026-08-02 19:59:48",
            "amount_in": "5000.00",
            "amount_out": "0.00",
            "transaction_content": "GX12 59X312345",
            "reference_number": "677760.050523.080001",
        }
        transaction = SePayFeed._to_transaction(row)
        self.assertEqual(transaction.id, "907")
        self.assertEqual(transaction.amount, 5000.0)
        self.assertEqual(transaction.visit_hint, 12)
        self.assertEqual(transaction.reference, "677760.050523.080001")

    def test_casso_response_is_normalised(self):
        from plate_app.bankfeed import CassoFeed

        row = {"id": 55, "tid": "FT2026", "description": "GX8 43K199999", "amount": 7000,
               "when": "2026-08-02 20:00:00"}
        transaction = CassoFeed._to_transaction(row)
        self.assertEqual(transaction.visit_hint, 8)
        self.assertEqual(transaction.amount, 7000.0)

    def test_build_feed_requires_provider_and_token(self):
        from plate_app.bankfeed import CassoFeed, SePayFeed, build_feed

        self.assertIsNone(build_feed(AppConfig()))
        self.assertIsNone(build_feed(AppConfig(payment_provider="sepay")))  # no token
        self.assertIsInstance(
            build_feed(AppConfig(payment_provider="sepay", payment_api_token="x")), SePayFeed
        )
        self.assertIsInstance(
            build_feed(AppConfig(payment_provider="casso", payment_api_token="x")), CassoFeed
        )

    def test_pending_payments_and_auto_settlement(self):
        from plate_app.bankfeed import match_all

        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory), tariff=Tariff(flat_fee=5000))
            entry = datetime.now().astimezone() - timedelta(hours=1)
            store.record("in", "IN", "59X312345", 0.9, frame(), detected_at=entry, direction="IN")
            store.record("out", "OUT", "59X312345", 0.9, frame(), direction="OUT")
            pending = store.pending_payments()
            self.assertEqual(list(pending.values()), [5000.0])

            visit_id = next(iter(pending))
            matches = match_all([self._tx(content=f"GX{visit_id} 59X312345")], pending)
            for matched_id, transaction in matches:
                store.mark_paid(matched_id, "BANK", username="(tự động)",
                                reference=transaction.reference)
            visit = store.latest_visits(limit=1)[0]
            self.assertEqual(visit.payment_status, "PAID")
            self.assertEqual(visit.payment_method, "BANK")
            self.assertEqual(visit.payment_reference, "FT01")
            self.assertEqual(store.pending_payments(), {})

    def test_mark_paid_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory), tariff=Tariff(flat_fee=5000))
            entry = datetime.now().astimezone() - timedelta(hours=1)
            store.record("in", "IN", "59X312345", 0.9, frame(), detected_at=entry, direction="IN")
            store.record("out", "OUT", "59X312345", 0.9, frame(), direction="OUT")
            visit_id = next(iter(store.pending_payments()))
            self.assertEqual(store.mark_paid(visit_id, "BANK"), 1)
            self.assertEqual(store.mark_paid(visit_id, "CASH"), 0)

    def test_state_survives_between_polls(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            self.assertEqual(store.get_state("bank_feed_since_id", "0"), "0")
            store.set_state("bank_feed_since_id", "907")
            store.set_state("bank_feed_since_id", "908")
            self.assertEqual(store.get_state("bank_feed_since_id"), "908")


class ReportingTests(unittest.TestCase):
    def test_subscription_sales_group_by_class(self):
        from plate_app import analytics

        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.add_subscription("59X311111", months=1, amount=100000)
            store.add_subscription("51A12345", months=1, amount=800000, vehicle_type=CAR)
            sales = analytics.subscription_sales(store, analytics.DateRange.today_only())
            self.assertEqual(sales["tickets"], 2)
            self.assertEqual(sales["total"], 900000)
            self.assertEqual(
                {row["vehicle_type"]: row["total"] for row in sales["by_type"]},
                {MOTORBIKE: 100000, CAR: 800000},
            )

    def test_shifts_in_range_reports_the_difference(self):
        from plate_app import analytics

        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            shift = store.open_shift("thu", opening_cash=100000)
            store.close_shift(shift["id"], counted_cash=90000)
            rows = analytics.shifts_in_range(store, analytics.DateRange.today_only())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["difference"], -10000)

    def test_export_includes_the_new_sections(self):
        import csv

        from plate_app import analytics

        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory))
            store.add_subscription("59X311111", months=1, amount=100000)
            shift = store.open_shift("thu")
            store.close_shift(shift["id"], counted_cash=100000)
            output = Path(directory) / "report.csv"
            analytics.export_report(store, analytics.DateRange.today_only(), output)
            with output.open(encoding="utf-8-sig", newline="") as handle:
                headings = {row[0] for row in csv.reader(handle) if row}
            self.assertIn("VÉ THÁNG", headings)
            self.assertIn("CA TRỰC", headings)


if __name__ == "__main__":
    unittest.main()
