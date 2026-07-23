"""LLM-based portfolio allocation agent.

Converts the agent-safe observation into a compact text summary per asset,
then asks an LLM to return schema-valid JSON target weights.
No raw prices, tickers, dates, or financial amounts cross the boundary.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

from .base import BaseAgent

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a JSON-only portfolio weight calculator.

Given a snapshot of anonymous assets, return a flat JSON mapping each asset_id to its target weight.

Rules: long-only (>=0), max 0.30 per asset, sum <= 1.00, remainder is cash.
Prefer: positive momentum, positive trend, low volatility, good fundamentals.
Avoid: negative momentum, high leverage.

You MUST output EXACTLY this format, nothing else:
{"target_weights": {"asset_id_1": 0.15, "asset_id_2": 0.20}}

No markdown. No explanation. No arrays. Just the JSON object above.\
"""


def _summarize_asset(
    asset_id: str,
    company_name: str,
    mf: dict[str, Any],
    ff: dict[str, Any],
    current_weight: float,
    rank: int,
    news_items: list[dict[str, Any]],
) -> str:
    """One compact line per asset for the LLM prompt."""
    def fmt(v: Any, pct: bool = False) -> str:
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return "n/a"
        if pct:
            return f"{v:+.2%}"
        return f"{v:.3f}"

    parts = [
        f"#{rank} {asset_id} ({company_name})",
        f"mom20={fmt(mf.get('return_20d'), True)}",
        f"mom60={fmt(mf.get('momentum_60d'), True)}",
        f"trend={fmt(mf.get('sma_distance_50'), True)}",
        f"vol={fmt(mf.get('volatility_20d'))}",
        f"vliq={fmt(mf.get('volume_ratio_20'))}",
        f"margin={fmt(ff.get('net_margin'))}",
        f"growth={fmt(ff.get('revenue_yoy'), True)}",
        f"leverage={fmt(ff.get('debt_to_assets'))}",
        f"age={ff.get('report_age_days', 'n/a')}d",
        f"w={current_weight:.3f}",
    ]
    for item in news_items[:3]:
        headline = str(item.get("headline", "")).strip()
        summary = str(item.get("summary", "")).strip()
        if summary:
            parts.append(f"news={headline} :: {summary}")
        elif headline:
            parts.append(f"news={headline}")
    return " | ".join(parts)


