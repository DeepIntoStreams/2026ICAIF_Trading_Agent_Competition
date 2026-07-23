# News-Aware Trading Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a configurable, point-in-time, news-aware US equity evaluation framework with three baseline agents and M1-M9 metrics.

**Architecture:** Preserve the repo's configured `src/portfolio_agent/` package layout and add focused modules for config, news, market calendar, events, execution, observations, metrics, and scripts. The evaluator owns all data retrieval and filtering, emits market/news/decision/close events, and writes immutable per-agent outputs plus a run manifest with resolved config and data hashes. Agent behavior is controlled through config and CLI overrides so evaluation horizon, news limits, cost assumptions, and LLM settings can be changed without code edits.

**Tech Stack:** Python 3.10+, pandas, numpy, PyYAML, torch, OpenAI Python SDK, stdlib `urllib.request`, stdlib `zoneinfo`, pytest/unittest-compatible tests.

## Global Constraints

- Use `src/portfolio_agent/` as the importable package because `pyproject.toml` sets `package-dir = {"" = "src"}`.
- Do not delete the untracked root-level `portfolio_agent/` directory unless the user gives separate approval.
- Agents may see ticker symbols, company names, and raw news headline/summary/source/url.
- No future leakage: every observation field with time semantics must satisfy `available_at_utc <= current_event_time_utc`.
- Default evaluation horizon is `evaluation.horizon_trading_days = 10`.
- The evaluator must support `evaluation.start_date`, `evaluation.end_date`, and `evaluation.horizon_trading_days` without code edits.
- Default LLM endpoint is `agents.llm.base_url = "http://localhost:8000/v1"`.
- Default LLM API key is `agents.llm.api_key = "unused"`.
- Default LLM model is `agents.llm.model = "google/gemma-4-31B-it"`.
- LLM endpoint, model, temperature, token limit, timeout, and rebalance frequency must be configurable through YAML and CLI overrides.
- Finnhub Company News is the MVP primary news source; use REST polling and record `first_seen_at_utc`.
- Historical news backfills without trustworthy first-seen timestamps must be labeled `historical_backfill_mode = true`.
- MVP execution uses daily OHLCV data: agents can see the current session open and approved history before deciding, but not the same-day high, low, close, adjusted close, or volume.
- Default portfolio rules: USD 1,000,000 initial capital, long-only, max asset weight 30 percent, gross exposure at most 100 percent, fractional shares, fee 10 bps, slippage disabled by default, risk-free rate 0.
- Metrics must report M1 through M9 exactly as specified in `docs/superpowers/specs/2026-07-24-ai-trading-agent-news-evaluation-design.md`.
- Every implementation task must run its focused tests before commit.

---

## File Structure

- Restore: `src/portfolio_agent/**` from the current root-level package or HEAD content, preserving local root-level files.
- Restore: `tests/test_metrics.py`, `tests/test_point_in_time.py`, `tests/test_risk.py`, `tests/test_security_boundary.py` from HEAD so existing behavior can be guarded while it changes.
- Modify: `pyproject.toml` only if pytest import setup proves broken after `src` restoration.
- Modify: `configs/evaluation.yaml` to hold all competition knobs.
- Modify: `scripts/run_evaluation.py` to run all configured agents and accept CLI overrides.
- Create: `scripts/collect_news.py`, `scripts/backfill_news.py`, `scripts/validate_dataset.py`.
- Create: `src/portfolio_agent/config.py` for typed config loading and dot-path overrides.
- Create: `src/portfolio_agent/events.py` for market and news event dataclasses.
- Create: `src/portfolio_agent/market_calendar.py` for session selection and decision cutoff times.
- Create: `src/portfolio_agent/execution.py` for market-on-close target-weight execution.
- Create: `src/portfolio_agent/news/models.py`, `src/portfolio_agent/news/store.py`, `src/portfolio_agent/news/normalizer.py`, `src/portfolio_agent/news/sentiment.py`.
- Create: `src/portfolio_agent/news/providers/base.py`, `src/portfolio_agent/news/providers/finnhub.py`, `src/portfolio_agent/news/providers/sec_edgar.py`, `src/portfolio_agent/news/providers/gdelt.py`.
- Modify: `src/portfolio_agent/security.py` to replace anonymization checks with point-in-time checks that allow ticker, company name, timestamp, and raw news only when available.
- Modify: `src/portfolio_agent/observation.py` to build ticker-aware decision observations with news.
- Modify: `src/portfolio_agent/evaluator.py` to add the event-driven daily evaluator while keeping compatibility wrappers where feasible.
- Modify: `src/portfolio_agent/metrics.py` to return M1-M9 plus sample-size warnings.
- Modify: `src/portfolio_agent/agents/base.py`, `hybrid_rule.py`, `ppo_portfolio.py`, and `llm_allocation.py` to use the event-style interface and news inputs.

## Task 1: Restore Runnable `src` Package Baseline

**Files:**
- Create/restore: `src/portfolio_agent/**`
- Create/restore: `tests/test_metrics.py`
- Create/restore: `tests/test_point_in_time.py`
- Create/restore: `tests/test_risk.py`
- Create/restore: `tests/test_security_boundary.py`
- Create/restore: `configs/evaluation.yaml`
- Create/restore: `scripts/run_evaluation.py`

**Interfaces:**
- Consumes: current root-level `portfolio_agent/**` package and HEAD versions of deleted tracked tests/config/scripts.
- Produces: importable `portfolio_agent` package from `src/portfolio_agent`, runnable current tests, and a known baseline before feature changes.

- [ ] **Step 1: Verify current dirty layout**

Run:

```powershell
git status --short
Test-Path src\portfolio_agent
Test-Path portfolio_agent
```

Expected: tracked `src/portfolio_agent/**` files are deleted, root `portfolio_agent` exists, and no unrelated files are staged.

- [ ] **Step 2: Restore package files mechanically**

Run:

```powershell
New-Item -ItemType Directory -Force src | Out-Null
Copy-Item -Recurse -Force portfolio_agent src\portfolio_agent
git restore --source=HEAD -- tests/test_metrics.py tests/test_point_in_time.py tests/test_risk.py tests/test_security_boundary.py configs/evaluation.yaml scripts/run_evaluation.py
```

Expected: `src/portfolio_agent/__init__.py` exists and imports resolve through the configured `src` layout. The root-level `portfolio_agent/` directory still exists.

- [ ] **Step 3: Run baseline tests**

Run:

```powershell
python -m pytest tests/test_metrics.py tests/test_point_in_time.py tests/test_risk.py tests/test_security_boundary.py -q
```

Expected: existing tests pass before feature edits begin. If imports fail because the editable package is not installed, run `python -m pip install -e .` once, then rerun the same pytest command.

- [ ] **Step 4: Commit restored runnable baseline**

Run:

```powershell
git add src/portfolio_agent tests/test_metrics.py tests/test_point_in_time.py tests/test_risk.py tests/test_security_boundary.py configs/evaluation.yaml scripts/run_evaluation.py
git commit -m "chore: restore src package baseline"
```

