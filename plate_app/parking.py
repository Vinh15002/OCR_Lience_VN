"""Business rules shared by the paid-parking and access-control modes.

Kept free of any I/O so the decisions and money math can be unit tested on
their own, independent of the database or the UI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

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


@dataclass(frozen=True)
class Tariff:
    """Parking price list. All amounts are in Vietnamese dong."""

    flat_fee: float = 0.0        # charged once per visit
    hourly_fee: float = 0.0      # added per started hour beyond the free window
    free_minutes: int = 0        # grace period before the hourly fee starts

    def fee_for(self, duration_seconds: int | None) -> float:
        if duration_seconds is None or duration_seconds < 0:
            duration_seconds = 0
        fee = float(self.flat_fee)
        if self.hourly_fee > 0:
            billable_minutes = max(0.0, duration_seconds / 60 - self.free_minutes)
            started_hours = math.ceil(billable_minutes / 60) if billable_minutes > 0 else 0
            fee += self.hourly_fee * started_hours
        return round(fee)


@dataclass(frozen=True)
class RegisteredVehicle:
    plate: str
    owner_name: str = ""
    access: str = ALLOW          # ALLOW (whitelist) or DENY (blacklist)
    note: str = ""
    valid_from: str | None = None
    valid_until: str | None = None
    active: bool = True

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
