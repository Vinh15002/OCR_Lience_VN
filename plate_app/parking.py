"""Business rules shared by the paid-parking and access-control modes.

Kept free of any I/O so the decisions and money math can be unit tested on
their own, independent of the database or the UI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Mapping

# Access outcomes.
ALLOW = "ALLOW"      # registered vehicle inside its valid window
GUEST = "GUEST"      # unknown vehicle, admitted (paid-parking mode)
DENY = "DENY"        # blacklisted, expired, or unknown in registered-only mode

# Gate opens for a paying guest as well as a registered vehicle; a denied
# vehicle keeps the barrier shut.
OPENING_STATUSES = frozenset({ALLOW, GUEST})

# Gate policies.
POLICY_ALL = "all"                      # admit everyone not blacklisted
POLICY_REGISTERED_ONLY = "registered_only"  # admit only registered vehicles

# Vehicle classes. Vietnamese lots price a motorbike, a car and a bicycle
# differently, so every visit carries one of these.
MOTORBIKE = "MOTORBIKE"
CAR = "CAR"
BICYCLE = "BICYCLE"
VEHICLE_TYPES = (MOTORBIKE, CAR, BICYCLE)
VEHICLE_TYPE_LABELS = {MOTORBIKE: "Xe máy", CAR: "Ô tô", BICYCLE: "Xe đạp"}


def normalize_vehicle_type(value: str | None, default: str = MOTORBIKE) -> str:
    text = str(value or "").strip().upper()
    return text if text in VEHICLE_TYPES else default


@dataclass(frozen=True)
class Tariff:
    """Parking price list for one vehicle class. Amounts in Vietnamese dong."""

    flat_fee: float = 0.0        # charged once per visit
    hourly_fee: float = 0.0      # added per started hour beyond the free window
    free_minutes: int = 0        # grace period before the hourly fee starts
    daily_cap: float = 0.0       # ceiling on the hourly part per started 24h; 0 = no cap
    overnight_fee: float = 0.0   # surcharge per night the vehicle stays over
    night_hour: int = 22         # hour of day that starts a "night"

    def fee_for(self, duration_seconds: int | None) -> float:
        """Fee from a duration alone (no overnight surcharge)."""
        return round(self._time_fee(duration_seconds))

    def fee_for_period(
        self,
        entry_at: datetime | None,
        exit_at: datetime | None,
        duration_seconds: int | None = None,
    ) -> float:
        """Fee for a visit, including the per-night surcharge when both ends are known."""
        if duration_seconds is None and entry_at and exit_at:
            duration_seconds = max(0, round((exit_at - entry_at).total_seconds()))
        fee = self._time_fee(duration_seconds)
        if self.overnight_fee > 0:
            fee += self.overnight_fee * self.nights_between(entry_at, exit_at)
        return round(fee)

    def nights_between(self, entry_at: datetime | None, exit_at: datetime | None) -> int:
        """How many times the visit crossed the `night_hour` boundary."""
        if entry_at is None or exit_at is None or exit_at <= entry_at:
            return 0
        hour = max(0, min(23, int(self.night_hour)))
        # First boundary at or after the entry time.
        boundary = entry_at.replace(hour=hour, minute=0, second=0, microsecond=0)
        if boundary <= entry_at:
            boundary += timedelta(days=1)
        nights = 0
        while boundary < exit_at:
            nights += 1
            boundary += timedelta(days=1)
        return nights

    def _time_fee(self, duration_seconds: int | None) -> float:
        if duration_seconds is None or duration_seconds < 0:
            duration_seconds = 0
        fee = float(self.flat_fee)
        if self.hourly_fee > 0:
            billable_minutes = max(0.0, duration_seconds / 60 - self.free_minutes)
            started_hours = math.ceil(billable_minutes / 60) if billable_minutes > 0 else 0
            hourly_part = self.hourly_fee * started_hours
            if self.daily_cap > 0:
                started_days = max(1, math.ceil(duration_seconds / 86400))
                hourly_part = min(hourly_part, self.daily_cap * started_days)
            fee += hourly_part
        return fee


@dataclass(frozen=True)
class TariffTable:
    """The price list of the whole site: one `Tariff` per vehicle class."""

    default: Tariff = field(default_factory=Tariff)
    by_type: Mapping[str, Tariff] = field(default_factory=dict)

    def for_type(self, vehicle_type: str | None) -> Tariff:
        return self.by_type.get(normalize_vehicle_type(vehicle_type), self.default)

    def fee_for_period(
        self,
        vehicle_type: str | None,
        entry_at: datetime | None,
        exit_at: datetime | None,
        duration_seconds: int | None = None,
    ) -> float:
        return self.for_type(vehicle_type).fee_for_period(entry_at, exit_at, duration_seconds)


@dataclass(frozen=True)
class RegisteredVehicle:
    plate: str
    owner_name: str = ""
    access: str = ALLOW          # ALLOW (whitelist) or DENY (blacklist)
    note: str = ""
    valid_from: str | None = None
    valid_until: str | None = None
    active: bool = True
    vehicle_type: str = MOTORBIKE
    phone: str = ""

    def days_left(self, today: date | None = None) -> int | None:
        """Days until the pass expires; None when it never expires."""
        if not self.valid_until:
            return None
        today = today or date.today()
        return (date.fromisoformat(self.valid_until[:10]) - today).days

    def is_valid(self, now: datetime) -> bool:
        if not self.active:
            return False
        # Compare on calendar dates so a timezone-aware "now" never clashes with
        # a plain date string entered in the UI.
        today = now.date() if isinstance(now, datetime) else now
        if self.valid_from and today < date.fromisoformat(self.valid_from[:10]):
            return False
        if self.valid_until and today > date.fromisoformat(self.valid_until[:10]):
            return False
        return True


@dataclass(frozen=True)
class AccessDecision:
    status: str
    reason: str

    @property
    def opens_gate(self) -> bool:
        return self.status in OPENING_STATUSES


def decide_access(
    vehicle: RegisteredVehicle | None,
    policy: str,
    now: datetime,
) -> AccessDecision:
    """Decide whether a recognised plate may pass the gate."""
    if vehicle is not None and vehicle.access == DENY:
        return AccessDecision(DENY, "blacklist")
    if vehicle is not None and vehicle.is_valid(now):
        who = vehicle.owner_name.strip() or vehicle.plate
        return AccessDecision(ALLOW, f"registered:{who}")
    # Unknown plate, or a registered one that is inactive/expired.
    if policy == POLICY_REGISTERED_ONLY:
        reason = "expired" if vehicle is not None else "not_registered"
        return AccessDecision(DENY, reason)
    return AccessDecision(GUEST, "guest")