Expected: commit includes only restored files needed for runnable implementation. Unrelated deleted docs and README remain untouched.

## Task 2: Add Typed Config And CLI Override Support

**Files:**
- Create: `src/portfolio_agent/config.py`
- Test: `tests/test_config.py`
- Modify: `configs/evaluation.yaml`

**Interfaces:**
- Consumes: YAML config path and CLI overrides shaped as `["a.b.c=value"]`.
- Produces: `load_config(path: str | Path | None, overrides: Sequence[str] | None = None) -> CompetitionConfig`, `config_to_dict(config: CompetitionConfig) -> dict[str, object]`, and `hash_config(config: CompetitionConfig) -> str`.

- [ ] **Step 1: Write config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

from portfolio_agent.config import config_to_dict, hash_config, load_config


def test_default_config_exposes_competition_knobs():
    cfg = load_config(None)
    assert cfg.evaluation.horizon_trading_days == 10
    assert cfg.evaluation.decision_minutes_before_close == 10
    assert cfg.agents.llm.base_url == "http://localhost:8000/v1"
    assert cfg.agents.llm.api_key == "unused"
    assert cfg.agents.llm.model == "google/gemma-4-31B-it"
    assert cfg.news.max_items_per_decision == 40


def test_dot_path_overrides_change_horizon_and_llm(tmp_path: Path):
    cfg_path = tmp_path / "evaluation.yaml"
    cfg_path.write_text(
        "evaluation:\n  horizon_trading_days: 10\nagents:\n  llm:\n    model: old\n",
        encoding="utf-8",
    )
    cfg = load_config(
        cfg_path,
        overrides=[
            "evaluation.horizon_trading_days=20",
            "evaluation.end_date=2026-06-18",
            "agents.llm.model=local/new-model",
            "agents.llm.temperature=0.2",
        ],
    )
    assert cfg.evaluation.horizon_trading_days == 20
    assert cfg.evaluation.end_date == "2026-06-18"
    assert cfg.agents.llm.model == "local/new-model"
    assert cfg.agents.llm.temperature == 0.2


def test_config_hash_uses_resolved_values():
    cfg_a = load_config(None, overrides=["evaluation.horizon_trading_days=5"])
    cfg_b = load_config(None, overrides=["evaluation.horizon_trading_days=10"])
    assert hash_config(cfg_a) != hash_config(cfg_b)
    assert config_to_dict(cfg_a)["evaluation"]["horizon_trading_days"] == 5
```

- [ ] **Step 2: Run tests to confirm they fail**

Run:

```powershell
python -m pytest tests/test_config.py -q
```

Expected: fails with missing `portfolio_agent.config`.

- [ ] **Step 3: Implement config module**

Create `src/portfolio_agent/config.py` with these concrete interfaces:

```python
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml


@dataclass
class EvaluationSettings:
    initial_cash: float = 1_000_000.0
    pre_roll_days: int = 60
    horizon_trading_days: int = 10
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
    max_asset_weight: float = 0.30
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
    parsed = yaml.safe_load(value)
    return parsed


def _apply_override(data: dict[str, Any], override: str) -> None:
    if "=" not in override:
        raise ValueError(f"Override must be name=value: {override}")
    dotted, raw_value = override.split("=", 1)
    parts = [p for p in dotted.split(".") if p]
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
    ev = EvaluationSettings(**data.get("evaluation", {}))
    constraints = ConstraintSettings(**data.get("constraints", {}))
    news = NewsSettings(**data.get("news", {}))
    agents_data = data.get("agents", {})
    agents = AgentSettings(
        enabled=agents_data.get("enabled", ["hybrid", "ppo", "llm"]),
        hybrid=HybridAgentSettings(**agents_data.get("hybrid", {})),
        ppo=PPOAgentSettings(**agents_data.get("ppo", {})),
        llm=LLMAgentSettings(**agents_data.get("llm", {})),
    )
    return CompetitionConfig(ev, constraints, news, agents)


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
    payload = json.dumps(config_to_dict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Update default YAML**

Replace `configs/evaluation.yaml` with keys matching `CompetitionConfig`. Keep values aligned with the spec:

```yaml
evaluation:
  initial_cash: 1000000
  pre_roll_days: 60
  horizon_trading_days: 10
  start_date:
  end_date:
  decision_minutes_before_close: 10
  fee_rate: 0.001
  slippage_bps: 0.0
  annualization: 252
  risk_free_rate: 0.0
constraints:
  long_only: true
  max_asset_weight: 0.30
  max_gross_exposure: 1.00
news:
  providers: ["finnhub"]
  data_dir: "data/news"
  max_items_per_decision: 40
  include_raw_text: true
  historical_backfill_mode: false
  final_poll_minutes_before_cutoff: 5
agents:
  enabled: ["hybrid", "ppo", "llm"]
  hybrid:
    news_weight: 0.20
    rebalance_frequency: 1
    max_positions: 8
  ppo:
    checkpoint_path:
    news_beta: 0.30
    news_count_gamma: 0.05
  llm:
    base_url: "http://localhost:8000/v1"
    api_key: "unused"
    model: "google/gemma-4-31B-it"
    temperature: 0.0
    max_tokens: 4096
    timeout_seconds: 60.0
    rebalance_frequency: 1
```

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_config.py -q
```

Expected: all config tests pass.

Commit:

```powershell
git add src/portfolio_agent/config.py tests/test_config.py configs/evaluation.yaml
git commit -m "feat: add configurable evaluation settings"
```

## Task 3: Add News Models, Store, Normalizer, And Sentiment

**Files:**
- Create: `src/portfolio_agent/news/__init__.py`
- Create: `src/portfolio_agent/news/models.py`
- Create: `src/portfolio_agent/news/normalizer.py`
- Create: `src/portfolio_agent/news/store.py`
- Create: `src/portfolio_agent/news/sentiment.py`
- Test: `tests/test_news_store.py`

**Interfaces:**
- Consumes: provider raw dicts and `NewsSettings`.
- Produces: `NewsRecord`, `normalize_finnhub_company_news`, `NewsStore.load_visible`, `score_news_by_ticker`.

- [ ] **Step 1: Write news store tests**

Create `tests/test_news_store.py`:

```python
from datetime import datetime, timezone

from portfolio_agent.news.models import NewsRecord
from portfolio_agent.news.store import NewsStore
from portfolio_agent.news.sentiment import score_news_by_ticker


def _record(news_id: str, ticker: str, available: str, headline: str) -> NewsRecord:
    ts = datetime.fromisoformat(available.replace("Z", "+00:00"))
    return NewsRecord(
        provider="unit",
        provider_news_id=news_id,
        published_at_utc=ts,
        fetched_at_utc=ts,
        first_seen_at_utc=ts,
        available_at_utc=ts,
        tickers=[ticker],
        company_names=[ticker + " Corp"],
        headline=headline,
        summary="",
        source="unit",
        url="https://example.com/" + news_id,
        content_hash="hash-" + news_id,
        raw_path="raw.jsonl",
    )


def test_visible_news_uses_available_at_cutoff(tmp_path):
    store = NewsStore(tmp_path)
    store.write_normalized([
        _record("before", "AAPL", "2026-06-05T18:00:00Z", "AAPL beats estimates"),
        _record("after", "AAPL", "2026-06-05T20:00:00Z", "AAPL guidance cut"),
    ])
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    visible = store.load_visible(["AAPL"], cutoff)
    assert [item.provider_news_id for item in visible] == ["before"]


def test_store_dedupes_by_provider_id_and_hash(tmp_path):
    store = NewsStore(tmp_path)
    one = _record("1", "MSFT", "2026-06-05T18:00:00Z", "MSFT wins contract")
    duplicate_id = _record("1", "MSFT", "2026-06-05T18:01:00Z", "MSFT wins contract")
    duplicate_hash = _record("2", "MSFT", "2026-06-05T18:02:00Z", "MSFT wins contract")
    duplicate_hash.content_hash = one.content_hash
    store.write_normalized([one, duplicate_id, duplicate_hash])
    visible = store.load_visible(["MSFT"], datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc))
    assert len(visible) == 1


