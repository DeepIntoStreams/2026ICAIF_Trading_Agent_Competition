"""Security checks for the evaluator-to-agent boundary."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from collections.abc import Mapping, Sequence
from typing import Any


FORBIDDEN_KEYS = {
    "raw_path",
    "source_path",
}


def make_asset_id(ticker: str, secret: bytes, length: int = 12) -> str:
    """Create a stable opaque ID without exposing the ticker."""
    digest = hmac.new(secret, ticker.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"asset_{digest[:length]}"


def _walk_forbidden(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS:
                location = ".".join(path + (str(key),))
                raise ValueError(f"Sensitive field crossed agent boundary: {location}")
            _walk_forbidden(child, path + (str(key),))
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _walk_forbidden(child, path + (str(index),))


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _walk_point_in_time(
    value: Any,
    cutoff_utc: datetime,
    path: tuple[str, ...] = (),
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = path + (key_text,)
            if key_text.endswith("_at_utc"):
                parsed = _parse_time(child)
                if parsed > cutoff_utc:
                    location = ".".join(child_path)
                    raise ValueError(
                        f"future data crossed agent boundary: {location}"
                    )
            _walk_point_in_time(child, cutoff_utc, child_path)
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _walk_point_in_time(child, cutoff_utc, path + (str(index),))


def assert_agent_safe_observation(observation: Mapping[str, Any]) -> None:
    """Reject internal file paths that should never cross the agent boundary."""
    _walk_forbidden(observation)


def assert_observation_point_in_time(
    observation: Mapping[str, Any],
    cutoff_utc: datetime,
) -> None:
    """Reject agent observations containing records available after cutoff."""
    if cutoff_utc.tzinfo is None:
        cutoff_utc = cutoff_utc.replace(tzinfo=timezone.utc)
    cutoff_utc = cutoff_utc.astimezone(timezone.utc)
    _walk_forbidden(observation)
    _walk_point_in_time(observation, cutoff_utc)

