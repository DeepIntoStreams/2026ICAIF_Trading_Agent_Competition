"""Walk-forward portfolio evaluator with strict security boundary."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
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
from .config import CompetitionConfig, config_to_dict, hash_config
from .events import MarketOpenEvent, NewsEvent
from .execution import ExecutionEngine, PortfolioState, Trade
from .market_calendar import select_evaluation_sessions, session_clock
from .metrics import compute_metrics
from .news.models import NewsRecord
from .news.store import NewsStore
from .observation import (
    build_decision_observation,
    build_observation,
    compute_fundamental_features,
    compute_market_features,
)
from .point_in_time import compute_yoy_growth, latest_available_fundamentals
from .risk import sanitize_target_weights
from .security import make_asset_id

logger = logging.getLogger(__name__)


def _current_code_state() -> dict[str, object]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return {"commit": commit, "dirty": bool(status.strip())}
    except Exception:
        return {"commit": None, "dirty": None}


def build_run_manifest(
    config: CompetitionConfig,
    agent_name: str,
    market_hashes: dict[str, str],
    news_hashes: dict[str, str],
    code_state: dict[str, object],
) -> dict[str, object]:
    resolved_config = config_to_dict(config)
    return {
        "agent_name": agent_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_hash": hash_config(config),
        "resolved_config": resolved_config,
        "llm": {
            "base_url": config.agents.llm.base_url,
            "model": config.agents.llm.model,
        },
        "market_hashes": dict(market_hashes),
        "news_hashes": dict(news_hashes),
        "code_state": dict(code_state),
        "historical_backfill_mode": config.news.historical_backfill_mode,
    }


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

    def __enter__(self) -> "_AuditLog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, default=str, ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
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
                    "max_asset_weight": 0.10,
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

        try:
            return self._run_inner(agent, audit, total_steps, output_dir)
        finally:
            if audit:
                audit.close()
                logger.info("Audit log saved to %s", audit.path)

    def _run_inner(
        self,
        agent: Agent,
        audit: _AuditLog | None,
        total_steps: int,
        output_dir: str | Path | None,
    ) -> dict[str, Any]:
        if audit:
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
        eval_cost = 0.0
        eval_trade_value = 0.0
        eval_violation_step_count = 0
        prev_fundamentals: dict[str, Any] = {}

        for step in range(total_steps):
            step_date = str(self._get_date(step).date())
            is_pre_roll = step < self.pre_roll_days
            eval_phase = "pre_roll" if is_pre_roll else "evaluation"

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

                if cash < 0:
                    cash = 0.0

                if not is_pre_roll:
                    eval_cost += step_fees
                    eval_trade_value += step_trade_val

                if audit and step_trades:
                    audit.write({
                        "event": "step",
                        "step": step,
                        "phase": "execution",
                        "eval_phase": eval_phase,
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
                    "eval_phase": eval_phase,
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
                    "eval_phase": eval_phase,
                    "num_assets_with_market": mkt_count,
                    "num_assets_with_fundamentals": fund_count,
                })

            # --- 4. Call agent ---
            allowed_ids = set(self.asset_ids.values())
            try:
                raw_action = agent.decide(obs)
            except Exception as e:
                logger.warning("Agent error at step %d: %s", step, e)
                raw_action = pending_weights if pending_weights is not None else {}
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
                if not is_pre_roll:
                    eval_violation_step_count += 1
                for v in violations:
                    all_violations.append({"step": step, "type": v})

            if audit:
                audit.write({
                    "event": "step",
                    "step": step,
                    "phase": "agent_action",
                    "eval_phase": eval_phase,
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
            initial_capital=self.initial_cash,
            total_transaction_cost=eval_cost,
            total_trade_value=eval_trade_value,
            violation_steps=eval_violation_step_count,
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
                "eval_transaction_cost": eval_cost,
                "total_trade_value": total_trade_value,
                "eval_trade_value": eval_trade_value,
                "violation_steps": violation_step_count,
                "eval_violation_steps": eval_violation_step_count,
                "metrics": metrics,
            })

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


class DailyTradingEvaluator:
    """Event-driven daily evaluator with point-in-time news observations."""

    def __init__(
        self,
        data_root: str | Path | None,
        config: CompetitionConfig,
        news_store: NewsStore | None = None,
        secret: bytes | None = None,
    ):
        if data_root is None:
            raise ValueError("data_root is required unless using from_frames")

        self.config = config
        self.data_root = Path(data_root)
        self.secret = secret or os.urandom(32)
        self.news_store = news_store

        sectors = load_evaluation_universe(self.data_root)
        tickers = flatten_universe(sectors)
        raw_prices = load_price_data(self.data_root, tickers)
        calendar, prices = align_trading_dates(raw_prices)
        fundamentals = load_fundamentals(self.data_root, tickers)

        self._set_frames(
            prices=prices,
            fundamentals=fundamentals,
            universe=sectors,
            calendar=calendar,
            news_records=None,
        )

    @classmethod
    def from_frames(
        cls,
        prices: dict[str, pd.DataFrame],
        fundamentals: pd.DataFrame,
        universe: dict[str, list[str]],
        config: CompetitionConfig,
        news_records: list[NewsRecord],
    ) -> "DailyTradingEvaluator":
        evaluator = cls.__new__(cls)
        evaluator.config = config
        evaluator.data_root = None
        evaluator.secret = b"unit-test-secret"
        evaluator.news_store = None
        all_dates: set[pd.Timestamp] = set()
        for frame in prices.values():
            all_dates.update(pd.to_datetime(frame["date"]).dt.normalize().tolist())
        evaluator._set_frames(
            prices=prices,
            fundamentals=fundamentals,
            universe=universe,
            calendar=sorted(all_dates),
            news_records=news_records,
        )
        return evaluator

    def _set_frames(
        self,
        prices: dict[str, pd.DataFrame],
        fundamentals: pd.DataFrame,
        universe: dict[str, list[str]],
        calendar: list[pd.Timestamp],
        news_records: list[NewsRecord] | None,
    ) -> None:
        self.prices = {
            ticker: frame.sort_values("date").reset_index(drop=True)
            for ticker, frame in prices.items()
        }
        self.fundamentals = fundamentals
        self.universe = universe
        self.tickers = flatten_universe(universe)
        self.sector_map = {ticker: sector for sector, group in universe.items() for ticker in group}
        self.calendar = sorted(pd.Timestamp(value).normalize() for value in calendar)
        self._news_records = list(news_records or [])

    def _all_news(self) -> list[NewsRecord]:
        if self.news_store is not None:
            return self.news_store.load_all()
        return list(self._news_records)

    def _market_hashes(self) -> dict[str, str]:
        if self.data_root is None:
            return {}

        root = Path(self.data_root)
        paths: list[Path] = [root / "universe.json"]
        for ticker in self.tickers:
            paths.append(root / "prices_daily" / f"{ticker}.csv")
            fundamentals_dir = root / "fundamentals_quarterly" / ticker
            paths.extend(
                [
                    fundamentals_dir / "income_statement.csv",
                    fundamentals_dir / "balance_sheet.csv",
                    fundamentals_dir / "cash_flow.csv",
                ]
            )

        hashes: dict[str, str] = {}
        for path in paths:
            if path.exists() and path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                hashes[path.relative_to(root).as_posix()] = digest
        return hashes

    def _row_for(self, ticker: str, session: pd.Timestamp) -> pd.Series | None:
        frame = self.prices.get(ticker)
        if frame is None or frame.empty:
            return None
        dates = pd.to_datetime(frame["date"]).dt.normalize()
        matches = frame[dates == pd.Timestamp(session).normalize()]
        if matches.empty:
            return None
        return matches.iloc[0]

    def _position_for(self, ticker: str, session: pd.Timestamp) -> int:
        frame = self.prices[ticker]
        dates = pd.to_datetime(frame["date"]).dt.normalize()
        matches = np.flatnonzero(dates == pd.Timestamp(session).normalize())
        if len(matches) == 0:
            raise ValueError(f"No price row for {ticker} on {session.date()}")
        return int(matches[0])

    def _open_prices(self, session: pd.Timestamp) -> dict[str, float]:
        prices: dict[str, float] = {}
        for ticker in self.tickers:
            row = self._row_for(ticker, session)
            if row is not None and pd.notna(row.get("adj_open")):
                prices[ticker] = float(row["adj_open"])
        return prices

    def _close_prices(self, session: pd.Timestamp) -> dict[str, float]:
        prices: dict[str, float] = {}
        for ticker in self.tickers:
            row = self._row_for(ticker, session)
            if row is not None and pd.notna(row.get("adj_close")):
                prices[ticker] = float(row["adj_close"])
        return prices

    def _market_features(self, session: pd.Timestamp) -> dict[str, dict[str, float | None]]:
        features: dict[str, dict[str, float | None]] = {}
        for ticker in self.tickers:
            frame = self.prices.get(ticker)
            if frame is None or frame.empty:
                features[ticker] = {}
                continue
            pos = self._position_for(ticker, session)
            history = frame.iloc[:pos]
            if history.empty:
                features[ticker] = {}
                continue
            closes = history["adj_close"].to_numpy()
            volumes = history["volume"].to_numpy()
            features[ticker] = compute_market_features(closes, volumes)
        return features

    def _fundamental_features(
        self,
        session: pd.Timestamp,
        prev_fundamentals: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        current_fundamentals: dict[str, Any] = {}
        features: dict[str, dict[str, Any]] = {}

        if self.fundamentals.empty:
            for ticker in self.tickers:
                features[ticker] = compute_fundamental_features(None, session)
            return features, current_fundamentals

        yoy = compute_yoy_growth(self.fundamentals, session)
        latest = latest_available_fundamentals(self.fundamentals, session)

        for ticker in self.tickers:
            ticker_rows = latest[latest["ticker"] == ticker]
            if ticker_rows.empty:
                features[ticker] = compute_fundamental_features(None, session)
                continue
            row = ticker_rows.iloc[0].to_dict()
            row["revenue_yoy"] = yoy.get(ticker)
            current_fundamentals[ticker] = row
            prev_row = prev_fundamentals.get(ticker)
            previous_available_at = prev_row.get("available_at") if prev_row else None
            features[ticker] = compute_fundamental_features(
                row,
                session,
                previous_available_at,
            )

        return features, current_fundamentals

    def _universe_rows(self) -> list[dict[str, str]]:
        return [
            {
                "ticker": ticker,
                "company_name": ticker,
                "sector": self.sector_map.get(ticker, ""),
            }
            for ticker in self.tickers
        ]

    def _portfolio_snapshot(
        self,
        state: PortfolioState,
        prices: dict[str, float],
    ) -> dict[str, Any]:
        nav = state.nav(prices)
        weights = {}
        for ticker in self.tickers:
            price = prices.get(ticker)
            if price is None or nav <= 0:
                weights[ticker] = 0.0
            else:
                weights[ticker] = state.shares.get(ticker, 0.0) * price / nav
        return {
            "weights": weights,
            "cash_ratio": state.cash / nav if nav > 0 else 1.0,
            "nav": nav,
        }

    def _visible_news_window(
        self,
        cutoff_utc: datetime,
        previous_cutoff_utc: datetime | None,
    ) -> list[NewsRecord]:
        ticker_set = set(self.tickers)
        records = []
        for record in self._all_news():
            if previous_cutoff_utc is not None and record.available_at_utc <= previous_cutoff_utc:
                continue
            if record.available_at_utc > cutoff_utc:
                continue
            if not ticker_set.intersection(record.tickers):
                continue
            records.append(record)
        return sorted(
            records,
            key=lambda record: (
                record.available_at_utc,
                record.provider,
                record.provider_news_id,
            ),
        )

    def _call_reset(self, agent: Any, context: dict[str, Any]) -> None:
        reset = getattr(agent, "reset", None)
        if reset is None:
            return
        try:
            reset(context)
        except TypeError:
            reset()

    def _call_observe(self, agent: Any, event: Any) -> None:
        observe = getattr(agent, "observe", None)
        if observe is not None:
            observe(event)

    def run_agent(
        self,
        agent: Any,
        agent_name: str,
        output_dir: str | Path,
    ) -> dict[str, Any]:
        cfg = self.config
        sessions = select_evaluation_sessions(self.calendar, cfg.evaluation)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        state = PortfolioState(
            cash=float(cfg.evaluation.initial_cash),
            shares={ticker: 0.0 for ticker in self.tickers},
        )
        engine = ExecutionEngine(
            fee_rate=cfg.evaluation.fee_rate,
            slippage_bps=cfg.evaluation.slippage_bps,
        )
        constraints = {
            "long_only": cfg.constraints.long_only,
            "max_asset_weight": cfg.constraints.max_asset_weight,
            "max_gross_exposure": cfg.constraints.max_gross_exposure,
            "fee_rate": cfg.evaluation.fee_rate,
            "slippage_bps": cfg.evaluation.slippage_bps,
        }
        context = {
            "agent_name": agent_name,
            "tickers": list(self.tickers),
            "config": config_to_dict(cfg),
        }
        self._call_reset(agent, context)

        nav_history = [float(cfg.evaluation.initial_cash)]
        daily_records: list[dict[str, Any]] = []
        trades_records: list[dict[str, Any]] = []
        actions_records: list[dict[str, Any]] = []
        event_sessions: list[dict[str, Any]] = []
        news_seen_records: list[dict[str, Any]] = []
        violations_records: list[dict[str, Any]] = []
        prev_fundamentals: dict[str, Any] = {}
        previous_cutoff_utc: datetime | None = None
        total_cost = 0.0
        total_trade_value = 0.0
        violation_days = 0

        for session in sessions:
            clock = session_clock(
                session,
                cfg.evaluation.decision_minutes_before_close,
            )
            open_prices = self._open_prices(session)
            close_prices = self._close_prices(session)
            self._call_observe(
                agent,
                MarketOpenEvent(
                    session_date=clock.session_date,
                    event_time_utc=clock.open_utc,
                    open_prices=open_prices,
                ),
            )

            visible_news = self._visible_news_window(
                clock.decision_cutoff_utc,
                previous_cutoff_utc,
            )
            for record in visible_news:
                self._call_observe(
                    agent,
                    NewsEvent(
                        session_date=clock.session_date,
                        event_time_utc=record.available_at_utc,
                        record=record,
                    ),
                )

            market_features = self._market_features(session)
            fundamental_features, prev_fundamentals = self._fundamental_features(
                session,
                prev_fundamentals,
            )
            portfolio = self._portfolio_snapshot(state, open_prices)
            portfolio_before = dict(portfolio)
            observation = build_decision_observation(
                session_date=clock.session_date,
                event_time_utc=clock.decision_cutoff_utc,
                universe=self._universe_rows(),
                open_prices=open_prices,
                market_features=market_features,
                fundamental_features=fundamental_features,
                portfolio=portfolio,
                constraints=constraints,
                news=visible_news,
                max_news_items=cfg.news.max_items_per_decision,
                include_raw_text=cfg.news.include_raw_text,
            )

            had_violation = False
            try:
                raw_action = agent.decide(observation)
            except Exception as exc:
                raw_action = {}
                had_violation = True
                violations_records.append(
                    {
                        "session_date": clock.session_date,
                        "type": "invalid_action",
                        "detail": str(exc),
                    }
                )

            agent_violation = getattr(agent, "last_decision_violation", None)
            if agent_violation:
                had_violation = True
                violations_records.append(
                    {
                        "session_date": clock.session_date,
                        "type": str(agent_violation),
                    }
                )
                try:
                    setattr(agent, "last_decision_violation", None)
                except Exception:
                    pass

            sanitized, violations = sanitize_target_weights(
                raw_action,
                allowed_assets=set(self.tickers),
                max_asset_weight=cfg.constraints.max_asset_weight,
                max_gross_exposure=cfg.constraints.max_gross_exposure,
            )
            if violations:
                had_violation = True
                for violation in violations:
                    violations_records.append(
                        {
                            "session_date": clock.session_date,
                            "type": violation,
                        }
                    )
            if had_violation:
                violation_days += 1

            state, trades = engine.execute_close(state, sanitized, close_prices)
            session_cost = sum(trade.fee for trade in trades)
            session_trade_value = sum(trade.trade_value for trade in trades)
            total_cost += session_cost
            total_trade_value += session_trade_value
            nav_close = state.nav(close_prices)
            portfolio_after = self._portfolio_snapshot(state, close_prices)
            previous_nav = nav_history[-1]
            nav_history.append(nav_close)
            daily_return = nav_close / previous_nav - 1.0 if previous_nav > 0 else 0.0

            daily_record = {
                "session_date": clock.session_date,
                "nav": nav_close,
                "daily_return": daily_return,
                "cash": state.cash,
                "cash_ratio": state.cash / nav_close if nav_close > 0 else 1.0,
                "transaction_cost": session_cost,
                "traded_notional": session_trade_value,
            }
            daily_records.append(daily_record)
            agent_diagnostics = {
                "last_pre_tilt_weights": getattr(
                    agent,
                    "last_pre_tilt_weights",
                    None,
                ),
                "last_post_tilt_weights": getattr(
                    agent,
                    "last_post_tilt_weights",
                    None,
                ),
            }
            action_record = {
                "session_date": clock.session_date,
                "raw_action": raw_action,
                "sanitized_action": sanitized,
                "violations": violations,
                "agent_diagnostics": agent_diagnostics,
            }
            actions_records.append(action_record)
            session_trades: list[dict[str, Any]] = []
            for trade in trades:
                trade_data = asdict(trade)
                trade_data["session_date"] = clock.session_date
                session_trades.append(trade_data)
                trades_records.append(trade_data)
            for item in observation["news"]:
                news_item = dict(item)
                news_item["session_date"] = clock.session_date
                news_seen_records.append(news_item)

            event_sessions.append(
                {
                    "event": "daily_session",
                    "session_index": len(event_sessions),
                    "session_date": clock.session_date,
                    "clock": {
                        "open_et": clock.open_et.isoformat(),
                        "open_utc": clock.open_utc.isoformat(),
                        "decision_cutoff_et": clock.decision_cutoff_et.isoformat(),
                        "decision_cutoff_utc": clock.decision_cutoff_utc.isoformat(),
                        "close_et": clock.close_et.isoformat(),
                        "close_utc": clock.close_utc.isoformat(),
                    },
                    "market_open": {
                        "open_prices": open_prices,
                        "portfolio_before": portfolio_before,
                    },
                    "observation": observation,
                    "news": observation["news"],
                    "decision": action_record,
                    "raw_action": raw_action,
                    "sanitized_action": sanitized,
                    "agent_diagnostics": agent_diagnostics,
                    "execution": {
                        "close_prices": close_prices,
                        "trades": session_trades,
                        "transaction_cost": session_cost,
                        "traded_notional": session_trade_value,
                    },
                    "trades": session_trades,
                    "portfolio_before": portfolio_before,
                    "portfolio_after": portfolio_after,
                    "daily_record": daily_record,
                    "running_metrics": {
                        "sessions_completed": len(daily_records),
                        "nav": nav_close,
                        "cumulative_return": (
                            nav_close / cfg.evaluation.initial_cash - 1.0
                            if cfg.evaluation.initial_cash > 0
                            else 0.0
                        ),
                        "daily_return": daily_return,
                        "total_transaction_cost": total_cost,
                        "total_traded_notional": total_trade_value,
                        "violation_days": violation_days,
                    },
                }
            )
            previous_cutoff_utc = clock.decision_cutoff_utc

        metrics = compute_metrics(
            nav=nav_history,
            initial_capital=cfg.evaluation.initial_cash,
            total_transaction_cost=total_cost,
            total_trade_value=total_trade_value,
            violation_steps=violation_days,
            decision_steps=len(sessions),
            annualization=cfg.evaluation.annualization,
            risk_free_rate=cfg.evaluation.risk_free_rate,
        )
        news_hashes = self.news_store.file_hashes() if self.news_store is not None else {}
        manifest = build_run_manifest(
            config=cfg,
            agent_name=agent_name,
            market_hashes=self._market_hashes(),
            news_hashes=news_hashes,
            code_state=_current_code_state(),
        )
        event_log = {
            "event": "evaluation_run",
            "agent_name": agent_name,
            "metrics": metrics,
            "run_manifest": manifest,
            "sessions": event_sessions,
        }
        result = {
            "metrics": metrics,
            "daily_records": daily_records,
            "trades": trades_records,
            "actions": actions_records,
            "news_seen": news_seen_records,
            "violations": violations_records,
            "run_manifest": manifest,
            "event_log": event_log,
        }
        self._save_daily_outputs(result, output_path)
        return result

    def _write_jsonl(self, path: Path, records: list[dict[str, Any]]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _save_daily_outputs(self, result: dict[str, Any], output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / "metrics.json", "w", encoding="utf-8") as f:
            json.dump(result["metrics"], f, indent=2, default=str)
        pd.DataFrame(result["daily_records"]).to_csv(
            output_dir / "daily_nav.csv",
            index=False,
        )
        self._write_jsonl(output_dir / "trades.jsonl", result["trades"])
        self._write_jsonl(output_dir / "actions.jsonl", result["actions"])
        self._write_jsonl(output_dir / "news_seen.jsonl", result["news_seen"])
        self._write_jsonl(output_dir / "violations.jsonl", result["violations"])
        with open(output_dir / "run_manifest.json", "w", encoding="utf-8") as f:
            json.dump(result["run_manifest"], f, indent=2, default=str)
        with open(output_dir / "event_log.json", "w", encoding="utf-8") as f:
            json.dump(result["event_log"], f, indent=2, ensure_ascii=False, default=str)