def _asset_metadata(observation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = observation.get("assets", [])
    if assets:
        return {str(item["ticker"]): dict(item) for item in assets}
    return {
        str(asset_id): {"ticker": str(asset_id), "company_name": str(asset_id)}
        for asset_id in observation.get("market_features", {})
    }


def _news_by_ticker(observation: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in observation.get("news", []):
        ticker = item.get("ticker")
        tickers = item.get("tickers") or ([ticker] if ticker else [])
        for asset_id in tickers:
            grouped.setdefault(str(asset_id), []).append(dict(item))
    return grouped


def _build_user_prompt(observation: dict[str, Any]) -> str:
    market = observation.get("market_features", {})
    fund = observation.get("fundamental_features", {})
    portfolio = observation.get("portfolio", {})
    weights = portfolio.get("weights", {})
    constraints = observation.get("constraints", {})
    metadata = _asset_metadata(observation)
    news_lookup = _news_by_ticker(observation)

    asset_ids = [asset_id for asset_id in metadata if asset_id in market]
    if not asset_ids:
        asset_ids = sorted(market.keys())

    scores: list[tuple[str, float]] = []
    for aid in asset_ids:
        mf = market.get(aid, {})
        m20 = mf.get("return_20d")
        m60 = mf.get("momentum_60d")
        score = 0.0
        if m20 is not None and math.isfinite(m20):
            score += m20
        if m60 is not None and math.isfinite(m60):
            score += m60
        scores.append((aid, score))
    scores.sort(key=lambda x: x[1], reverse=True)

    lines = [
        f"Step: {observation.get('step_id', '?')}",
        f"NAV ratio: {portfolio.get('nav_ratio', 1.0):.4f}",
        f"Cash: {portfolio.get('cash_ratio', 1.0):.2%}",
        f"Drawdown: {portfolio.get('drawdown', 0.0):.2%}",
        f"Max weight: {constraints.get('max_asset_weight', 0.30)}",
        "",
        "Asset snapshots (ranked by momentum):",
    ]

    for rank, (aid, _) in enumerate(scores, 1):
        mf = market.get(aid, {})
        ff = fund.get(aid, {})
        w = weights.get(aid, 0.0)
        company_name = str(metadata.get(aid, {}).get("company_name", aid))
        lines.append(
            _summarize_asset(
                aid,
                company_name,
                mf,
                ff,
                w,
                rank,
                news_lookup.get(aid, []),
            )
        )

    return "\n".join(lines)


def _extract_json_block(text: str) -> str | None:
    """Find the outermost JSON object, handling nested braces and truncation."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    # Truncated JSON: try to salvage by closing open braces
    fragment = text[start:]
    # Find the last successfully closed brace or last complete number value
    last_good = -1
    d = 0
    for i, ch in enumerate(fragment):
        if ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
            last_good = i
    # Try cutting at last complete numeric value before truncation
    # Look for pattern like: 0.15, or 0.15} which indicates complete entries
    match = None
    for m in re.finditer(r':\s*[\d.]+\s*[,}]', fragment):
        match = m
    if match:
        cut = match.end() - 1
        repaired = fragment[:cut + 1]
        # Recount braces
        open_count = repaired.count("{") - repaired.count("}")
        repaired = repaired.rstrip().rstrip(",")
        repaired += "}" * open_count
        return repaired

    return None


def _parse_llm_response(text: str) -> dict[str, float]:
    text = text.strip()

    code_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if code_match:
        inner = code_match.group(1).strip()
        if inner.startswith("{"):
            text = inner

    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        block = _extract_json_block(text)
        if block:
            try:
                parsed = json.loads(block)
            except json.JSONDecodeError:
                pass

    if parsed is None:
        logger.warning("No JSON object found in LLM response: %.200s", text)
        return {}

    if not isinstance(parsed, dict):
        logger.warning("Unexpected JSON structure: %.200s", text)
        return {}

    result: dict[str, float] = {}

    for wkey in ("target_weights", "weights", "allocations", "portfolio"):
        if wkey in parsed and isinstance(parsed[wkey], dict):
            for k, v in parsed[wkey].items():
                if str(k) in ("cash", "reasoning", "explanation"):
                    continue
                try:
                    result[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            if result:
                return result

    if "actions" in parsed and isinstance(parsed["actions"], list):
        for item in parsed["actions"]:
            aid = item.get("asset") or item.get("asset_id") or ""
            w = item.get("target_weight") or item.get("weight") or 0
            if str(aid) in ("cash",):
                continue
            try:
                result[str(aid)] = float(w)
            except (TypeError, ValueError):
                continue
        if result:
            return result

    for k, v in parsed.items():
        if k in ("reasoning", "explanation", "notes", "cash", "total", "cash_weight"):
            continue
        try:
            result[str(k)] = float(v)
        except (TypeError, ValueError):
            continue

    return result


def _call_llm(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout_seconds: float = 60.0,
) -> str:
    """Call LLM via OpenAI-compatible chat completions API."""
    from openai import OpenAI
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_seconds)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


class LLMAllocationAgent(BaseAgent):
    """Portfolio agent that uses an LLM for allocation decisions."""

    def __init__(
        self,
        model_name: str = "google/gemma-4-31B-it",
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "unused",
        temperature: float = 0.0,
        rebalance_frequency: int = 5,
        max_retries: int = 2,
        max_tokens: int = 4096,
        timeout_seconds: float = 60.0,
    ):
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.rebalance_frequency = rebalance_frequency
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self._last_weights: dict[str, float] = {}
        self._step_count = 0

    def reset(self) -> None:
        self._last_weights = {}
        self._step_count = 0

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        self._step_count += 1

        if (
            self.rebalance_frequency > 1
            and (self._step_count - 1) % self.rebalance_frequency != 0
            and self._last_weights
        ):
            return dict(self._last_weights)

        user_prompt = _build_user_prompt(observation)

        for attempt in range(self.max_retries + 1):
            try:
                text = _call_llm(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=self.model_name,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    timeout_seconds=self.timeout_seconds,
                )
                logger.info("LLM raw response (step %d): %.300s", self._step_count, text)
                weights = _parse_llm_response(text)

                if weights:
                    self._last_weights = weights
                    return weights

                logger.warning(
                    "LLM returned unparseable response (attempt %d/%d): %.300s",
                    attempt + 1,
                    self.max_retries + 1,
                    text,
                )
            except Exception as e:
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    e,
                )

        if self._last_weights:
            return dict(self._last_weights)
        return {}