def test_simple_sentiment_scores_positive_and_negative_news():
    records = [
        _record("good", "NVDA", "2026-06-05T18:00:00Z", "NVDA raises guidance after earnings beat"),
        _record("bad", "TSLA", "2026-06-05T18:00:00Z", "TSLA faces probe after recall"),
    ]
    scores = score_news_by_ticker(records)
    assert scores["NVDA"].sentiment > 0
    assert scores["TSLA"].sentiment < 0
```

- [ ] **Step 2: Run tests to confirm they fail**

Run:

```powershell
python -m pytest tests/test_news_store.py -q
```

Expected: fails with missing `portfolio_agent.news`.

- [ ] **Step 3: Implement data model and store**

Create the modules with these public signatures:

```python
# src/portfolio_agent/news/models.py
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class NewsRecord:
    provider: str
    provider_news_id: str
    published_at_utc: datetime
    fetched_at_utc: datetime
    first_seen_at_utc: datetime
    available_at_utc: datetime
    tickers: list[str]
    company_names: list[str]
    headline: str
    summary: str
    source: str
    url: str
    content_hash: str
    raw_path: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("published_at_utc", "fetched_at_utc", "first_seen_at_utc", "available_at_utc"):
            data[key] = data[key].isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsRecord":
        converted = dict(data)
        for key in ("published_at_utc", "fetched_at_utc", "first_seen_at_utc", "available_at_utc"):
            converted[key] = datetime.fromisoformat(str(converted[key]).replace("Z", "+00:00"))
        return cls(**converted)


@dataclass
class TickerNewsScore:
    ticker: str
    sentiment: float
    count: int
    latest_available_at_utc: datetime | None


def stable_content_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

```python
# src/portfolio_agent/news/store.py
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import NewsRecord


class NewsStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.normalized_path = self.root / "normalized.jsonl"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def write_raw(self, provider: str, payloads: Iterable[dict]) -> Path:
        path = self.raw_dir / f"{provider}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            for payload in payloads:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        return path

    def write_normalized(self, records: Iterable[NewsRecord]) -> None:
        existing = self.load_all()
        by_key: dict[tuple[str, str], NewsRecord] = {(r.provider, r.provider_news_id): r for r in existing}
        seen_hashes = {r.content_hash for r in existing}
        for record in records:
            key = (record.provider, record.provider_news_id)
            if key in by_key or record.content_hash in seen_hashes:
                continue
            by_key[key] = record
            seen_hashes.add(record.content_hash)
        ordered = sorted(by_key.values(), key=lambda r: (r.available_at_utc, r.provider, r.provider_news_id))
        self.normalized_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.normalized_path, "w", encoding="utf-8") as f:
            for record in ordered:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def load_all(self) -> list[NewsRecord]:
        if not self.normalized_path.exists():
            return []
        records: list[NewsRecord] = []
        with open(self.normalized_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(NewsRecord.from_dict(json.loads(line)))
        return records

    def load_visible(self, tickers: Iterable[str], cutoff_utc: datetime) -> list[NewsRecord]:
        ticker_set = set(tickers)
        visible = [
            record for record in self.load_all()
            if record.available_at_utc <= cutoff_utc and ticker_set.intersection(record.tickers)
        ]
        return sorted(visible, key=lambda r: (r.available_at_utc, r.provider, r.provider_news_id))

    def file_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                hashes[str(path.relative_to(self.root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return hashes
```

- [ ] **Step 4: Implement normalizer and sentiment**

Use `normalize_finnhub_company_news(payloads, ticker, company_name, fetched_at_utc, first_seen_lookup, raw_path, historical_backfill_mode)` in `normalizer.py`. Use `score_news_by_ticker(records)` in `sentiment.py`. The sentiment scorer must be deterministic and local, using keyword weights:

```python
POSITIVE_WORDS = {"beat", "beats", "raise", "raises", "upgrade", "growth", "profit", "record", "wins", "contract"}
NEGATIVE_WORDS = {"miss", "cuts", "cut", "downgrade", "probe", "recall", "lawsuit", "loss", "falls", "warning"}
```

Return one `TickerNewsScore` per ticker with `sentiment = positive_count - negative_count` divided by visible article count.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_news_store.py -q
```

Expected: all news store and sentiment tests pass.

Commit:

```powershell
git add src/portfolio_agent/news tests/test_news_store.py
git commit -m "feat: add point-in-time news store"
```

## Task 4: Add News Provider Clients

**Files:**
- Create: `src/portfolio_agent/news/providers/__init__.py`
- Create: `src/portfolio_agent/news/providers/base.py`
- Create: `src/portfolio_agent/news/providers/finnhub.py`
- Create: `src/portfolio_agent/news/providers/sec_edgar.py`
- Create: `src/portfolio_agent/news/providers/gdelt.py`
- Test: `tests/test_news_providers.py`

**Interfaces:**
- Consumes: provider API keys from environment variables and date windows.
- Produces: `NewsProvider.fetch_company_news(ticker, company_name, start_date, end_date, fetched_at_utc) -> list[NewsRecord]`.

- [ ] **Step 1: Write provider tests with stubbed HTTP**

Create `tests/test_news_providers.py`:

```python
from datetime import date, datetime, timezone

from portfolio_agent.news.providers.finnhub import FinnhubCompanyNewsProvider


def test_finnhub_provider_maps_company_news(monkeypatch):
    payload = [
        {
            "id": 10,
            "datetime": 1780675200,
            "headline": "Apple beats estimates",
            "summary": "Apple reports stronger services revenue.",
            "source": "UnitWire",
            "url": "https://example.com/aapl",
            "related": "AAPL",
        }
    ]

    def fake_get_json(url, params, timeout_seconds):
        assert "company-news" in url
        assert params["symbol"] == "AAPL"
        return payload

    monkeypatch.setattr("portfolio_agent.news.providers.finnhub._get_json", fake_get_json)
    provider = FinnhubCompanyNewsProvider(api_key="unit")
    records = provider.fetch_company_news(
        "AAPL",
        "Apple Inc.",
        date(2026, 6, 5),
        date(2026, 6, 5),
        datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc),
    )
    assert records[0].provider == "finnhub"
    assert records[0].provider_news_id == "10"
    assert records[0].tickers == ["AAPL"]
    assert records[0].company_names == ["Apple Inc."]
    assert records[0].available_at_utc == datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run:

