"""Credential primitives: passwords, second factors and tokens.

Deliberately stateless. Nothing here opens a database, reads a file or looks at
the clock: every function takes what it needs as an argument, so the policy that
surrounds them - lockouts, revocation, expiry - lives in one place and can be
tested without a store underneath it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
# Bounds for a record being verified rather than written. They exist to stop a
# tampered record demanding gigabytes, not to judge how strong it is.
SCRYPT_MIN_N = 2**12
SCRYPT_MAX_FACTOR = 32
SCRYPT_MAX_MEMORY_BYTES = 128 * 2**20
MIN_SALT_BYTES = 8
MIN_HASH_BYTES = 16
TOTP_STEP = 30
TOTP_DIGITS = 6

SCOPE_READ = "read"
SCOPE_CONTROL = "control"
SCOPE_ADMIN = "admin"

_SCOPE_RANKS = {SCOPE_READ: 1, SCOPE_CONTROL: 2, SCOPE_ADMIN: 3}


@dataclass(frozen=True)
class AccessClaims:
    """Verified claims carried by an access token."""

    refresh_id: int
    scope: str
    expires_at: int


def hash_password(password: str) -> str:
    """Hash a password with the supported scrypt profile.

    Args:
        password: Password to protect.

    Returns:
        str: An encoded scrypt password record.
    """
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAX_MEMORY_BYTES,
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        )
    )


def _is_supported_cost(n: int, r: int, p: int) -> bool:
    # n must be a power of two for scrypt itself; the ceilings cap how much work
    # a record can ask for, since an attacker who can edit it could otherwise
    # turn one login attempt into a memory exhaustion.
    if n < SCRYPT_MIN_N or n & (n - 1):
        return False
    if not 1 <= r <= SCRYPT_MAX_FACTOR or not 1 <= p <= SCRYPT_MAX_FACTOR:
        return False
    return 128 * n * r <= SCRYPT_MAX_MEMORY_BYTES


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against an encoded scrypt record.

    Args:
        password: Candidate password.
        encoded: Encoded password record.

    Returns:
        bool: Whether the candidate matches a valid supported record.
    """
    try:
        scheme, n_text, r_text, p_text, salt_text, hash_text = encoded.split("$")
        if scheme != "scrypt":
            return False
        # The record's own cost is what verifies it. Pinning these to the current
        # constants would mean that raising the profile silently invalidates every
        # password already stored, and the only way back is a reset on the host.
        n, r, p = int(n_text), int(r_text), int(p_text)
        if not _is_supported_cost(n, r, p):
            return False
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(hash_text, validate=True)
        if len(salt) < MIN_SALT_BYTES or len(expected) < MIN_HASH_BYTES:
            return False
        derived = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=SCRYPT_MAX_MEMORY_BYTES,
        )
    except (UnicodeError, ValueError, TypeError, binascii.Error):
        return False
    return hmac.compare_digest(derived, expected)


def generate_totp_secret() -> str:
    """Generate an unpadded base32 TOTP secret.

    Returns:
        str: A secret encoding 20 random bytes.
    """
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode_totp_secret(secret: str) -> bytes:
    padding = "=" * (-len(secret) % 8)
    return base64.b32decode(secret + padding, casefold=True)


def totp_code(secret: str, at: int) -> str:
    """Calculate the TOTP code at a Unix timestamp.

    Args:
        secret: Unpadded base32 TOTP secret.
        at: Unix timestamp in seconds; must not be negative.

    Returns:
        str: The six-digit TOTP code.
    """
    counter = at // TOTP_STEP
    digest = hmac.digest(
        _decode_totp_secret(secret), struct.pack(">Q", counter), "sha1"
    )
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"


def verify_totp(secret: str, code: str, at: int, window: int = 1) -> bool:
    """Check a TOTP code within the permitted clock-drift window.

    Args:
        secret: Unpadded base32 TOTP secret.
        code: Candidate six-digit code.
        at: Unix timestamp in seconds.
        window: Number of 30-second steps accepted on either side.

    Returns:
        bool: Whether the candidate matches an accepted step.
    """
    if len(code) != TOTP_DIGITS or not code.isascii() or not code.isdigit():
        return False
    if window < 0:
        return False
    try:
        return any(
            hmac.compare_digest(code, totp_code(secret, at + offset * TOTP_STEP))
            for offset in range(-window, window + 1)
            if at + offset * TOTP_STEP >= 0
        )
    except (UnicodeError, ValueError, TypeError, binascii.Error, struct.error):
        return False


def generate_recovery_codes(count: int = 8) -> list[str]:
    """Generate typable single-use recovery codes.

    Args:
        count: Number of codes to generate; must not be negative.

    Returns:
        list[str]: Random codes shaped as `xxxxx-xxxxx`.
    """
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return [
        "".join(secrets.choice(alphabet) for _ in range(5))
        + "-"
        + "".join(secrets.choice(alphabet) for _ in range(5))
        for _ in range(count)
    ]


def generate_token() -> str:
    """Generate a refresh token from machine-grade entropy.

    Returns:
        str: A URL-safe refresh token.
    """
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a refresh token for storage.

    Args:
        token: Refresh token to hash.

    Returns:
        str: The SHA-256 digest in hexadecimal.
    """
    # A fast digest is correct because 256 bits of machine entropy leave nothing
    # practical to brute-force; scrypt would add cost without protection.
    return hashlib.sha256(token.encode()).hexdigest()


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    if not value.isascii():
        raise ValueError
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def issue_access_token(key: bytes, refresh_id: int, scope: str, expires_at: int) -> str:
    """Issue a signed stateless access token.

    Args:
        key: HMAC signing key.
        refresh_id: Identifier of the backing refresh token.
        scope: Access scope granted by the token.
        expires_at: Expiration time as a Unix timestamp in seconds.

    Returns:
        str: The encoded payload and its HMAC-SHA256 signature.
    """
    # Signed rather than stored: verifying costs no database read, which is what
    # keeps a reconnecting video session from touching SQLite on every attempt.
    payload = f"v1:{refresh_id}:{scope}:{expires_at}".encode()
    signature = hmac.digest(key, payload, "sha256")
    return f"{_urlsafe_encode(payload)}.{_urlsafe_encode(signature)}"


def verify_access_token(key: bytes, token: str, now: int) -> AccessClaims | None:
    """Verify and decode a stateless access token.

    Args:
        key: HMAC signing key.
        token: Candidate access token.
        now: Current Unix timestamp in seconds.

    Returns:
        AccessClaims | None: Verified claims, or `None` for an invalid token.
    """
    try:
        payload_text, signature_text = token.split(".")
        payload = _urlsafe_decode(payload_text)
        signature = _urlsafe_decode(signature_text)
        expected = hmac.digest(key, payload, "sha256")
        if not hmac.compare_digest(signature, expected):
            return None
        version, refresh_text, scope, expiry_text = payload.decode().split(":")
        if version != "v1":
            return None
        claims = AccessClaims(int(refresh_text), scope, int(expiry_text))
        if claims.expires_at <= now:
            return None
    except (UnicodeError, ValueError, TypeError, binascii.Error):
        return None
    return claims


def scope_allows(held: str, required: str) -> bool:
    """Check whether one scope includes another.

    Args:
        held: Scope granted to the caller.
        required: Scope required by the operation.

    Returns:
        bool: Whether both scopes are known and the held scope is sufficient.
    """
    held_rank = _SCOPE_RANKS.get(held)
    required_rank = _SCOPE_RANKS.get(required)
    return (
        held_rank is not None
        and required_rank is not None
        and held_rank >= required_rank
    )
