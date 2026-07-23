"""Security checks for the private-evaluator to agent boundary."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from typing import Any


FORBIDDEN_KEYS = {
    "ticker",
    "symbol",
    "company_name",
    "timestamp",
    "date",
    "period_end",
    "available_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "shares",
    "raw_path",
    "source_path",
    "revenue",
    "net_income",
    "operating_cash_flow",
    "free_cash_flow",
    "total_assets",
    "total_debt",
    "stockholders_equity",
}


def make_asset_id(ticker: str, secret: bytes, length: int = 12) -> str:
    """Create a stable opaque ID without exposing the ticker."""
    digest = hmac.new(secret, ticker.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"asset_{digest[:length]}"


def _walk(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS:
                location = ".".join(path + (str(key),))
                raise ValueError(f"Sensitive field crossed agent boundary: {location}")
            _walk(child, path + (str(key),))
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _walk(child, path + (str(index),))


def assert_agent_safe_observation(observation: Mapping[str, Any]) -> None:
    """Reject observations containing raw identity, time, price, or filings."""
    _walk(observation)