```powershell
python -m pytest tests/test_news_providers.py -q
```

Expected: fails with missing provider modules.

- [ ] **Step 3: Implement provider protocol and Finnhub client**

Use stdlib HTTP to avoid adding a dependency:

```python
# src/portfolio_agent/news/providers/base.py
from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from portfolio_agent.news.models import NewsRecord


class NewsProvider(Protocol):
    def fetch_company_news(
        self,
        ticker: str,
        company_name: str,
        start_date: date,
        end_date: date,
        fetched_at_utc: datetime,
    ) -> list[NewsRecord]:
        ...
```

```python
# src/portfolio_agent/news/providers/finnhub.py
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from portfolio_agent.news.models import NewsRecord, stable_content_hash


FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


def _get_json(url: str, params: dict[str, Any], timeout_seconds: float) -> Any:
    full_url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(full_url, headers={"User-Agent": "portfolio-agent-evaluator/0.1"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


class FinnhubCompanyNewsProvider:
    def __init__(self, api_key: str, timeout_seconds: float = 30.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_company_news(
        self,
        ticker: str,
        company_name: str,
        start_date: date,
        end_date: date,
        fetched_at_utc: datetime,
    ) -> list[NewsRecord]:
        payload = _get_json(
            FINNHUB_COMPANY_NEWS_URL,
            {"symbol": ticker, "from": start_date.isoformat(), "to": end_date.isoformat(), "token": self.api_key},
            self.timeout_seconds,
        )
        records: list[NewsRecord] = []
        for item in payload or []:
            published = datetime.fromtimestamp(int(item["datetime"]), tz=timezone.utc)
            raw_for_hash = {"provider": "finnhub", "ticker": ticker, "payload": item}
            records.append(NewsRecord(
                provider="finnhub",
                provider_news_id=str(item.get("id", stable_content_hash(raw_for_hash))),
                published_at_utc=published,
                fetched_at_utc=fetched_at_utc,
                first_seen_at_utc=fetched_at_utc,
                available_at_utc=fetched_at_utc,
                tickers=[ticker],
                company_names=[company_name],
                headline=str(item.get("headline", "")),
                summary=str(item.get("summary", "")),
                source=str(item.get("source", "")),
                url=str(item.get("url", "")),
                content_hash=stable_content_hash(raw_for_hash),
                raw_path="",
            ))
        return records
```

- [ ] **Step 4: Add SEC and GDELT skeleton providers behind config flags**

Implement `SecEdgarNewsProvider` and `GdeltNewsProvider` with the same protocol and deterministic empty-list behavior when required lookup metadata is unavailable. Do not enable them by default. Their constructors must accept `timeout_seconds: float = 30.0`.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_news_providers.py tests/test_news_store.py -q
```

Expected: provider and store tests pass.

Commit:

```powershell
git add src/portfolio_agent/news/providers tests/test_news_providers.py
git commit -m "feat: add news provider clients"
```

## Task 5: Add Market Calendar, Events, And Close Execution

**Files:**
- Create: `src/portfolio_agent/events.py`
- Create: `src/portfolio_agent/market_calendar.py`
- Create: `src/portfolio_agent/execution.py`
- Test: `tests/test_market_calendar.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: aligned price calendar, `EvaluationSettings`, `ConstraintSettings`.
- Produces: `select_evaluation_sessions`, `session_clock`, `ExecutionEngine.execute_close`.

- [ ] **Step 1: Write calendar tests**

Create `tests/test_market_calendar.py`:

```python
import pandas as pd

from portfolio_agent.config import EvaluationSettings
from portfolio_agent.market_calendar import select_evaluation_sessions, session_clock


def test_select_latest_horizon_from_available_calendar():
    calendar = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"])
    selected = select_evaluation_sessions(calendar, EvaluationSettings(horizon_trading_days=3))
    assert [d.date().isoformat() for d in selected] == ["2026-06-03", "2026-06-04", "2026-06-05"]


def test_end_date_and_horizon_select_last_n_sessions():
    calendar = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"])
    selected = select_evaluation_sessions(calendar, EvaluationSettings(horizon_trading_days=2, end_date="2026-06-04"))
    assert [d.date().isoformat() for d in selected] == ["2026-06-03", "2026-06-04"]


def test_session_clock_regular_close_cutoff():
    clock = session_clock(pd.Timestamp("2026-06-05"), decision_minutes_before_close=10)
    assert clock.open_et.strftime("%H:%M") == "09:30"
    assert clock.decision_cutoff_et.strftime("%H:%M") == "15:50"
    assert clock.close_et.strftime("%H:%M") == "16:00"
```

- [ ] **Step 2: Write execution tests**

Create `tests/test_execution.py`:

```python
import math

from portfolio_agent.execution import ExecutionEngine, PortfolioState


def test_close_execution_rebalances_to_target_weights_with_fees():
    engine = ExecutionEngine(fee_rate=0.001, slippage_bps=0.0)
    state = PortfolioState(cash=1_000_000.0, shares={"AAPL": 0.0, "MSFT": 0.0})
    new_state, trades = engine.execute_close(
        state,
        target_weights={"AAPL": 0.5, "MSFT": 0.25},
        close_prices={"AAPL": 100.0, "MSFT": 50.0},
    )
    assert len(trades) == 2
    assert math.isclose(sum(t.trade_value for t in trades), 750_000.0)
    assert math.isclose(sum(t.fee for t in trades), 750.0)
    assert new_state.cash < 250_000.0
```

- [ ] **Step 3: Run tests to confirm they fail**

Run:

```powershell
python -m pytest tests/test_market_calendar.py tests/test_execution.py -q
```

Expected: fails with missing modules.

- [ ] **Step 4: Implement events, calendar, and execution**

Create dataclasses:

```python
# src/portfolio_agent/events.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from portfolio_agent.news.models import NewsRecord


@dataclass(frozen=True)
class SessionClock:
    session_date: str
    open_et: datetime
    open_utc: datetime
    decision_cutoff_et: datetime
    decision_cutoff_utc: datetime
    close_et: datetime
    close_utc: datetime


@dataclass(frozen=True)
class MarketOpenEvent:
    session_date: str
    event_time_utc: datetime
    open_prices: dict[str, float]


@dataclass(frozen=True)
class NewsEvent:
    session_date: str
    event_time_utc: datetime
    record: NewsRecord


@dataclass(frozen=True)
class DecisionEvent:
    session_date: str
    event_time_utc: datetime


@dataclass(frozen=True)
class MarketCloseEvent:
    session_date: str
    event_time_utc: datetime
    close_prices: dict[str, float]
```

