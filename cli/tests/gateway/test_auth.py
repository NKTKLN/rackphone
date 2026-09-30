from __future__ import annotations

import base64
import hashlib

import pytest

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


def encode_scrypt(  # noqa: PLR0913, PLR0917
    password: str,
    n: int,
    r: int = 8,
    p: int = 1,
    salt: bytes = b"0123456789abcdef",
    dklen: int = 32,
) -> str:
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=128 * 2**20
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


# RFC 6238 appendix B, SHA-1 column: the ASCII key "12345678901234567890". The
# RFC's codes are eight digits; a six-digit code is the same value's low digits.
RFC_6238_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_6238_CODES = [
    (59, "287082"),
    (1_111_111_109, "081804"),
    (1_111_111_111, "050471"),
    (1_234_567_890, "005924"),
    (2_000_000_000, "279037"),
    (20_000_000_000, "353130"),
]


@pytest.mark.parametrize(("at", "expected"), RFC_6238_CODES)
def test_totp_matches_the_rfc_reference_values(at: int, expected: str) -> None:
    # Every other TOTP test checks this module against itself, which a wrong
    # truncation offset passes while no authenticator app ever agrees with it.
    assert totp_code(RFC_6238_SECRET, at) == expected


def test_totp_secret_is_read_regardless_of_case_and_padding() -> None:
    # Apps and people hand secrets back lowercased and without the padding a
    # 16-byte key needs, and both must still decode to the same key.
    unpadded = "GAYTEMZUGU3DOOBZMFRGGZDFMY"
    assert totp_code(unpadded, 59) == "192291"
    assert totp_code(unpadded.lower(), 59) == "192291"
    assert totp_code(RFC_6238_SECRET.lower(), 59) == "287082"


def test_totp_refuses_a_code_from_two_steps_ahead() -> None:
    secret = generate_totp_secret()
    assert verify_totp(secret, totp_code(secret, 1_000_000), 999_970) is True
    assert verify_totp(secret, totp_code(secret, 1_000_060), 1_000_000) is False


def test_a_zero_totp_window_accepts_only_the_current_step() -> None:
    secret = generate_totp_secret()
    code = totp_code(secret, 1_000_000)
    assert verify_totp(secret, code, 1_000_000, window=0) is True
    assert verify_totp(secret, code, 1_000_030, window=0) is False


@pytest.mark.parametrize("at", [0, 15])
def test_totp_verifies_in_the_first_step_after_the_epoch(at: int) -> None:
    # The step before the epoch has a negative counter and must be skipped, not
    # allowed to fail the whole check.
    secret = generate_totp_secret()
    assert verify_totp(secret, totp_code(secret, at), at) is True


@pytest.mark.parametrize(
    ("n", "r", "p"),
    [
        (2**11, 8, 1),  # below the minimum work factor
        (2**12, 33, 1),  # block size above the ceiling
        (2**12, 1, 33),  # parallelism above the ceiling: CPU, not memory
    ],
)
def test_an_honest_record_outside_the_cost_bounds_is_rejected(
    n: int, r: int, p: int
) -> None:
    # Each record really is that password, so only the bounds check can refuse
    # it; scrypt itself would compute and match every one of them.
    assert verify_password("whatever", encode_scrypt("whatever", n, r, p)) is False


@pytest.mark.parametrize(
    ("n", "r", "p"),
    [(2**12, 8, 1), (2**12, 1, 1), (2**12, 32, 1), (2**12, 1, 32)],
)
def test_a_record_on_the_cost_bounds_is_accepted(n: int, r: int, p: int) -> None:
    assert verify_password("whatever", encode_scrypt("whatever", n, r, p)) is True


@pytest.mark.parametrize("field", [4, 5])
def test_a_record_with_non_base64_characters_is_rejected(field: int) -> None:
    # A lenient decoder skips the stray byte and matches anyway, which would
    # make a corrupted record look intact.
    record = encode_scrypt("whatever", 2**12).split("$")
    record[field] = record[field][:4] + "!" + record[field][4:]
    assert verify_password("whatever", "$".join(record)) is False


def test_short_salts_and_hashes_are_rejected_at_their_minimums() -> None:
    def record(salt: bytes, dklen: int) -> str:
        return encode_scrypt("whatever", 2**12, salt=salt, dklen=dklen)

    assert verify_password("whatever", record(b"8 bytes!", 16)) is True
    assert verify_password("whatever", record(b"7 bytes", 32)) is False
    assert verify_password("whatever", record(b"0123456789abcdef", 15)) is False


def test_an_access_token_with_a_stray_character_is_rejected() -> None:
    # A lenient decoder drops the byte and the signature still matches, so one
    # token would have many spellings that all pass.
    token = issue_access_token(b"signing-key", 42, SCOPE_READ, 2_000)
    payload, signature = token.split(".")
    for tampered in (
        f"{payload[:3]}!{payload[3:]}.{signature}",
        f"{payload}.!{signature}",
    ):
        assert verify_access_token(b"signing-key", tampered, 1_000) is None
