"""Experiment 3: does the LLM trade on the observation, or on what it knows about AAPL?

LOCAL EXPERIMENT SCRIPT -- not part of the upstream prototype.

Runs the LLM baseline twice over an identical window, seed, and prompt format.
The only difference is asset identity:

  real   -- real tickers and company names, exactly as the evaluator emits them
  alias  -- tickers replaced by ASSET_nn, company names by "Company nn"

Everything numeric (prices, market features, fundamentals, portfolio state,
constraints) is untouched, so any behavioural difference is attributable to
identity alone. News is disabled in both arms: news text carries company names
in free-form prose, and scrubbing it would confound the identity manipulation
with a change in the news content itself.

Sector is deliberately preserved. It is legitimate data the competition intends
to provide, and hiding it would ablate more than identity.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portfolio_agent.agents.llm_allocation import LLMAllocationAgent
from portfolio_agent.config import load_config
from portfolio_agent.evaluator import DailyTradingEvaluator
from portfolio_agent.news.store import NewsStore

# Observation fields keyed by ticker that must be re-keyed under aliasing.
TICKER_KEYED_FIELDS = ("open_prices", "market_features", "fundamental_features")


class AliasingAgent:
    """Wraps an agent, hiding asset identity behind stable neutral aliases.

    Rewrites the observation on the way in and maps target weights back to real
    tickers on the way out, so the evaluator and its logs are unaffected.
    """

    def __init__(self, inner: Any, seed: int = 1) -> None:
        self.inner = inner
        self.seed = seed
        self.ticker_to_alias: dict[str, str] = {}
        self.alias_to_ticker: dict[str, str] = {}

    def _ensure_map(self, tickers: Sequence[str]) -> None:
        if self.ticker_to_alias:
            return
        # Shuffle so alias index carries no alphabetical signal.
        shuffled = sorted(tickers)
        random.Random(self.seed).shuffle(shuffled)
        for index, ticker in enumerate(shuffled, start=1):
            alias = f"ASSET_{index:02d}"
            self.ticker_to_alias[ticker] = alias
            self.alias_to_ticker[alias] = ticker

    def _alias_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        self._ensure_map(list(observation.get("market_features", {}).keys()))
        to_alias = self.ticker_to_alias
        obs = dict(observation)

        assets = []
        for asset in observation.get("assets", []):
            item = dict(asset)
            ticker = str(item.get("ticker"))
            alias = to_alias.get(ticker, ticker)
            item["ticker"] = alias
            item["company_name"] = f"Company {alias.split('_')[-1]}"
            assets.append(item)
        obs["assets"] = assets

        for field in TICKER_KEYED_FIELDS:
            source = observation.get(field) or {}
            obs[field] = {to_alias.get(str(k), str(k)): v for k, v in source.items()}

        portfolio = dict(observation.get("portfolio") or {})
        weights = portfolio.get("weights") or {}
        portfolio["weights"] = {
            to_alias.get(str(k), str(k)): v for k, v in weights.items()
        }
        obs["portfolio"] = portfolio

        # Both arms run news-free; refuse to run aliased if that ever changes,
        # rather than silently leaking company names through headline text.
        if observation.get("news"):
            raise RuntimeError(
                "Aliased arm received news items. Company names in headline text "
                "would defeat the aliasing; run this experiment with an empty "
                "news store."
            )
        obs["news"] = []
        return obs

    def reset(self, *args: Any, **kwargs: Any) -> Any:
        if hasattr(self.inner, "reset"):
            return self.inner.reset(*args, **kwargs)
        return None

    def observe(self, *args: Any, **kwargs: Any) -> Any:
        if hasattr(self.inner, "observe"):
            return self.inner.observe(*args, **kwargs)
        return None

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        aliased = self._alias_observation(observation)
        action = self.inner.decide(aliased) or {}
        # Unknown keys are passed through untouched so the evaluator still counts
        # them as violations rather than having them silently dropped here.
        return {
            self.alias_to_ticker.get(str(k), str(k)): v for k, v in action.items()
        }

    @property
    def diagnostics(self) -> Any:
        return getattr(self.inner, "diagnostics", None)


def build_llm(config: Any) -> LLMAllocationAgent:
    settings = config.agents.llm
    return LLMAllocationAgent(
        model_name=settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        temperature=settings.temperature,
        rebalance_frequency=settings.rebalance_frequency,
        max_tokens=settings.max_tokens,
        timeout_seconds=settings.timeout_seconds,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--config", default="configs/evaluation.yaml")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--set", action="append", default=[])
    args = parser.parse_args(argv)

    config = load_config(args.config, args.set)
    if config.news.data_dir and Path(config.news.data_dir).glob("*.jsonl"):
        pass  # emptiness is enforced per-decision by AliasingAgent

    output_root = Path(args.output_root)
    news_store = NewsStore(config.news.data_dir)
    evaluator = DailyTradingEvaluator(
        data_root=args.data_root,
        config=config,
        news_store=news_store,
    )

    results: dict[str, dict] = {}
    for arm in ("real", "alias"):
        print(f"Running identity arm: {arm}")
        agent: Any = build_llm(config)
        if arm == "alias":
            agent = AliasingAgent(agent, seed=config.evaluation.seed)
        result = evaluator.run_agent(agent, f"llm_{arm}", output_root / arm)
        results[arm] = result
        print(
            f"Finished {arm}: "
            f"cumulative_return={result['metrics']['m1_cumulative_return']:.4%}"
        )
        if arm == "alias":
            with open(output_root / "alias_map.json", "w", encoding="utf-8") as f:
                json.dump(agent.ticker_to_alias, f, indent=2)

    comparison = {arm: result["metrics"] for arm, result in results.items()}
    with open(output_root / "comparison.json", "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, default=str)
    print(f"Comparison saved to {output_root}")


if __name__ == "__main__":
    main()