Use `zoneinfo.ZoneInfo("America/New_York")` and `ZoneInfo("UTC")` in `market_calendar.py`. `select_evaluation_sessions` must accept `pd.Timestamp` sequences from actual market data and apply date filters against those available sessions.

Create `PortfolioState`, `Trade`, and `ExecutionEngine` in `execution.py`. Execution must sell first, then buy, apply `fee_rate`, apply `slippage_bps` as a configurable adverse price adjustment, and keep cash non-negative by scaling buys.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_market_calendar.py tests/test_execution.py -q
```

Expected: calendar and execution tests pass.

Commit:

```powershell
git add src/portfolio_agent/events.py src/portfolio_agent/market_calendar.py src/portfolio_agent/execution.py tests/test_market_calendar.py tests/test_execution.py
git commit -m "feat: add daily market events and execution"
```

## Task 6: Update Security And Observation Boundary For Raw News

**Files:**
- Modify: `src/portfolio_agent/security.py`
- Modify: `src/portfolio_agent/observation.py`
- Test: `tests/test_security_boundary.py`
- Test: `tests/test_observation_news.py`

**Interfaces:**
- Consumes: market features, fundamentals, portfolio state, ticker/company metadata, and visible news.
- Produces: `build_decision_observation(...) -> dict[str, object]` and `assert_observation_point_in_time(observation, cutoff_utc)`.

- [ ] **Step 1: Update security tests for the new policy**

Replace the previous "ticker is forbidden" assertion with:

```python
from datetime import datetime, timezone

from portfolio_agent.security import assert_observation_point_in_time


def test_ticker_company_and_raw_news_are_allowed_when_available():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    observation = {
        "event_time_utc": cutoff.isoformat(),
        "assets": [{"ticker": "AAPL", "company_name": "Apple Inc.", "open_price": 100.0}],
        "news": [{"ticker": "AAPL", "headline": "Apple beats estimates", "available_at_utc": cutoff.isoformat()}],
    }
    assert_observation_point_in_time(observation, cutoff)


def test_future_news_is_rejected():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    observation = {
        "event_time_utc": cutoff.isoformat(),
        "news": [{"ticker": "AAPL", "headline": "Future", "available_at_utc": "2026-06-05T20:01:00+00:00"}],
    }
    with pytest.raises(ValueError, match="future"):
        assert_observation_point_in_time(observation, cutoff)
```

Keep `make_asset_id` tests because older agents and audit logs can still use stable IDs.

- [ ] **Step 2: Add news observation tests**

Create `tests/test_observation_news.py`:

```python
from datetime import datetime, timezone

from portfolio_agent.news.models import NewsRecord
from portfolio_agent.observation import build_decision_observation


def test_decision_observation_filters_news_by_cutoff():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    before = NewsRecord("unit", "1", cutoff, cutoff, cutoff, cutoff, ["AAPL"], ["Apple Inc."], "Apple beat", "", "unit", "", "h1", "")
    after_time = datetime(2026, 6, 5, 20, 1, tzinfo=timezone.utc)
    after = NewsRecord("unit", "2", after_time, after_time, after_time, after_time, ["AAPL"], ["Apple Inc."], "Future news", "", "unit", "", "h2", "")
    obs = build_decision_observation(
        session_date="2026-06-05",
        event_time_utc=cutoff,
        universe=[{"ticker": "AAPL", "company_name": "Apple Inc."}],
        open_prices={"AAPL": 100.0},
        market_features={"AAPL": {"return_1d": 0.01}},
        fundamental_features={"AAPL": {"net_margin": 0.2}},
        portfolio={"weights": {"AAPL": 0.0}, "cash_ratio": 1.0, "nav": 1_000_000.0},
        constraints={"max_asset_weight": 0.30},
        news=[before, after],
        max_news_items=40,
    )
    assert [n["provider_news_id"] for n in obs["news"]] == ["1"]
    assert obs["assets"][0]["ticker"] == "AAPL"
```

- [ ] **Step 3: Run tests to confirm failure**

Run:

```powershell
python -m pytest tests/test_security_boundary.py tests/test_observation_news.py -q
```

Expected: fails until security and observation modules are updated.

- [ ] **Step 4: Implement point-in-time assertion and decision observation builder**

In `security.py`, keep `make_asset_id`, remove ticker/company/time from forbidden-field policy, and add recursive timestamp checking:

```python
def assert_observation_point_in_time(observation: Mapping[str, Any], cutoff_utc: datetime) -> None:
    ...
```

The function must parse keys ending in `_at_utc`; if any parsed timestamp is greater than `cutoff_utc`, raise `ValueError("future data crossed agent boundary: <path>")`.

In `observation.py`, keep existing feature functions and add `build_decision_observation`. The builder must sort news by `(available_at_utc, provider, provider_news_id)`, filter by cutoff, cap to `max_news_items`, and serialize datetimes as ISO strings.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_security_boundary.py tests/test_observation_news.py -q
```

Expected: security and observation tests pass.

Commit:

```powershell
git add src/portfolio_agent/security.py src/portfolio_agent/observation.py tests/test_security_boundary.py tests/test_observation_news.py
git commit -m "feat: allow point-in-time raw news observations"
```

## Task 7: Add Event-Driven Daily Evaluator

**Files:**
- Modify: `src/portfolio_agent/evaluator.py`
- Test: `tests/test_daily_evaluator.py`

**Interfaces:**
- Consumes: `CompetitionConfig`, price/fundamental loaders, `NewsStore`, `ExecutionEngine`, and agents implementing `reset`, `observe`, `decide`.
- Produces: `DailyTradingEvaluator.run_agent(agent, agent_name, output_dir) -> dict[str, object]`.

- [ ] **Step 1: Write evaluator sentinel test**

Create `tests/test_daily_evaluator.py` with a tiny synthetic two-asset fixture and sentinel future news:

```python
from datetime import datetime, timezone

import pandas as pd

from portfolio_agent.config import load_config
from portfolio_agent.evaluator import DailyTradingEvaluator
from portfolio_agent.news.models import NewsRecord


class CaptureAgent:
    def __init__(self):
        self.news_seen = []

    def reset(self, context=None):
        self.news_seen = []

    def observe(self, event):
        pass

    def decide(self, observation):
        self.news_seen.extend(item["headline"] for item in observation["news"])
        return {"AAPL": 0.5}


def test_evaluator_does_not_show_post_cutoff_news(tmp_path, monkeypatch):
    cfg = load_config(None, overrides=["evaluation.horizon_trading_days=2", "evaluation.pre_roll_days=0"])
    dates = pd.to_datetime(["2026-06-05", "2026-06-08"])
    prices = {
        "AAPL": pd.DataFrame({"date": dates, "adj_open": [100, 101], "adj_close": [101, 102], "volume": [1, 1]}),
        "MSFT": pd.DataFrame({"date": dates, "adj_open": [50, 51], "adj_close": [51, 52], "volume": [1, 1]}),
    }
    visible_time = datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)
    future_time = datetime(2026, 6, 5, 21, 0, tzinfo=timezone.utc)
    news = [
        NewsRecord("unit", "visible", visible_time, visible_time, visible_time, visible_time, ["AAPL"], ["Apple Inc."], "visible news", "", "unit", "", "h1", ""),
        NewsRecord("unit", "future", future_time, future_time, future_time, future_time, ["AAPL"], ["Apple Inc."], "future news", "", "unit", "", "h2", ""),
    ]
    evaluator = DailyTradingEvaluator.from_frames(
        prices=prices,
        fundamentals=pd.DataFrame(),
        universe={"Technology": ["AAPL", "MSFT"]},
        config=cfg,
        news_records=news,
    )
    agent = CaptureAgent()
    evaluator.run_agent(agent, "capture", tmp_path)
    assert "visible news" in agent.news_seen
    assert "future news" not in agent.news_seen
```

