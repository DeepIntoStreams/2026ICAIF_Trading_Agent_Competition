"""Shared helpers for reproducible bootstrap commands."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "bootstrap_config.json"
RUN_RECORDS = HERE / "run_records"


def load_config(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    config = json.loads(raw)
    instruments = config.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        raise ValueError("config.instruments must be a non-empty list")
    tickers = [item["ticker"] for item in instruments]
    if len(tickers) != len(set(tickers)):
        raise ValueError("config.instruments contains duplicate tickers")
    config["_config_sha256"] = hashlib.sha256(raw).hexdigest()
    return config


def safe_database_target(database_url: str) -> str:
    """Return host/database without exposing a password in run records."""
    parts = urlsplit(database_url)
    hostname = parts.hostname or ""
    if parts.port:
        hostname = f"{hostname}:{parts.port}"
    username = f"{parts.username}@" if parts.username else ""
    return urlunsplit((parts.scheme, username + hostname, parts.path, "", ""))


def write_run_record(kind: str, payload: dict[str, Any]) -> Path:
    RUN_RECORDS.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = RUN_RECORDS / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}_{kind}.json"
    document = {"recorded_at_utc": now.isoformat(), **payload}
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
