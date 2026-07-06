"""Edition and license boundary helpers for Phase 5 #t58."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Literal

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel

Edition = Literal["community", "enterprise"]
LicenseStatus = Literal["not_configured", "active", "expired", "invalid"]
LicenseVerifier = Literal["hmac", "ed25519", "external-http"]

COMMUNITY_FEATURES = frozenset(
    {
        "core_pam",
        "workflow_jit",
        "audit_reports",
    }
)

ENTERPRISE_FEATURES = frozenset(
    {
        "admin_console",
        "license_management",
        "edition_feature_flags",
    }
)

ALL_FEATURES = tuple(sorted(COMMUNITY_FEATURES | ENTERPRISE_FEATURES))


class LicenseSummary(BaseModel):
    configured_edition: Edition
    effective_edition: Edition
    license_status: LicenseStatus
    enabled_features: list[str]
    disabled_features: list[str]
    expires_at: str | None = None


class HttpLicenseValidationClient:
    """Minimal HTTPS adapter for external commercial license validation."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bearer_token: str = "",
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not endpoint_url.startswith("https://"):
            raise ValueError("license validation endpoint must use https")
        self._endpoint_url = endpoint_url
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def validate(self, *, license_key: str) -> dict[str, object] | None:
        if not license_key.strip():
            return None
        headers: dict[str, str] = {}
        if self._bearer_token.strip():
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._endpoint_url,
                    json={"license_key": license_key},
                    headers=headers,
                )
            if response.status_code != 200:
                return None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("active") is False:
            return None
        return payload


def get_license_summary(
    *,
    configured_edition: Edition,
    license_key: str,
    signing_secret: str,
    public_key: str | bytes = "",
    license_verifier: LicenseVerifier = "hmac",
    now: datetime | None = None,
) -> LicenseSummary:
    checked_at = _coerce_utc(now or datetime.now(UTC))
    if configured_edition == "community":
        return _community_summary(configured_edition=configured_edition, status="not_configured")

    payload = _decode_verified_license(
        license_key=license_key,
        signing_secret=signing_secret,
        public_key=public_key,
        license_verifier=license_verifier,
    )
    if payload is None:
        status: LicenseStatus = "not_configured" if not license_key.strip() else "invalid"
        return _community_summary(configured_edition=configured_edition, status=status)

    return _summary_from_verified_payload(
        configured_edition=configured_edition,
        payload=payload,
        checked_at=checked_at,
    )


async def get_license_summary_from_external_verifier(
    *,
    configured_edition: Edition,
    license_key: str,
    client: HttpLicenseValidationClient | None,
    now: datetime | None = None,
) -> LicenseSummary:
    checked_at = _coerce_utc(now or datetime.now(UTC))
    if configured_edition == "community":
        return _community_summary(configured_edition=configured_edition, status="not_configured")
    if client is None:
        status: LicenseStatus = "not_configured" if not license_key.strip() else "invalid"
        return _community_summary(configured_edition=configured_edition, status=status)
    payload = await client.validate(license_key=license_key)
    if payload is None:
        status = "not_configured" if not license_key.strip() else "invalid"
        return _community_summary(configured_edition=configured_edition, status=status)
    return _summary_from_verified_payload(
        configured_edition=configured_edition,
        payload=payload,
        checked_at=checked_at,
    )


def _summary_from_verified_payload(
    *,
    configured_edition: Edition,
    payload: dict[str, object],
    checked_at: datetime,
) -> LicenseSummary:
    expires_at = _parse_expires_at(payload.get("expires_at"))
    if expires_at is None or expires_at <= checked_at:
        return _community_summary(
            configured_edition=configured_edition,
            status="expired",
            expires_at=_format_timestamp(expires_at) if expires_at else None,
        )
    if payload.get("edition") != "enterprise":
        return _community_summary(configured_edition=configured_edition, status="invalid")

    payload_features = payload.get("features", [])
    if not isinstance(payload_features, list):
        return _community_summary(configured_edition=configured_edition, status="invalid")
    licensed_features = {
        str(feature)
        for feature in payload_features
        if str(feature) in ENTERPRISE_FEATURES
    }
    enabled = sorted(COMMUNITY_FEATURES | licensed_features)
    return LicenseSummary(
        configured_edition=configured_edition,
        effective_edition="enterprise",
        license_status="active",
        enabled_features=enabled,
        disabled_features=[feature for feature in ALL_FEATURES if feature not in enabled],
        expires_at=_format_timestamp(expires_at),
    )


def build_license_key(
    *,
    payload: dict[str, object],
    signing_secret: str,
    private_key: Ed25519PrivateKey | None = None,
) -> str:
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if private_key is not None:
        signature = private_key.sign(payload_bytes)
    else:
        signature = hmac.new(signing_secret.encode(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64encode(payload_bytes)}.{_b64encode(signature)}"


def _community_summary(
    *,
    configured_edition: Edition,
    status: LicenseStatus,
    expires_at: str | None = None,
) -> LicenseSummary:
    enabled = sorted(COMMUNITY_FEATURES)
    return LicenseSummary(
        configured_edition=configured_edition,
        effective_edition="community",
        license_status=status,
        enabled_features=enabled,
        disabled_features=[feature for feature in ALL_FEATURES if feature not in enabled],
        expires_at=expires_at,
    )


def _decode_verified_license(
    *,
    license_key: str,
    signing_secret: str,
    public_key: str | bytes,
    license_verifier: LicenseVerifier,
) -> dict[str, object] | None:
    if not license_key.strip() or "." not in license_key:
        return None
    payload_part, signature_part = license_key.split(".", maxsplit=1)
    try:
        payload_bytes = _b64decode(payload_part)
        signature = _b64decode(signature_part)
    except ValueError:
        return None
    if not _verify_license_signature(
        payload_bytes=payload_bytes,
        signature=signature,
        signing_secret=signing_secret,
        public_key=public_key,
        license_verifier=license_verifier,
    ):
        return None
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _verify_license_signature(
    *,
    payload_bytes: bytes,
    signature: bytes,
    signing_secret: str,
    public_key: str | bytes,
    license_verifier: LicenseVerifier,
) -> bool:
    if license_verifier == "hmac":
        if not signing_secret.strip():
            return False
        expected = hmac.new(signing_secret.encode(), payload_bytes, hashlib.sha256).digest()
        return hmac.compare_digest(signature, expected)
    if license_verifier == "external-http":
        return False
    if not public_key:
        return False
    try:
        public_key_bytes = public_key if isinstance(public_key, bytes) else _b64decode(public_key)
        verifier = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        verifier.verify(signature, payload_bytes)
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


def _parse_expires_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _coerce_utc(parsed)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return _coerce_utc(value).isoformat().replace("+00:00", "Z")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise ValueError("invalid base64") from exc