- [ ] **Step 2: Run test to confirm failure**

Run:

```powershell
python -m pytest tests/test_daily_evaluator.py -q
```

Expected: fails with missing `DailyTradingEvaluator`.

- [ ] **Step 3: Implement `DailyTradingEvaluator`**

Add a new class in `evaluator.py` rather than deleting `WalkForwardEvaluator` in the same commit. Required constructor and factory:

```python
class DailyTradingEvaluator:
    def __init__(
        self,
        data_root: str | Path | None,
        config: CompetitionConfig,
        news_store: NewsStore | None = None,
        secret: bytes | None = None,
    ):
        ...

    @classmethod
    def from_frames(
        cls,
        prices: dict[str, pd.DataFrame],
        fundamentals: pd.DataFrame,
        universe: dict[str, list[str]],
        config: CompetitionConfig,
        news_records: list[NewsRecord],
    ) -> "DailyTradingEvaluator":
        ...

    def run_agent(self, agent: Any, agent_name: str, output_dir: str | Path) -> dict[str, Any]:
        ...
```

The run loop must:

- select sessions with `select_evaluation_sessions`
- call `agent.reset(context)` if supported, else `agent.reset()`
- emit market-open events through `agent.observe(event)` when the method exists
- collect visible premarket plus intraday news by cutoff
- build the decision observation with ticker/company/raw news
- call `agent.decide(observation)` once per evaluated session
- sanitize actions with ticker keys
- execute at close through `ExecutionEngine`
- write per-agent `metrics.json`, `daily_nav.csv`, `trades.jsonl`, `actions.jsonl`, `news_seen.jsonl`, `violations.jsonl`, and `run_manifest.json`

- [ ] **Step 4: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_daily_evaluator.py tests/test_observation_news.py tests/test_execution.py -q
```

Expected: evaluator, observation, and execution tests pass.

Commit:

```powershell
git add src/portfolio_agent/evaluator.py tests/test_daily_evaluator.py
git commit -m "feat: add event-driven daily evaluator"
```

## Task 8: Implement M1-M9 Metrics

**Files:**
- Modify: `src/portfolio_agent/metrics.py`
- Modify: `tests/test_metrics.py`

**Interfaces:**
- Consumes: NAV sequence, transaction costs, traded notional, violation days, decision days, annualization, risk-free rate.
- Produces: `compute_metrics(...) -> dict[str, float | int | bool | None]` with keys `m1_cumulative_return` through `m9_violation_rate`.

- [ ] **Step 1: Replace metric tests with M1-M9 fixtures**

Update `tests/test_metrics.py`:

```python
import math

from portfolio_agent.metrics import compute_metrics


def test_metrics_report_m1_to_m9():
    metrics = compute_metrics(
        nav=[100.0, 110.0, 105.0, 120.0],
        total_transaction_cost=1.0,
        total_trade_value=50.0,
        violation_steps=1,
        decision_steps=3,
        annualization=252,
        risk_free_rate=0.0,
    )
    assert math.isclose(metrics["m1_cumulative_return"], 0.20)
    assert math.isclose(metrics["m2_daily_win_rate"], 2 / 3)
    assert metrics["m3_sharpe_ratio"] is not None
    assert metrics["m4_sortino_ratio"] is not None
    assert metrics["m5_maximum_drawdown"] > 0
    assert metrics["m6_value_at_risk_95"] >= 0
    assert metrics["m7_expected_shortfall_95"] >= 0
    assert math.isclose(metrics["m8_turnover"], 50.0 / ((100.0 + 110.0 + 105.0 + 120.0) / 4))
    assert math.isclose(metrics["m8_cost_rate"], 1.0 / ((100.0 + 110.0 + 105.0 + 120.0) / 4))
    assert math.isclose(metrics["m9_violation_rate"], 1 / 3)
    assert metrics["sample_size"] == 3
    assert metrics["low_sample_warning"] is True


def test_zero_volatility_risk_metrics_are_null():
    metrics = compute_metrics([100.0, 100.0, 100.0], decision_steps=2)
    assert metrics["m1_cumulative_return"] == 0.0
    assert metrics["m2_daily_win_rate"] == 0.0
    assert metrics["m3_sharpe_ratio"] is None
    assert metrics["m4_sortino_ratio"] is None
    assert metrics["m5_maximum_drawdown"] == 0.0
```

- [ ] **Step 2: Run tests to confirm failure**

Run:

```powershell
python -m pytest tests/test_metrics.py -q
```

Expected: fails because old metric keys do not match M1-M9.

- [ ] **Step 3: Implement M1-M9**

Update `compute_metrics` with:

```python
def compute_metrics(
    nav: Sequence[float],
    initial_capital: float | None = None,
    total_transaction_cost: float = 0.0,
    total_trade_value: float = 0.0,
    violation_steps: int = 0,
    decision_steps: int | None = None,
    annualization: int = 252,
    risk_free_rate: float = 0.0,
    var_confidence: float = 0.95,
) -> dict[str, float | int | bool | None]:
    ...
```

Return M1-M9 keys plus compatibility aliases `total_return`, `sharpe`, `sortino`, `max_drawdown`, `turnover`, `cost_rate`, and `violation_rate` so older callers do not break during the migration.

- [ ] **Step 4: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_metrics.py -q
```

Expected: metric tests pass.

Commit:

```powershell
git add src/portfolio_agent/metrics.py tests/test_metrics.py
git commit -m "feat: report competition metrics"
```

## Task 9: Make Baseline Agents News-Aware And Configurable

**Files:**
- Modify: `src/portfolio_agent/agents/base.py`
- Modify: `src/portfolio_agent/agents/hybrid_rule.py`
- Modify: `src/portfolio_agent/agents/ppo_portfolio.py`
- Modify: `src/portfolio_agent/agents/llm_allocation.py`
- Test: `tests/test_agents_news.py`

**Interfaces:**
- Consumes: decision observations with `assets`, `news`, `market_features`, `fundamental_features`, `portfolio`, and `constraints`.
- Produces: ticker-keyed target weights for hybrid, PPO, and LLM baselines.

- [ ] **Step 1: Write agent news tests**

Create `tests/test_agents_news.py`:

