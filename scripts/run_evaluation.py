"""Run walk-forward evaluation for one or more agents.

Usage:
    python scripts/run_evaluation.py \
        --data_root stock_data_1y \
        --config configs/evaluation.yaml \
        --agents hybrid_rule ppo llm \
        --output_dir results

The evaluation data (stock_data_1y) is loaded exclusively by the evaluator.
Agent code never sees raw tickers, dates, or prices.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portfolio_agent.evaluator import WalkForwardEvaluator


def build_agent(agent_name: str, config: dict) -> object:
    agent_cfg = config.get("agents", {}).get(agent_name, {})

    if agent_name == "hybrid_rule":
        from portfolio_agent.agents.hybrid_rule import HybridRuleAgent
        return HybridRuleAgent(
            rebalance_frequency=agent_cfg.get("rebalance_frequency", 5),
            max_positions=agent_cfg.get("max_positions", 8),
            technical_weight=agent_cfg.get("technical_weight", 0.70),
            fundamental_weight=agent_cfg.get("fundamental_weight", 0.30),
            momentum_short_weight=agent_cfg.get("momentum_short_weight", 0.60),
            momentum_long_weight=agent_cfg.get("momentum_long_weight", 0.40),
            max_asset_weight=config.get("constraints", {}).get("max_asset_weight", 0.30),
        )

    if agent_name == "ppo":
        from portfolio_agent.agents.ppo_portfolio import PPOPortfolioAgent
        return PPOPortfolioAgent(
            checkpoint_path=agent_cfg.get("checkpoint"),
            max_asset_weight=config.get("constraints", {}).get("max_asset_weight", 0.30),
        )

    if agent_name == "llm":
        from portfolio_agent.agents.llm_allocation import LLMAllocationAgent
        return LLMAllocationAgent(
            model_name=agent_cfg.get("model_name", "google/gemma-4-31B-it"),
            base_url=agent_cfg.get("base_url", "http://10.86.229.182:8000/v1"),
            api_key=agent_cfg.get("api_key", "unused"),
            temperature=agent_cfg.get("temperature", 0.0),
            rebalance_frequency=agent_cfg.get("rebalance_frequency", 5),
        )

    raise ValueError(f"Unknown agent: {agent_name}")


def print_metrics(name: str, metrics: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Agent: {name}")
    print(f"{'=' * 60}")
    order = [
        "sharpe", "total_return", "annualized_return",
        "annualized_volatility", "sortino", "max_drawdown",
        "calmar", "cost_rate", "turnover", "violation_rate",
    ]
    for key in order:
        val = metrics.get(key)
        if val is None:
            print(f"  {key:25s}: N/A")
        elif key in ("total_return", "annualized_return", "max_drawdown", "cost_rate", "violation_rate"):
            print(f"  {key:25s}: {val:+.4%}")
        else:
            print(f"  {key:25s}: {val:+.4f}")
    print(f"{'=' * 60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run portfolio evaluation")
    parser.add_argument("--data_root", type=str, default="stock_data_1y")
    parser.add_argument("--config", type=str, default="configs/evaluation.yaml")
    parser.add_argument(
        "--agents",
        nargs="+",
        default=["hybrid_rule"],
        choices=["hybrid_rule", "ppo", "llm"],
    )
    parser.add_argument("--output_dir", type=str, default="results")
    parser.add_argument("--audit_dir", type=str, default="private_audit")
    parser.add_argument("--secret", type=str, default=None)
    args = parser.parse_args()

    secret = args.secret.encode() if args.secret else os.urandom(32)

    evaluator = WalkForwardEvaluator(
        data_root=args.data_root,
        config_path=args.config,
        secret=secret,
    )

    all_metrics: dict[str, dict] = {}

    for agent_name in args.agents:
        print(f"\n>>> Running evaluation for agent: {agent_name}")

        agent = build_agent(agent_name, evaluator.cfg)
        if hasattr(agent, "reset"):
            agent.reset()

        agent_output_dir = Path(args.output_dir) / agent_name
        agent_audit_dir = Path(args.audit_dir) / agent_name
        result = evaluator.run(
            agent,
            output_dir=agent_output_dir,
            audit_dir=agent_audit_dir,
        )

        print_metrics(agent_name, result["metrics"])
        all_metrics[agent_name] = result["metrics"]

    summary_path = Path(args.output_dir) / "comparison.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2, default=str)

    print(f"Comparison saved to {summary_path}")


if __name__ == "__main__":
    main()
