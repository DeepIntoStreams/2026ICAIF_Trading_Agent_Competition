"""Bounded request intake and lightweight overload protection for the receiver."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request


class RequestBodyTooLarge(ValueError):
    """Raised as soon as a streamed request exceeds the configured byte limit."""


class RequestBodyTimedOut(TimeoutError):
    """Raised when a client does not finish its body within the read deadline."""


class InvalidJsonValue(ValueError):
    """Raised when parsed JSON cannot safely be stored as PostgreSQL JSONB."""


async def read_bounded_body(
    request: Request,
    *,
    max_bytes: int,
    timeout_seconds: float,
) -> bytes:
    """Read an ASGI body without ever buffering more than ``max_bytes``."""

    async def read_stream() -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes:
                raise RequestBodyTooLarge("request body too large")
            chunks.append(chunk)
        return b"".join(chunks)

    try:
        async with asyncio.timeout(timeout_seconds):
            return await read_stream()
    except TimeoutError as exc:
        raise RequestBodyTimedOut("request body read timed out") from exc


def validate_json_tree(
    value: Any,
    *,
    max_depth: int = 32,
    max_nodes: int = 4096,
) -> None:
    """Reject non-finite values and pathologically nested JSON structures."""

    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise InvalidJsonValue("JSON document contains too many values")
        if depth > max_depth:
            raise InvalidJsonValue("JSON document is nested too deeply")
        if isinstance(item, float) and not math.isfinite(item):
            raise InvalidJsonValue("JSON numbers must be finite")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


class AdmissionGate:
    """A non-queuing global cap used before accepting another decision request."""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("admission capacity must be positive")
        self.capacity = capacity
        self._active = 0
        self._lock = asyncio.Lock()

    async def try_enter(self) -> bool:
        async with self._lock:
            if self._active >= self.capacity:
                return False
            self._active += 1
            return True

    async def leave(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    """Small in-process limiter; a trusted gateway remains the multi-worker limit."""

    def __init__(self, capacity: int, window_seconds: float, *, max_keys: int = 10_000):
        if capacity <= 0 or window_seconds <= 0 or max_keys <= 0:
            raise ValueError("rate-limit settings must be positive")
        self.capacity = float(capacity)
        self.refill_per_second = float(capacity) / window_seconds
        self.max_idle_seconds = window_seconds * 2
        self.max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()
        self._operations = 0

    async def allow(self, key: str) -> tuple[bool, int]:
        """Consume one token and return ``(allowed, retry_after_seconds)``."""

        now = time.monotonic()
        async with self._lock:
            self._operations += 1
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_keys:
                    self._discard_idle(now)
                if len(self._buckets) >= self.max_keys:
                    oldest = min(
                        self._buckets, key=lambda item: self._buckets[item].updated_at
                    )
                    self._buckets.pop(oldest, None)
                bucket = _Bucket(self.capacity, now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(
                    self.capacity,
                    bucket.tokens + elapsed * self.refill_per_second,
                )
                bucket.updated_at = now

            if self._operations % 1_024 == 0:
                self._discard_idle(now)
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0
            retry_after = max(1, math.ceil((1.0 - bucket.tokens) / self.refill_per_second))
            return False, retry_after

    def _discard_idle(self, now: float) -> None:
        cutoff = now - self.max_idle_seconds
        stale = [
            key for key, bucket in self._buckets.items()
            if bucket.updated_at < cutoff
        ]
        for key in stale:
            self._buckets.pop(key, None)