```python
from portfolio_agent.agents.hybrid_rule import HybridRuleAgent
from portfolio_agent.agents.llm_allocation import LLMAllocationAgent
from portfolio_agent.agents.ppo_portfolio import NewsTiltedPPOAgent


def _observation(news_headline: str):
    return {
        "session_date": "2026-06-05",
        "event_time_utc": "2026-06-05T19:50:00+00:00",
        "assets": [
            {"ticker": "AAPL", "company_name": "Apple Inc.", "open_price": 100.0},
            {"ticker": "MSFT", "company_name": "Microsoft Corp.", "open_price": 50.0},
        ],
        "market_features": {
            "AAPL": {"return_20d": 0.05, "momentum_60d": 0.10, "volatility_20d": 0.02, "sma_distance_50": 0.05},
            "MSFT": {"return_20d": 0.05, "momentum_60d": 0.10, "volatility_20d": 0.02, "sma_distance_50": 0.05},
        },
        "fundamental_features": {
            "AAPL": {"revenue_yoy": 0.05, "fcf_margin": 0.2, "net_margin": 0.2, "roe": 0.2, "debt_to_assets": 0.2},
            "MSFT": {"revenue_yoy": 0.05, "fcf_margin": 0.2, "net_margin": 0.2, "roe": 0.2, "debt_to_assets": 0.2},
        },
        "portfolio": {"weights": {"AAPL": 0.0, "MSFT": 0.0}, "cash_ratio": 1.0, "nav": 1_000_000.0},
        "constraints": {"max_asset_weight": 0.30, "max_gross_exposure": 1.0},
        "news": [{"ticker": "AAPL", "headline": news_headline, "summary": "", "available_at_utc": "2026-06-05T18:00:00+00:00"}],
    }


def test_hybrid_agent_reacts_to_positive_news():
    agent = HybridRuleAgent(rebalance_frequency=1, news_weight=0.20)
    weights = agent.decide(_observation("Apple raises guidance after earnings beat"))
    assert weights.get("AAPL", 0.0) >= weights.get("MSFT", 0.0)


def test_news_tilted_ppo_keeps_output_feasible():
    agent = NewsTiltedPPOAgent(news_beta=0.5, news_count_gamma=0.1)
    weights = agent.decide(_observation("Apple raises guidance after earnings beat"))
    assert sum(weights.values()) <= 1.0
    assert all(value >= 0 for value in weights.values())


def test_llm_agent_uses_configured_endpoint_and_news(monkeypatch):
    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        assert "Apple Inc." in kwargs["user_prompt"]
        assert "Apple raises guidance" in kwargs["user_prompt"]
        return '{"target_weights": {"AAPL": 0.3}}'

    monkeypatch.setattr("portfolio_agent.agents.llm_allocation._call_llm", fake_call_llm)
    agent = LLMAllocationAgent(base_url="http://localhost:8000/v1", model_name="google/gemma-4-31B-it")
    weights = agent.decide(_observation("Apple raises guidance after earnings beat"))
    assert captured["base_url"] == "http://localhost:8000/v1"
    assert captured["model"] == "google/gemma-4-31B-it"
    assert weights == {"AAPL": 0.3}
```

- [ ] **Step 2: Run tests to confirm failure**

Run:

```powershell
python -m pytest tests/test_agents_news.py -q
```

Expected: fails until agents consume ticker-keyed news observations.

- [ ] **Step 3: Update base agent interface**

Modify `BaseAgent` so it accepts optional context and events while remaining backward-compatible:

```python
class BaseAgent(abc.ABC):
    def reset(self, context: dict[str, Any] | None = None) -> None:
        pass

    def observe(self, event: Any) -> None:
        pass

    @abc.abstractmethod
    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        ...
```

- [ ] **Step 4: Update hybrid and PPO agents**

Hybrid:

- accept `news_weight: float = 0.20`
- compute ticker-level news scores with `score_news_by_ticker`
- use score weights `technical=0.55`, `fundamental=0.25`, `news=0.20`
- redistribute `news_weight` when a ticker has no visible news

PPO:

- keep `PPOPortfolioAgent` for compatibility
- add `NewsTiltedPPOAgent(PPOPortfolioAgent)` with `news_beta` and `news_count_gamma`
- transform base weights with `adjusted_logit_i = log(base_weight_i + epsilon) + beta * sentiment_i + gamma * log(1 + count_i)`
- cap and normalize through the existing risk layer

- [ ] **Step 5: Update LLM agent**

Change defaults:

```python
model_name: str = "google/gemma-4-31B-it"
base_url: str = "http://localhost:8000/v1"
api_key: str = "unused"
temperature: float = 0.0
timeout_seconds: float = 60.0
```

Prompt must include ticker, company name, raw visible news headline and summary, and current portfolio. `_call_llm` must pass `timeout=timeout_seconds` into `OpenAI(...)` or the chat completion call, depending on OpenAI SDK support in the installed version.

- [ ] **Step 6: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_agents_news.py -q
```

Expected: agent news tests pass.

Commit:

```powershell
git add src/portfolio_agent/agents tests/test_agents_news.py
git commit -m "feat: make baseline agents news-aware"
```

## Task 10: Add Scripts For Collection, Validation, And Multi-Agent Runs

**Files:**
- Create: `scripts/collect_news.py`
- Create: `scripts/backfill_news.py`
- Create: `scripts/validate_dataset.py`
- Modify: `scripts/run_evaluation.py`
- Test: `tests/test_scripts_config.py`

**Interfaces:**
- Consumes: `--config`, repeated `--set key=value`, data root, output root, provider API keys.
- Produces: normalized news files, validation reports, per-agent outputs, and cross-agent comparison files.

- [ ] **Step 1: Write script config tests**

Create `tests/test_scripts_config.py`:

```python
from scripts.run_evaluation import parse_args


def test_run_evaluation_accepts_repeated_overrides():
    args = parse_args([
        "--data-root", "data/stock_data_1y",
        "--output-root", "outputs/test",
        "--set", "evaluation.horizon_trading_days=5",
        "--set", "agents.llm.model=local/model",
    ])
    assert args.set == ["evaluation.horizon_trading_days=5", "agents.llm.model=local/model"]
```

- [ ] **Step 2: Run test to confirm failure**

Run:

```powershell
python -m pytest tests/test_scripts_config.py -q
```

Expected: fails until script exposes `parse_args`.

- [ ] **Step 3: Implement `run_evaluation.py`**

Required CLI:

```text
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval --set evaluation.horizon_trading_days=5 --set agents.llm.model=local/model
```

The script must:

- call `load_config(args.config, args.set)`
- instantiate configured agents from `agents.enabled`
- load `NewsStore(config.news.data_dir)`
- run `DailyTradingEvaluator` once per agent
- write `comparison.csv` and `comparison.json`
- write each agent's resolved config and manifest

- [ ] **Step 4: Implement collection and validation scripts**

`collect_news.py` must:

- accept `--config`, `--data-root`, `--provider finnhub`, `--once`, and repeated `--set`
- read `FINNHUB_API_KEY` from the environment
- fetch each ticker for the current UTC date or configured date window
- write raw and normalized files through `NewsStore`

`backfill_news.py` must:

- accept `--from-date`, `--to-date`, `--historical-backfill-mode`
- force manifest labeling when first-seen timestamps are synthetic

`validate_dataset.py` must:

- check that market dates cover the resolved evaluation window
- check that every news record has `available_at_utc`
- check that no output `news_seen.jsonl` contains news after decision cutoff
- write `news_coverage_report.json`

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_scripts_config.py -q
```

