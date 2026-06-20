"""Walk-forward portfolio evaluator with strict security boundary."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from .data_loader import (
    align_trading_dates,
    flatten_universe,
    load_evaluation_universe,
    load_fundamentals,
    load_price_data,
)
from .metrics import compute_metrics
from .observation import (
    build_observation,
    compute_fundamental_features,
    compute_market_features,
)
from .point_in_time import compute_yoy_growth, latest_available_fundamentals
from .risk import sanitize_target_weights
from .security import make_asset_id

logger = logging.getLogger(__name__)


class Agent(Protocol):
    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        ...


class _AuditLog:
    """Private audit trail stored outside the agent-visible output directory.

    This log contains raw tickers, dates, prices, and execution details
    that must NEVER be exposed to agents. It is written to a separate
    directory controlled exclusively by the evaluator.
    """

    def __init__(self, log_dir: Path):
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._path = log_dir / f"audit_{ts}.jsonl"
        self._file = open(self._path, "w", encoding="utf-8")

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, default=str, ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    @property
    def path(self) -> Path:
        return self._path


def _load_config(config_path: str | Path) -> dict:
    import yaml
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class WalkForwardEvaluator:
    """Runs a walk-forward evaluation over private data.

    The evaluator owns all private state (tickers, dates, prices, filings).
    Agents receive only anonymized, derived observations.
    """

    def __init__(
        self,
        data_root: str | Path,
        config_path: str | Path | None = None,
        secret: bytes | None = None,
    ):
        if config_path is not None:
            self.cfg = _load_config(config_path)
        else:
            self.cfg = {
                "evaluation": {
                    "initial_cash": 1_000_000,
                    "pre_roll_days": 60,
                    "fee_rate": 0.001,
                },
                "constraints": {
                    "long_only": True,
                    "max_asset_weight": 0.30,
                    "max_gross_exposure": 1.00,
                },
            }

        eval_cfg = self.cfg["evaluation"]
        self.initial_cash = float(eval_cfg["initial_cash"])
        self.pre_roll_days = int(eval_cfg["pre_roll_days"])
        self.fee_rate = float(eval_cfg["fee_rate"])

        constraint_cfg = self.cfg["constraints"]
        self.max_asset_weight = float(constraint_cfg["max_asset_weight"])
        self.max_gross_exposure = float(constraint_cfg["max_gross_exposure"])

        self.secret = secret or os.urandom(32)

        sectors = load_evaluation_universe(data_root)
        self.tickers = flatten_universe(sectors)
        self.sector_map = {t: s for s, ts in sectors.items() for t in ts}

        raw_prices = load_price_data(data_root, self.tickers)
        self.calendar, self.prices = align_trading_dates(raw_prices)

        self.fundamentals = load_fundamentals(data_root, self.tickers)

        self.asset_ids = {t: make_asset_id(t, self.secret) for t in self.tickers}
        self.id_to_ticker = {v: k for k, v in self.asset_ids.items()}

        self.constraints_obs = {
            "long_only": True,
            "max_asset_weight": self.max_asset_weight,
            "max_gross_exposure": self.max_gross_exposure,
            "fee_rate": self.fee_rate,
        }

    def _get_close(self, ticker: str, step: int) -> float | None:
        df = self.prices.get(ticker)
        if df is None or step >= len(df):
            return None
        val = df.iloc[step]["adj_close"]
        return float(val) if pd.notna(val) else None

    def _get_open(self, ticker: str, step: int) -> float | None:
        df = self.prices.get(ticker)
        if df is None or step >= len(df):
            return None
        val = df.iloc[step]["adj_open"]
        return float(val) if pd.notna(val) else None

    def _get_date(self, step: int) -> pd.Timestamp:
        first_ticker = next(iter(self.prices))
        return self.prices[first_ticker].iloc[step]["date"]

    def _build_market_features(
        self, step: int,
    ) -> dict[str, dict[str, float | None]]:
        features: dict[str, dict[str, float | None]] = {}
        for ticker in self.tickers:
            aid = self.asset_ids[ticker]
            df = self.prices.get(ticker)
            if df is None or step >= len(df):
                features[aid] = {}
                continue
            closes = df["adj_close"].values[: step + 1]
            volumes = df["volume"].values[: step + 1]
            features[aid] = compute_market_features(closes, volumes)
        return features

    def _build_fundamental_features(
        self,
        step: int,
        prev_fundamentals: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        cutoff_date = self._get_date(step)
        current_fundamentals: dict[str, Any] = {}
        feat: dict[str, dict[str, Any]] = {}

        if self.fundamentals.empty:
            for ticker in self.tickers:
                aid = self.asset_ids[ticker]
                feat[aid] = compute_fundamental_features(None, cutoff_date)
            return feat, current_fundamentals

        yoy = compute_yoy_growth(self.fundamentals, cutoff_date)
        latest = latest_available_fundamentals(self.fundamentals, cutoff_date)

        for ticker in self.tickers:
            aid = self.asset_ids[ticker]
            ticker_rows = latest[latest["ticker"] == ticker]

            if ticker_rows.empty:
                feat[aid] = compute_fundamental_features(None, cutoff_date)
                continue

            row = ticker_rows.iloc[0].to_dict()
            row["revenue_yoy"] = yoy.get(ticker)
            current_fundamentals[ticker] = row

            prev_at = None
            prev_row = prev_fundamentals.get(ticker)
            if prev_row is not None:
                prev_at = prev_row.get("available_at")

            feat[aid] = compute_fundamental_features(row, cutoff_date, prev_at)

        return feat, current_fundamentals

    def run(
        self,
        agent: Agent,
        output_dir: str | Path | None = None,
        audit_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Execute the full walk-forward evaluation.

        Args:
            agent: the portfolio agent to evaluate.
            output_dir: sanitized results (safe to share).
            audit_dir: private audit log with raw tickers, prices, trades.
                       This directory must NEVER be shared with agents.

        Returns a result dict with metrics and audit data.
        """
        total_steps = min(len(df) for df in self.prices.values())
        if total_steps < self.pre_roll_days + 10:
            raise ValueError(
                f"Insufficient data: {total_steps} steps, need at least "
                f"{self.pre_roll_days + 10}"
            )

        audit: _AuditLog | None = None
        if audit_dir is not None:
            audit = _AuditLog(Path(audit_dir))
            audit.write({
                "event": "evaluation_start",
                "timestamp": datetime.now().isoformat(),
                "initial_cash": self.initial_cash,
                "fee_rate": self.fee_rate,
                "pre_roll_days": self.pre_roll_days,
                "total_steps": total_steps,
                "num_assets": len(self.tickers),
                "tickers": self.tickers,
                "asset_id_mapping": self.asset_ids,
                "sector_mapping": self.sector_map,
            })

        cash = self.initial_cash
        shares: dict[str, float] = {t: 0.0 for t in self.tickers}
        pending_weights: dict[str, float] | None = None

        nav_history: list[float] = []
        daily_records: list[dict[str, Any]] = []
        all_violations: list[dict[str, Any]] = []
        total_cost = 0.0
        total_trade_value = 0.0
        violation_step_count = 0
        prev_fundamentals: dict[str, Any] = {}

        for step in range(total_steps):
            step_date = str(self._get_date(step).date())

            # --- 1. Execute pending weights at open ---
            step_trades: list[dict[str, Any]] = []
            step_fees = 0.0
            step_trade_val = 0.0

            if pending_weights is not None and step > 0:
                nav_open = cash
                opens: dict[str, float] = {}
                for ticker in self.tickers:
                    op = self._get_open(ticker, step)
                    if op is not None and op > 0:
                        nav_open += shares[ticker] * op
                        opens[ticker] = op
                    else:
                        cl = self._get_close(ticker, step - 1)
                        if cl is not None:
                            nav_open += shares[ticker] * cl
                            opens[ticker] = cl

                sell_value = 0.0
                buy_orders: list[tuple[str, float]] = []

                for ticker in self.tickers:
                    if ticker not in opens:
                        continue
                    aid = self.asset_ids[ticker]
                    target_w = pending_weights.get(aid, 0.0)
                    target_value = target_w * nav_open
                    target_shares = target_value / opens[ticker]
                    delta = target_shares - shares[ticker]

                    if delta < 0:
                        trade_val = abs(delta) * opens[ticker]
                        fee = trade_val * self.fee_rate
                        cash += trade_val - fee
                        total_cost += fee
                        total_trade_value += trade_val
                        sell_value += trade_val
                        step_fees += fee
                        step_trade_val += trade_val
                        old_shares = shares[ticker]
                        shares[ticker] = target_shares
                        step_trades.append({
                            "ticker": ticker,
                            "asset_id": aid,
                            "direction": "sell",
                            "shares_before": old_shares,
                            "shares_after": target_shares,
                            "shares_delta": delta,
                            "open_price": opens[ticker],
                            "trade_value": trade_val,
                            "fee": fee,
                        })

                for ticker in self.tickers:
                    if ticker not in opens:
                        continue
                    aid = self.asset_ids[ticker]
                    target_w = pending_weights.get(aid, 0.0)
                    target_value = target_w * nav_open
                    target_shares = target_value / opens[ticker]
                    delta = target_shares - shares[ticker]

                    if delta > 0:
                        buy_orders.append((ticker, delta))

                if buy_orders:
                    total_buy_cost = sum(
                        d * opens[t] * (1 + self.fee_rate) for t, d in buy_orders
                    )
                    scale = 1.0
                    if total_buy_cost > cash:
                        scale = cash / total_buy_cost if total_buy_cost > 0 else 0.0

                    for ticker, delta in buy_orders:
                        aid = self.asset_ids[ticker]
                        actual_delta = delta * scale
                        trade_val = actual_delta * opens[ticker]
                        fee = trade_val * self.fee_rate
                        cash -= trade_val + fee
                        total_cost += fee
                        total_trade_value += trade_val
                        step_fees += fee
                        step_trade_val += trade_val
                        old_shares = shares[ticker]
                        shares[ticker] += actual_delta
                        step_trades.append({
                            "ticker": ticker,
                            "asset_id": aid,
                            "direction": "buy",
                            "shares_before": old_shares,
                            "shares_after": shares[ticker],
                            "shares_delta": actual_delta,
                            "open_price": opens[ticker],
                            "trade_value": trade_val,
                            "fee": fee,
                            "buy_scale": scale,
                        })

                if cash < -1e-6:
                    cash = 0.0

                if audit and step_trades:
                    audit.write({
                        "event": "step",
                        "step": step,
                        "phase": "execution",
                        "date": step_date,
                        "nav_open": nav_open,
                        "num_trades": len(step_trades),
                        "trades": step_trades,
                        "total_trade_value": step_trade_val,
                        "total_fees": step_fees,
                        "cash_after": cash,
                    })

            # --- 2. Mark portfolio at close ---
            nav_close = cash
            weights: dict[str, float] = {}
            holdings_detail: dict[str, dict[str, Any]] = {}

            for ticker in self.tickers:
                cl = self._get_close(ticker, step)
                if cl is not None:
                    nav_close += shares[ticker] * cl

            for ticker in self.tickers:
                cl = self._get_close(ticker, step)
                if cl is not None and nav_close > 0:
                    weights[ticker] = shares[ticker] * cl / nav_close
                else:
                    weights[ticker] = 0.0

                if shares[ticker] > 1e-9:
                    holdings_detail[ticker] = {
                        "shares": shares[ticker],
                        "close_price": cl,
                        "value": shares[ticker] * cl if cl else 0,
                        "weight": weights[ticker],
                    }

            nav_history.append(nav_close)
            cash_ratio = cash / nav_close if nav_close > 0 else 1.0
            nav_ratio = nav_close / self.initial_cash

            running_max = max(nav_history)
            drawdown = nav_close / running_max - 1.0 if running_max > 0 else 0.0

            daily_return = (
                nav_close / nav_history[-2] - 1.0 if len(nav_history) >= 2 else 0.0
            )

            record = {
                "step": step,
                "nav": nav_close,
                "nav_ratio": nav_ratio,
                "daily_return": daily_return,
                "drawdown": drawdown,
                "cash_ratio": cash_ratio,
            }
            daily_records.append(record)

            if audit:
                audit.write({
                    "event": "step",
                    "step": step,
                    "phase": "mark_close",
                    "date": step_date,
                    "nav": nav_close,
                    "cash": cash,
                    "cash_ratio": cash_ratio,
                    "nav_ratio": nav_ratio,
                    "drawdown": drawdown,
                    "daily_return": daily_return,
                    "num_holdings": len(holdings_detail),
                    "holdings": holdings_detail,
                })

            if step >= total_steps - 1:
                break

            # --- 3. Build observation ---
            market_feat = self._build_market_features(step)
            fund_feat, prev_fundamentals = self._build_fundamental_features(
                step, prev_fundamentals
            )

            weight_obs = {self.asset_ids[t]: weights[t] for t in self.tickers}
            obs = build_observation(
                step_id=step,
                market_features=market_feat,
                fundamental_features=fund_feat,
                portfolio_weights=weight_obs,
                cash_ratio=cash_ratio,
                nav_ratio=nav_ratio,
                drawdown=drawdown,
                constraints=self.constraints_obs,
            )

            if audit:
                mkt_count = sum(
                    1 for v in market_feat.values() if v
                )
                fund_count = sum(
                    1 for v in fund_feat.values()
                    if v.get("net_margin") is not None
                )
                audit.write({
                    "event": "step",
                    "step": step,
                    "phase": "observation_sent",
                    "num_assets_with_market": mkt_count,
                    "num_assets_with_fundamentals": fund_count,
                })

            # --- 4. Call agent ---
            allowed_ids = set(self.asset_ids.values())
            try:
                raw_action = agent.decide(obs)
            except Exception as e:
                logger.warning("Agent error at step %d: %s", step, e)
                raw_action = {}
                all_violations.append({
                    "step": step,
                    "type": "invalid_action",
                    "detail": str(e),
                })

            sanitized, violations = sanitize_target_weights(
                raw_action,
                allowed_assets=allowed_ids,
                max_asset_weight=self.max_asset_weight,
                max_gross_exposure=self.max_gross_exposure,
            )

            if violations:
                violation_step_count += 1
                for v in violations:
                    all_violations.append({"step": step, "type": v})

            if audit:
                audit.write({
                    "event": "step",
                    "step": step,
                    "phase": "agent_action",
                    "raw_action": raw_action,
                    "sanitized_action": sanitized,
                    "violations": violations,
                    "raw_action_sum": sum(
                        float(v) for v in raw_action.values()
                        if isinstance(v, (int, float))
                    ),
                    "sanitized_action_sum": sum(sanitized.values()),
                    "num_positions": sum(1 for v in sanitized.values() if v > 1e-6),
                })

            pending_weights = sanitized

        # --- Compute metrics (exclude pre-roll) ---
        eval_nav = nav_history[self.pre_roll_days:]
        if len(eval_nav) < 2:
            raise ValueError("Not enough evaluation steps after pre-roll")

        eval_steps = len(eval_nav) - 1
        metrics = compute_metrics(
            nav=eval_nav,
            total_transaction_cost=total_cost,
            total_trade_value=total_trade_value,
            violation_steps=violation_step_count,
            decision_steps=eval_steps,
        )

        result = {
            "metrics": metrics,
            "nav_history": nav_history,
            "daily_records": daily_records,
            "violations": all_violations,
            "total_steps": total_steps,
            "pre_roll_days": self.pre_roll_days,
            "eval_steps": eval_steps,
        }

        if audit:
            audit.write({
                "event": "evaluation_end",
                "timestamp": datetime.now().isoformat(),
                "total_steps": total_steps,
                "eval_steps": eval_steps,
                "pre_roll_days": self.pre_roll_days,
                "total_transaction_cost": total_cost,
                "total_trade_value": total_trade_value,
                "violation_steps": violation_step_count,
                "metrics": metrics,
            })
            audit.close()
            logger.info("Audit log saved to %s", audit.path)

        if output_dir is not None:
            self._save_outputs(result, Path(output_dir))

        return result

    def _save_outputs(self, result: dict, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_dir / "metrics.json", "w", encoding="utf-8") as f:
            json.dump(result["metrics"], f, indent=2, default=str)

        nav_df = pd.DataFrame(result["daily_records"])
        nav_df.to_csv(output_dir / "daily_nav.csv", index=False)

        if result["violations"]:
            with open(output_dir / "violations.jsonl", "w", encoding="utf-8") as f:
                for v in result["violations"]:
                    f.write(json.dumps(v) + "\n")

        logger.info("Evaluation outputs saved to %s", output_dir)
