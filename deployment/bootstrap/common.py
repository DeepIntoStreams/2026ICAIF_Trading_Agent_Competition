"""Shared helpers for reproducible bootstrap commands."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path(
    os.environ.get("COMPETITION_CONFIG_PATH", HERE / "bootstrap_config.json")
)
RUN_RECORDS = HERE / "run_records"


def load_config(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    config = json.loads(raw)
    if not isinstance(config.get("calendar"), str) or not config["calendar"].strip():
        raise ValueError("config.calendar must be a non-empty string")
    instruments = config.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        raise ValueError("config.instruments must be a non-empty list")
    for item in instruments:
        if not isinstance(item, dict):
            raise ValueError("every config.instruments item must be an object")
        for field in ("ticker", "company_name", "sector"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"config.instruments[].{field} must be non-empty")
    tickers = [item["ticker"] for item in instruments]
    if len(tickers) != len(set(tickers)):
        raise ValueError("config.instruments contains duplicate tickers")
    if Decimal(str(config.get("initial_capital_usd", "0"))) <= 0:
        raise ValueError("config.initial_capital_usd must be positive")
    constraints = config.get("constraints")
    if not isinstance(constraints, dict):
        raise ValueError("config.constraints must be an object")
    required_constraints = {
        "long_only",
        "max_asset_weight",
        "max_gross_exposure",
        "fee_rate",
        "slippage_bps",
    }
    missing_constraints = sorted(required_constraints - set(constraints))
    if missing_constraints:
        raise ValueError(f"config.constraints is missing {missing_constraints}")
    if not isinstance(constraints["long_only"], bool):
        raise ValueError("config.constraints.long_only must be boolean")
    max_asset = Decimal(str(constraints["max_asset_weight"]))
    max_gross = Decimal(str(constraints["max_gross_exposure"]))
    fee_rate = Decimal(str(constraints["fee_rate"]))
    slippage = Decimal(str(constraints["slippage_bps"]))
    if max_asset <= 0 or max_gross <= 0 or max_asset > max_gross:
        raise ValueError("config portfolio weight constraints are inconsistent")
    if fee_rate < 0 or slippage < 0:
        raise ValueError("config fee_rate and slippage_bps must be non-negative")
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
