"""VietQR / NAPAS EMVCo payload generation for parking-fee collection.

Only the payload string is built here (pure + unit-testable). Turning it into a
scannable image is done in the UI with the optional ``qrcode`` package.
"""

from __future__ import annotations

from dataclasses import dataclass


def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def crc16_ccitt(data: str) -> int:
    """CRC-16/CCITT-FALSE, the checksum EMVCo/VietQR uses for tag 63."""
    crc = 0xFFFF
    for byte in data.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class BankAccount:
    bank_bin: str = ""       # 6-digit NAPAS acquirer id, e.g. 970415 (Vietinbank)
    account_number: str = ""
    account_name: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.bank_bin and self.account_number)


def build_vietqr(
    account: BankAccount,
    amount: float | None = None,
    description: str = "",
    service: str = "QRIBFTTA",
) -> str:
    """Return an EMVCo payload string for a VietQR bank transfer.

    ``amount`` present -> dynamic QR (point of initiation 12); otherwise a static
    QR the payer types the amount into.
    """
    beneficiary = _tlv("00", account.bank_bin) + _tlv("01", account.account_number)
    merchant_account = _tlv("00", "A000000727") + _tlv("01", beneficiary) + _tlv("02", service)

    payload = _tlv("00", "01")
    payload += _tlv("01", "12" if amount else "11")
    payload += _tlv("38", merchant_account)
    payload += _tlv("53", "704")  # VND
    if amount:
        payload += _tlv("54", str(int(round(amount))))
    payload += _tlv("58", "VN")
    if description:
        payload += _tlv("62", _tlv("08", description[:25]))
    payload += "6304"  # CRC tag + length, checksum computed over everything up to here
    return payload + f"{crc16_ccitt(payload):04X}"
