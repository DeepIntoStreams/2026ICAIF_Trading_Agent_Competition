"""Typed configuration loading for competition evaluation runs."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import yaml


@dataclass
class EvaluationSettings:
    initial_cash: float = 1_000_000.0
    pre_roll_days: int = 60
    horizon_trading_days: int = 10
    seed: int = 1
    start_date: str | None = None
    end_date: str | None = None
    decision_minutes_before_close: int = 10
    fee_rate: float = 0.001
    slippage_bps: float = 0.0
    annualization: int = 252
    risk_free_rate: float = 0.0


@dataclass
class ConstraintSettings:
    long_only: bool = True
    max_asset_weight: float = 0.30   # per-asset cap knob (set 0.10 or 0.30)
    max_gross_exposure: float = 1.00


@dataclass
class NewsSettings:
    providers: list[str] = field(default_factory=lambda: ["finnhub"])
    data_dir: str = "data/news"
    max_items_per_decision: int = 40
    include_raw_text: bool = True
    historical_backfill_mode: bool = False
    final_poll_minutes_before_cutoff: int = 5


@dataclass
class HybridAgentSettings:
    news_weight: float = 0.20
    rebalance_frequency: int = 1
    max_positions: int = 8


@dataclass
class PPOAgentSettings:
    checkpoint_path: str | None = None
    seed: int = 1
    news_beta: float = 0.30
    news_count_gamma: float = 0.05


@dataclass
class LLMAgentSettings:
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "unused"
    model: str = "google/gemma-4-31B-it"
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    rebalance_frequency: int = 1


@dataclass
class AgentSettings:
    enabled: list[str] = field(default_factory=lambda: ["hybrid", "ppo", "llm"])
    hybrid: HybridAgentSettings = field(default_factory=HybridAgentSettings)
    ppo: PPOAgentSettings = field(default_factory=PPOAgentSettings)
    llm: LLMAgentSettings = field(default_factory=LLMAgentSettings)


@dataclass
class CompetitionConfig:
    evaluation: EvaluationSettings = field(default_factory=EvaluationSettings)
    constraints: ConstraintSettings = field(default_factory=ConstraintSettings)
    news: NewsSettings = field(default_factory=NewsSettings)
    agents: AgentSettings = field(default_factory=AgentSettings)


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_scalar(value: str) -> Any:
    return yaml.safe_load(value)


def _apply_override(data: dict[str, Any], override: str) -> None:
    if "=" not in override:
        raise ValueError(f"Override must be name=value: {override}")

    dotted, raw_value = override.split("=", 1)
    parts = [part for part in dotted.split(".") if part]
    if not parts:
        raise ValueError(f"Override path is empty: {override}")

    cursor = data
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"Override parent is not a mapping: {dotted}")
        cursor = child
    cursor[parts[-1]] = _parse_scalar(raw_value)


def _from_dict(data: dict[str, Any]) -> CompetitionConfig:
    evaluation_data = dict(data.get("evaluation", {}))
    for key in ("start_date", "end_date"):
        value = evaluation_data.get(key)
        if isinstance(value, (date, datetime)):
            evaluation_data[key] = value.date().isoformat() if isinstance(value, datetime) else value.isoformat()

    evaluation = EvaluationSettings(**evaluation_data)
    constraints = ConstraintSettings(**data.get("constraints", {}))
    news = NewsSettings(**data.get("news", {}))

    agents_data = data.get("agents", {})
    agents = AgentSettings(
        enabled=agents_data.get("enabled", ["hybrid", "ppo", "llm"]),
        hybrid=HybridAgentSettings(**agents_data.get("hybrid", {})),
        ppo=PPOAgentSettings(**agents_data.get("ppo", {})),
        llm=LLMAgentSettings(**agents_data.get("llm", {})),
    )

    return CompetitionConfig(
        evaluation=evaluation,
        constraints=constraints,
        news=news,
        agents=agents,
    )


def config_to_dict(config: CompetitionConfig) -> dict[str, Any]:
    return asdict(config)


def load_config(
    path: str | Path | None,
    overrides: Sequence[str] | None = None,
) -> CompetitionConfig:
    data = config_to_dict(CompetitionConfig())

    if path is not None and Path(path).exists():
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Config root must be a mapping")
        data = _deep_merge(data, loaded)

    for override in overrides or []:
        _apply_override(data, override)

    return _from_dict(data)


def hash_config(config: CompetitionConfig) -> str:
    payload = json.dumps(
        config_to_dict(config),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
