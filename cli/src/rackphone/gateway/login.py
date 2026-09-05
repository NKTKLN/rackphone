"""Login policy: who is let in, who is locked out, and for how long.

The decisions live here and nowhere else. `auth` holds the cryptography and
`authstore` holds the rows, so this module is the only place that knows a
lockout climbs a ladder or that a second factor is required at all - and it
reaches none of that through globals: every timestamp arrives as an argument,
so a test can say what time it is.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from enum import StrEnum

from rackphone.gateway.auth import (
    SCOPE_CONTROL,
    AccessClaims,
    generate_recovery_codes,
    generate_token,
    generate_totp_secret,
    hash_token,
    issue_access_token,
    scope_allows,
    verify_access_token,
    verify_password,
    verify_totp,
)
from rackphone.gateway.authstore import AuthStore, next_lockout
from rackphone.gateway.config import GatewayConfig

# Per-IP delay, doubling to a fifteen-minute ceiling. An attacker rotates
# addresses, so this is not a wall - it turns thousands of guesses a minute into
# a handful, which is what makes a password survive being reachable at all.
IP_LADDER = (60, 120, 240, 480, 900)
IP_FAILURE_THRESHOLD = 3
IP_FAILURE_WINDOW_SECONDS = 900
# The account ladder is far slower because it is far more dangerous: it is the
# rung that a stranger can climb on your behalf, so it expires on its own and
# only attempts naming the real account reach it.
ACCOUNT_FAILURE_THRESHOLD = 5
ACCOUNT_FAILURE_WINDOW_SECONDS = 3600


class RefusalReason(StrEnum):
    """Stable reasons why authentication was refused."""

    BAD_CREDENTIALS = "bad_credentials"
    TOTP_REQUIRED = "totp_required"
    BAD_TOTP = "bad_totp"
    LOCKED = "locked"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class Tokens:
    """A refresh token and its matching short-lived access token."""

    refresh: str
    access: str
    scope: str
    refresh_expires_at: int
    access_expires_at: int


@dataclass(frozen=True)
class LoginOutcome:
    """Tokens from a success or the reason authentication was refused."""

    tokens: Tokens | None = None
    reason: RefusalReason | None = None
    # Absolute, not a duration: `retry_after` would read as seconds, which is
    # what the HTTP header of that name carries, and the caller would hand a
    # 1970 timestamp to whoever asked when to come back.
    locked_until: int | None = None


class LoginService:
    """Coordinate authentication primitives with persistent auth state."""

    def __init__(self, config: GatewayConfig, store: AuthStore) -> None:
        """Initialize the login policy service.

        Args:
            config: Gateway credentials and token lifetimes.
            store: Authentication state store.
        """
        self.config = config
        self.store = store
        # Cache the signing key so request authorization never queries SQLite.
        self._access_key = store.access_key()

    @staticmethod
    def _ip_subject(ip: str) -> str:
        return f"ip:{ip}"

    @staticmethod
    def _account_subject(username: str) -> str:
        return f"account:{username}"

    def _tokens(self, refresh: str, refresh_id: int, scope: str, now: int) -> Tokens:
        access_expires_at = now + self.config.access_ttl_seconds
        return Tokens(
            refresh=refresh,
            access=issue_access_token(
                self._access_key, refresh_id, scope, access_expires_at
            ),
            scope=scope,
            refresh_expires_at=now + self.config.refresh_ttl_seconds,
            access_expires_at=access_expires_at,
        )

    def _refuse(
        self, reason: RefusalReason, locked_until: int | None = None
    ) -> LoginOutcome:
        return LoginOutcome(reason=reason, locked_until=locked_until)

    def _record_failure(
        self, username: str, ip: str, now: int, reason: RefusalReason
    ) -> LoginOutcome:
        self.store.record_attempt(ip, username, False, now)
        ip_subject = self._ip_subject(ip)
        account_subject = self._account_subject(self.config.admin.username)
        locks = []

        ip_failures = self.store.failures_since(now - IP_FAILURE_WINDOW_SECONDS, ip=ip)
        if ip_failures >= IP_FAILURE_THRESHOLD:
            lockout = next_lockout(
                ip_subject, self.store.last_level(ip_subject), now, IP_LADDER
            )
            self.store.lock(lockout)
            locks.append(lockout)

        # Only attempts naming the real account count toward its distributed
        # lock, so random usernames cannot deny service to the administrator.
        if hmac.compare_digest(username, self.config.admin.username):
            account_failures = self.store.failures_since(
                now - ACCOUNT_FAILURE_WINDOW_SECONDS,
                username=self.config.admin.username,
            )
            if account_failures >= ACCOUNT_FAILURE_THRESHOLD:
                lockout = next_lockout(
                    account_subject,
                    self.store.last_level(account_subject),
                    now,
                )
                self.store.lock(lockout)
                locks.append(lockout)

        detail = f"reason={reason.value}"
        if locks:
            detail += "; locked=" + ",".join(lockout.subject for lockout in locks)
        self.store.record_audit(
            now, "login_failed", actor=username, subject=ip, detail=detail
        )
        return self._refuse(reason)

    def log_in(  # noqa: PLR0913, PLR0917
        self,
        username: str,
        password: str,
        ip: str,
        device_label: str,
        now: int,
        totp_code: str | None = None,
        recovery_code: str | None = None,
        scope: str = SCOPE_CONTROL,
    ) -> LoginOutcome:
        """Authenticate an administrator and issue a device token pair.

        Args:
            username: Presented administrator username.
            password: Presented administrator password.
            ip: Source IP address used for rate limiting.
            device_label: Non-secret label recorded with the session.
            now: Current Unix timestamp in seconds.
            totp_code: Optional current authenticator code.
            recovery_code: Optional unused single-use recovery code.
            scope: Authorization scope granted to the device.

        Returns:
            LoginOutcome: Issued tokens or a stable refusal reason.
        """
        admin = self.config.admin
        # No counters exist to protect until an administrator has credentials.
        if not admin.is_configured:
            return self._refuse(RefusalReason.NOT_CONFIGURED)

        ip_lock = self.store.active_lock(self._ip_subject(ip), now)
        account_lock = self.store.active_lock(
            self._account_subject(admin.username), now
        )
        if ip_lock is not None or account_lock is not None:
            # Recording here would let any caller perpetually extend a lockout.
            until = max(
                lock.until for lock in (ip_lock, account_lock) if lock is not None
            )
            return self._refuse(RefusalReason.LOCKED, until)

        # Always run the expensive password verifier, even for an unknown name.
        username_ok = hmac.compare_digest(username, admin.username)
        password_ok = verify_password(password, admin.password_hash)
        if not username_ok or not password_ok:
            return self._record_failure(
                username, ip, now, RefusalReason.BAD_CREDENTIALS
            )

        secret = self.store.totp_secret()
        if secret is not None:
            if totp_code is None and recovery_code is None:
                return self._record_failure(
                    username, ip, now, RefusalReason.TOTP_REQUIRED
                )
            totp_ok = totp_code is not None and verify_totp(secret, totp_code, now)
            recovery_ok = (
                not totp_ok
                and recovery_code is not None
                and self.store.consume_recovery_code(hash_token(recovery_code), now)
            )
            if not totp_ok and not recovery_ok:
                return self._record_failure(username, ip, now, RefusalReason.BAD_TOTP)

        self.store.record_attempt(ip, username, True, now)
        self.store.clear_lock(self._ip_subject(ip))
        self.store.clear_lock(self._account_subject(admin.username))
        refresh = generate_token()
        refresh_id = self.store.issue_refresh(
            hash_token(refresh),
            device_label,
            scope,
            now,
            self.config.refresh_ttl_seconds,
        )
        tokens = self._tokens(refresh, refresh_id, scope, now)
        self.store.record_audit(
            now,
            "login",
            actor=admin.username,
            subject=device_label,
            detail=f"scope={scope}",
        )
        return LoginOutcome(tokens=tokens)

    def refresh(self, refresh_token: str, now: int) -> LoginOutcome:
        """Rotate a refresh token and issue a matching access token.

        Args:
            refresh_token: Refresh token to rotate.
            now: Current Unix timestamp in seconds.

        Returns:
            LoginOutcome: Replacement tokens or a refusal.
        """
        old_hash = hash_token(refresh_token)
        session = self.store.resolve_refresh(old_hash, now)
        if session is None:
            return self._refuse(RefusalReason.BAD_CREDENTIALS)
        replacement = generate_token()
        refresh_id = self.store.rotate_refresh(
            old_hash,
            hash_token(replacement),
            now,
            self.config.refresh_ttl_seconds,
        )
        if refresh_id is None:
            return self._refuse(RefusalReason.BAD_CREDENTIALS)
        tokens = self._tokens(replacement, refresh_id, session.scope, now)
        self.store.record_audit(
            now,
            "refresh",
            actor=self.config.admin.username,
            subject=session.device_label,
            detail=f"scope={session.scope}",
        )
        return LoginOutcome(tokens=tokens)

    def authorise(
        self, access_token: str, now: int, required_scope: str
    ) -> AccessClaims | None:
        """Verify an access token and enforce its required scope.

        Args:
            access_token: Signed access token presented by the caller.
            now: Current Unix timestamp in seconds.
            required_scope: Minimum scope required by the operation.

        Returns:
            AccessClaims | None: Verified sufficient claims, or `None`.
        """
        claims = verify_access_token(self._access_key, access_token, now)
        if claims is None or not scope_allows(claims.scope, required_scope):
            return None
        return claims

    def log_out(self, refresh_token: str, now: int) -> bool:
        """Revoke one refresh token and audit the result.

        Args:
            refresh_token: Refresh token to revoke.
            now: Current Unix timestamp in seconds.

        Returns:
            bool: Whether a live token was revoked.
        """
        session = self.store.resolve_refresh(hash_token(refresh_token), now)
        revoked = session is not None and self.store.revoke_refresh(session.id, now)
        self.store.record_audit(
            now,
            "logout",
            actor=self.config.admin.username,
            subject="" if session is None else session.device_label,
            detail=f"revoked={str(revoked).lower()}",
        )
        return revoked

    def log_out_everywhere(self, now: int) -> int:
        """Revoke all live refresh tokens and audit the operation.

        Args:
            now: Current Unix timestamp in seconds.

        Returns:
            int: Number of newly revoked tokens.
        """
        count = self.store.revoke_all(now)
        self.store.record_audit(now, "logout_everywhere", detail=f"count={count}")
        return count

    def enable_totp(self, now: int) -> tuple[str, list[str]]:
        """Enable TOTP and return its one-time enrollment secrets.

        Args:
            now: Current Unix timestamp in seconds.

        Returns:
            tuple[str, list[str]]: TOTP secret and plaintext recovery codes.
        """
        secret = generate_totp_secret()
        recovery_codes = generate_recovery_codes()
        self.store.enable_totp(secret, [hash_token(code) for code in recovery_codes])
        self.store.record_audit(now, "totp_enabled")
        return secret, recovery_codes

    def disable_totp(self, now: int) -> None:
        """Disable TOTP and remove recovery codes.

        Args:
            now: Current Unix timestamp in seconds.
        """
        self.store.disable_totp()
        self.store.record_audit(now, "totp_disabled")
