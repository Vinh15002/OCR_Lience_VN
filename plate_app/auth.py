"""Password hashing and roles for operator/admin accounts.

Uses PBKDF2-HMAC-SHA256 from the standard library so there is no extra
dependency and passwords are never stored in the clear.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLES = (ROLE_ADMIN, ROLE_OPERATOR)

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 200_000


def hash_password(password: str, salt: str | None = None, iterations: int = _ITERATIONS) -> str:
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"{_ALGORITHM}${iterations}${salt}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, digest = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(derived.hex(), digest)


@dataclass(frozen=True)
class User:
    username: str
    role: str = ROLE_OPERATOR
    active: bool = True

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN
