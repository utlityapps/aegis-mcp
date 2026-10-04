"""Webhook signing (HMAC-SHA256 over timestamp + body) and display-token checks. Stdlib only."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Final

SIGNATURE_HEADER: Final = "x-aegis-signature"
TIMESTAMP_HEADER: Final = "x-aegis-timestamp"
SIGNATURE_VERSION: Final = "v1"
MAX_CLOCK_SKEW_SECONDS: Final = 300
MIN_SECRET_LENGTH: Final = 32


class SignatureError(PermissionError):
    """The request was not signed by a holder of the webhook secret, or the signature is stale."""


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Header value for `x-aegis-signature`: `v1=<hex HMAC-SHA256 of "<timestamp>." + body>`."""
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_VERSION}={mac}"


def verify(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    clock: Callable[[], float] = time.time,
) -> None:
    """Raise SignatureError unless the signature matches and the timestamp is within the allowed skew."""
    if not timestamp or not signature:
        raise SignatureError("missing signature headers")
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        raise SignatureError("malformed timestamp")
    if abs(clock() - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
        raise SignatureError("stale or future timestamp")
    expected = sign(secret, timestamp, body)
    if not signature.isascii() or not hmac.compare_digest(signature.encode(), expected.encode()):
        raise SignatureError("signature mismatch")


def bearer_matches(authorization: str | None, token: str) -> bool:
    if not authorization or not authorization.isascii():
        return False
    scheme, _, presented = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(presented.strip().encode(), token.encode())
