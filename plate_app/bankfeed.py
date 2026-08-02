"""Automatic payment confirmation from a bank-transaction feed.

The QR window can only ever say "a code was shown"; it cannot know the money
arrived. This module closes that gap by reading incoming transfers from a
bank-feed service (SePay or Casso) and matching them back to the visit that
was billed, using the transfer note the QR carries (`GX<visit id> <plate>`).

Network access lives in the feed classes; the matching logic below them is
pure so it can be unit tested without a bank account.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

PROVIDER_NONE = "none"
PROVIDER_SEPAY = "sepay"
PROVIDER_CASSO = "casso"
PROVIDERS = (PROVIDER_NONE, PROVIDER_SEPAY, PROVIDER_CASSO)

_NOTE_PATTERN = re.compile(r"GX\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class BankTransaction:
    """One incoming transfer, normalised across providers."""

    id: str
    amount: float          # positive = money in
    content: str           # transfer note written by the payer
    when: str = ""
    reference: str = ""

    @property
    def visit_hint(self) -> int | None:
        """Visit id encoded in the transfer note, if the payer kept it."""
        match = _NOTE_PATTERN.search(self.content or "")
        return int(match.group(1)) if match else None


def match_transaction(
    transaction: BankTransaction,
    pending: dict[int, float],
    tolerance: float = 0.0,
) -> int | None:
    """Which unpaid visit this transfer settles, or None.

    `pending` maps visit id -> amount owed. A note carrying the visit id wins;
    otherwise the transfer is only accepted when exactly one unpaid visit owes
    that amount — never guess between two identical bills.
    """
    if transaction.amount <= 0 or not pending:
        return None
    hinted = transaction.visit_hint
    if hinted is not None:
        # An explicit GX reference must never fall back to amount matching.
        # Otherwise an archived GX3 transfer could settle GX1 after visit IDs
        # are reused or GX3 has already been paid.
        if hinted in pending and transaction.amount + tolerance >= pending[hinted]:
            return hinted
        return None
    candidates = [
        visit_id
        for visit_id, owed in pending.items()
        if abs(transaction.amount - owed) <= tolerance or transaction.amount == owed
    ]
    return candidates[0] if len(candidates) == 1 else None


def match_all(
    transactions: list[BankTransaction],
    pending: dict[int, float],
    tolerance: float = 0.0,
) -> list[tuple[int, BankTransaction]]:
    """Match a batch, never settling the same visit twice in one pass."""
    remaining = dict(pending)
    matches: list[tuple[int, BankTransaction]] = []
    for transaction in transactions:
        visit_id = match_transaction(transaction, remaining, tolerance)
        if visit_id is not None:
            matches.append((visit_id, transaction))
            remaining.pop(visit_id, None)
    return matches


def _local_datetime(value: str) -> datetime | None:
    """Parse provider/app timestamps into comparable local naive datetimes."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def match_requested(
    transactions: list[BankTransaction],
    pending: dict[int, float],
    requested_at: dict[int, str],
    tolerance: float = 0.0,
) -> list[tuple[int, BankTransaction]]:
    """Match only transactions created after that visit's QR was requested.

    This prevents an old bank transaction carrying a reused ``GX<visit id>``
    from settling a new visit when the app starts polling for the first time.
    A missing/unparseable provider timestamp is rejected rather than guessed.
    """
    remaining = dict(pending)
    matches: list[tuple[int, BankTransaction]] = []
    for transaction in transactions:
        transaction_at = _local_datetime(transaction.when)
        if transaction_at is None:
            continue
        eligible = {}
        for visit_id, amount in remaining.items():
            request_at = _local_datetime(requested_at.get(visit_id, ""))
            if request_at is not None and transaction_at >= request_at:
                eligible[visit_id] = amount
        visit_id = match_transaction(transaction, eligible, tolerance)
        if visit_id is not None:
            matches.append((visit_id, transaction))
            remaining.pop(visit_id, None)
    return matches


class FeedError(RuntimeError):
    """The feed could not be read (network, token, quota…)."""


def _get_json(url: str, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        raise FeedError(f"HTTP {exc.code}: kiểm tra lại API token") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:  # pragma: no cover
        raise FeedError(f"Không kết nối được dịch vụ ngân hàng: {exc}") from exc
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise FeedError("Dịch vụ trả về dữ liệu không đọc được") from exc


class SePayFeed:
    """https://my.sepay.vn — reads `/userapi/transactions/list`."""

    name = PROVIDER_SEPAY
    base_url = "https://my.sepay.vn/userapi/transactions/list"

    def __init__(self, token: str, account_number: str = "", timeout: float = 10.0):
        self.token = token.strip()
        self.account_number = account_number.strip()
        self.timeout = timeout

    def fetch(self, since_id: str | None = None, limit: int = 20) -> list[BankTransaction]:
        params: dict[str, str] = {"limit": str(limit)}
        if self.account_number:
            params["account_number"] = self.account_number
        if since_id:
            params["since_id"] = str(since_id)
        else:
            # First poll after a restart: only look at today, never the archive.
            params["transaction_date_min"] = (
                (datetime.now() - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
            )
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        payload = _get_json(url, {"Authorization": f"Bearer {self.token}"}, self.timeout)
        return [self._to_transaction(row) for row in payload.get("transactions", [])]

    @staticmethod
    def _to_transaction(row: dict) -> BankTransaction:
        return BankTransaction(
            id=str(row.get("id", "")),
            amount=float(row.get("amount_in") or 0),
            content=str(row.get("transaction_content") or ""),
            when=str(row.get("transaction_date") or ""),
            reference=str(row.get("reference_number") or ""),
        )


class CassoFeed:
    """https://casso.vn — reads `/v2/transactions`."""

    name = PROVIDER_CASSO
    base_url = "https://oauth.casso.vn/v2/transactions"

    def __init__(self, token: str, account_number: str = "", timeout: float = 10.0):
        self.token = token.strip()
        self.account_number = account_number.strip()
        self.timeout = timeout

    def fetch(self, since_id: str | None = None, limit: int = 20) -> list[BankTransaction]:
        params = {
            "pageSize": str(limit),
            "sort": "DESC",
            "fromDate": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        payload = _get_json(url, {"Authorization": f"Apikey {self.token}"}, self.timeout)
        if payload.get("error"):
            raise FeedError(str(payload.get("message") or "Casso trả về lỗi"))
        records = (payload.get("data") or {}).get("records") or []
        transactions = [self._to_transaction(row) for row in records]
        if since_id:
            # Casso has no since_id; drop anything already processed.
            transactions = [item for item in transactions if item.id != str(since_id)]
        return transactions

    @staticmethod
    def _to_transaction(row: dict) -> BankTransaction:
        return BankTransaction(
            id=str(row.get("id", "")),
            amount=float(row.get("amount") or 0),
            content=str(row.get("description") or ""),
            when=str(row.get("when") or ""),
            reference=str(row.get("tid") or ""),
        )


def build_feed(config) -> SePayFeed | CassoFeed | None:
    """Feed described by the app config, or None when auto-confirm is off."""
    provider = str(getattr(config, "payment_provider", PROVIDER_NONE) or "").strip().lower()
    token = str(getattr(config, "payment_api_token", "") or "").strip()
    if provider not in (PROVIDER_SEPAY, PROVIDER_CASSO) or not token:
        return None
    account = str(getattr(config, "bank_account", "") or "")
    if provider == PROVIDER_SEPAY:
        return SePayFeed(token, account)
    return CassoFeed(token, account)