Expected: script config tests pass.

Commit:

```powershell
git add scripts/collect_news.py scripts/backfill_news.py scripts/validate_dataset.py scripts/run_evaluation.py tests/test_scripts_config.py
git commit -m "feat: add configurable evaluation scripts"
```

## Task 11: Integration Replay And Reproducibility Checks

**Files:**
- Modify: `src/portfolio_agent/evaluator.py`
- Modify: `scripts/run_evaluation.py`
- Create or update: `tests/test_reproducibility.py`

**Interfaces:**
- Consumes: local `data/stock_data_1y`, `configs/evaluation.yaml`, and normalized news when available.
- Produces: deterministic output directories and run manifests.

- [ ] **Step 1: Write reproducibility test**

Create `tests/test_reproducibility.py`:

```python
import json

from portfolio_agent.config import load_config
from portfolio_agent.evaluator import build_run_manifest


def test_run_manifest_records_resolved_config_and_llm_settings(tmp_path):
    cfg = load_config(None, overrides=["evaluation.horizon_trading_days=5", "agents.llm.model=local/model"])
    manifest = build_run_manifest(
        config=cfg,
        agent_name="llm",
        market_hashes={"AAPL.csv": "abc"},
        news_hashes={"normalized.jsonl": "def"},
        code_state={"commit": "unit", "dirty": True},
    )
    assert manifest["resolved_config"]["evaluation"]["horizon_trading_days"] == 5
    assert manifest["resolved_config"]["agents"]["llm"]["model"] == "local/model"
    assert manifest["news_hashes"]["normalized.jsonl"] == "def"
```

- [ ] **Step 2: Run test to confirm failure**

Run:

```powershell
python -m pytest tests/test_reproducibility.py -q
```

Expected: fails until manifest builder exists.

- [ ] **Step 3: Implement manifest builder and reproducible ordering**

Add:

```python
def build_run_manifest(
    config: CompetitionConfig,
    agent_name: str,
    market_hashes: dict[str, str],
    news_hashes: dict[str, str],
    code_state: dict[str, object],
) -> dict[str, object]:
    ...
```

The manifest must include `hash_config(config)`, `config_to_dict(config)`, `agent_name`, LLM endpoint and model for LLM runs, market hashes, news hashes, code state, `historical_backfill_mode`, and UTC creation timestamp.

- [ ] **Step 4: Run unit test suite**

Run:

```powershell
python -m pytest tests -q
```

Expected: all unit tests pass.

- [ ] **Step 5: Run 5-day smoke replay without LLM**

Run:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/smoke_5d --set evaluation.horizon_trading_days=5 --set agents.enabled=["hybrid","ppo"]
```

Expected: `outputs/smoke_5d/comparison.csv`, `comparison.json`, and per-agent output folders exist. Metrics include M1-M9.

- [ ] **Step 6: Run default 10-day mechanics replay without LLM**

Run:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/mechanics_10d --set agents.enabled=["hybrid","ppo"]
```

Expected: output covers 10 selected sessions and manifests record `horizon_trading_days = 10`.

- [ ] **Step 7: Run optional LLM smoke when local server is available**

Run:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/llm_smoke_2d --set evaluation.horizon_trading_days=2 --set agents.enabled=["llm"] --set agents.llm.base_url=http://localhost:8000/v1 --set agents.llm.model=google/gemma-4-31B-it
```

Expected if local server is running: LLM output exists and `run_manifest.json` records the configured endpoint and model. If the local server is unavailable, record the connection error and keep the non-LLM replay as the verified runnable path.

- [ ] **Step 8: Commit integration and manifest work**

Run:

```powershell
git add src/portfolio_agent/evaluator.py scripts/run_evaluation.py tests/test_reproducibility.py
git commit -m "feat: add reproducible evaluation manifests"
```

Expected: final feature commits contain code, tests, scripts, and config for the MVP.

## Task 12: Final Verification And Handoff Notes

**Files:**
- Modify: `docs/superpowers/specs/2026-07-24-ai-trading-agent-news-evaluation-design.md` only if verification reveals a mismatch between implementation and spec.
- Create: `docs/news_evaluation_usage.md`

**Interfaces:**
- Consumes: implemented CLI and latest verification outputs.
- Produces: concise operator instructions for changing horizon, dates, news limits, and LLM settings.

- [ ] **Step 1: Write usage doc**

Create `docs/news_evaluation_usage.md` with:

````markdown
# News Evaluation Usage

Run a default 10-session replay:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval
```

Run a 5-session experiment:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval_5d --set evaluation.horizon_trading_days=5
```

Change the LLM model:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval_llm --set agents.enabled=["llm"] --set agents.llm.base_url=http://localhost:8000/v1 --set agents.llm.model=google/gemma-4-31B-it
```

Use explicit dates:

```powershell
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/news_eval_dates --set evaluation.start_date=2026-06-05 --set evaluation.end_date=2026-06-18
```
````

- [ ] **Step 2: Run final verification**

Run:

```powershell
python -m pytest tests -q
python scripts/run_evaluation.py --data-root data/stock_data_1y --config configs/evaluation.yaml --output-root outputs/final_smoke --set evaluation.horizon_trading_days=5 --set agents.enabled=["hybrid","ppo"]
python scripts/validate_dataset.py --config configs/evaluation.yaml --data-root data/stock_data_1y --output-root outputs/final_smoke --set evaluation.horizon_trading_days=5
```

Expected: tests pass, smoke output exists, and validation reports no future-news leakage.

- [ ] **Step 3: Commit usage doc**

Run:

```powershell
git add docs/news_evaluation_usage.md
git commit -m "docs: add news evaluation usage"
```

Expected: usage doc is committed after verification.

## Plan Self-Review Results

- Spec coverage: covered news ingestion, configurable evaluation horizon, event-driven daily simulation, point-in-time observations, three baseline agents, M1-M9 metrics, outputs, manifests, scripts, and verification.
- Configurability coverage: `evaluation.horizon_trading_days`, `start_date`, `end_date`, decision cutoff, fees, slippage, news limits, enabled agents, and LLM parameters all flow through config and CLI overrides.
- Scope check: this remains one MVP because each task produces a runnable layer used by the next task. Paid feeds, intraday bars, PPO retraining, and ranking weights remain outside this plan.
- Type consistency: public names used across tasks are `CompetitionConfig`, `NewsRecord`, `NewsStore`, `DailyTradingEvaluator`, `ExecutionEngine`, `build_decision_observation`, `assert_observation_point_in_time`, and `compute_metrics`.
