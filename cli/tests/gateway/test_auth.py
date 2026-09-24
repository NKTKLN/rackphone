from __future__ import annotations

import base64
import hashlib

from rackphone.gateway.auth import (
    SCOPE_ADMIN,
    SCOPE_CONTROL,
    SCOPE_READ,
    AccessClaims,
    generate_recovery_codes,
    generate_token,
    generate_totp_secret,
    hash_password,
    hash_token,
    issue_access_token,
    scope_allows,
    totp_code,
    verify_access_token,
    verify_password,
    verify_totp,
)


def encode_scrypt(password: str, n: int, r: int = 8, p: int = 1) -> str:
    salt = b"0123456789abcdef"
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * 2**20
    )
    return "$".join(
        (
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.b64encode(salt).decode(),
            base64.b64encode(derived).decode(),
        )
    )


def test_password_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded) is True


def test_wrong_password_is_rejected() -> None:
    encoded = hash_password("correct password")
    assert verify_password("wrong password", encoded) is False


def test_malformed_password_record_is_rejected() -> None:
    assert verify_password("password", "scrypt$broken") is False
    assert verify_password("password", "unknown$16384$8$1$c2FsdA==$aGFzaA==") is False


def test_password_hashes_use_fresh_salts() -> None:
    assert hash_password("same password") != hash_password("same password")


def test_totp_window_accepts_nearby_steps_only() -> None:
    secret = generate_totp_secret()
    code = totp_code(secret, 1_000_000)
    assert verify_totp(secret, code, 1_000_030) is True
    assert verify_totp(secret, code, 1_000_060) is False


def test_garbage_totp_input_is_rejected() -> None:
    assert verify_totp("not base32!", "123456", 1_000_000) is False
    assert verify_totp(generate_totp_secret(), "12x456", 1_000_000) is False


def test_access_token_round_trip() -> None:
    token = issue_access_token(b"signing-key", 42, SCOPE_CONTROL, 2_000)
    assert verify_access_token(b"signing-key", token, 1_000) == AccessClaims(
        refresh_id=42, scope=SCOPE_CONTROL, expires_at=2_000
    )


def test_tampered_access_token_signature_is_rejected() -> None:
    token = issue_access_token(b"signing-key", 42, SCOPE_READ, 2_000)
    payload, signature = token.split(".")
    replacement = "A" if signature[-1] != "A" else "B"
    tampered = f"{payload}.{signature[:-1]}{replacement}"
    assert verify_access_token(b"signing-key", tampered, 1_000) is None


def test_expired_access_token_is_rejected() -> None:
    token = issue_access_token(b"signing-key", 42, SCOPE_READ, 1_000)
    assert verify_access_token(b"signing-key", token, 1_000) is None


def test_access_token_signed_with_another_key_is_rejected() -> None:
    token = issue_access_token(b"first-key", 42, SCOPE_READ, 2_000)
    assert verify_access_token(b"second-key", token, 1_000) is None


def test_scope_ranking() -> None:
    assert scope_allows(SCOPE_ADMIN, SCOPE_ADMIN) is True
    assert scope_allows(SCOPE_ADMIN, SCOPE_CONTROL) is True
    assert scope_allows(SCOPE_ADMIN, SCOPE_READ) is True
    assert scope_allows(SCOPE_CONTROL, SCOPE_READ) is True
    assert scope_allows(SCOPE_CONTROL, SCOPE_ADMIN) is False
    assert scope_allows(SCOPE_READ, SCOPE_CONTROL) is False
    assert scope_allows("unknown", SCOPE_READ) is False
    assert scope_allows(SCOPE_ADMIN, "unknown") is False


def test_password_verifies_against_the_records_own_cost() -> None:
    # Raising the profile must not invalidate what is already stored.
    assert verify_password("stored earlier", encode_scrypt("stored earlier", 2**15))


def test_password_record_demanding_absurd_memory_is_rejected() -> None:
    record = encode_scrypt("whatever", 2**14).split("$")
    record[1] = str(2**30)
    assert verify_password("whatever", "$".join(record)) is False


def test_password_record_with_non_power_of_two_cost_is_rejected() -> None:
    record = encode_scrypt("whatever", 2**14).split("$")
    record[1] = "20000"
    assert verify_password("whatever", "$".join(record)) is False


def test_negative_totp_window_is_rejected() -> None:
    secret = generate_totp_secret()
    code = totp_code(secret, 1_000_000)
    assert verify_totp(secret, code, 1_000_000, window=-1) is False


def test_refresh_tokens_are_unique_and_hash_stably() -> None:
    first, second = generate_token(), generate_token()
    assert first != second
    assert hash_token(first) == hash_token(first)
    assert hash_token(first) != hash_token(second)
    assert len(hash_token(first)) == 64


def test_recovery_codes_are_distinct_and_typable() -> None:
    codes = generate_recovery_codes()
    assert len(codes) == 8
    assert len(set(codes)) == 8
    assert all(len(code) == 11 and code[5] == "-" for code in codes)
    assert not any(set("il1o0") & set(code) for code in codes)
