"""Run configurable news-aware daily trading evaluations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portfolio_agent.agents.hybrid_rule import HybridRuleAgent
from portfolio_agent.agents.llm_allocation import LLMAllocationAgent
from portfolio_agent.agents.ppo_portfolio import NewsTiltedPPOAgent
from portfolio_agent.config import CompetitionConfig, load_config
from portfolio_agent.evaluator import DailyTradingEvaluator
from portfolio_agent.news.store import NewsStore


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run news-aware daily trading evaluation."
    )
    parser.add_argument("--data-root", required=True, help="Market data root directory.")
    parser.add_argument(
        "--config",
        default="configs/evaluation.yaml",
        help="Evaluation config YAML path.",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="Directory where evaluation outputs will be written.",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        help="Override a config value, for example evaluation.horizon_trading_days=5.",
    )
    return parser.parse_args(argv)


def build_agent(agent_name: str, config: CompetitionConfig) -> object:
    if agent_name == "hybrid":
        settings = config.agents.hybrid
        return HybridRuleAgent(
            rebalance_frequency=settings.rebalance_frequency,
            max_positions=settings.max_positions,
            news_weight=settings.news_weight,
            max_asset_weight=config.constraints.max_asset_weight,
        )

    if agent_name == "ppo":
        settings = config.agents.ppo
        return NewsTiltedPPOAgent(
            checkpoint_path=settings.checkpoint_path,
            max_asset_weight=config.constraints.max_asset_weight,
            news_beta=settings.news_beta,
            news_count_gamma=settings.news_count_gamma,
        )

    if agent_name == "llm":
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

    raise ValueError(f"Unknown agent: {agent_name}")


def _write_comparison(output_root: Path, results: dict[str, dict]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    comparison = {
        agent_name: result["metrics"]
        for agent_name, result in results.items()
    }
    with open(output_root / "comparison.json", "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, default=str)

    rows = [
        {"agent": agent_name, **metrics}
        for agent_name, metrics in comparison.items()
    ]
    pd.DataFrame(rows).to_csv(output_root / "comparison.csv", index=False)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    config = load_config(args.config, args.set)
    output_root = Path(args.output_root)
    news_store = NewsStore(config.news.data_dir)
    evaluator = DailyTradingEvaluator(
        data_root=args.data_root,
        config=config,
        news_store=news_store,
    )

    results: dict[str, dict] = {}
    for agent_name in config.agents.enabled:
        print(f"Running evaluation for agent: {agent_name}")
        agent = build_agent(agent_name, config)
        result = evaluator.run_agent(
            agent,
            agent_name,
            output_root / agent_name,
        )
        results[agent_name] = result
        print(
            f"Finished {agent_name}: "
            f"cumulative_return={result['metrics']['m1_cumulative_return']:.4%}"
        )

    _write_comparison(output_root, results)
    print(f"Comparison saved to {output_root}")


if __name__ == "__main__":
    main()

