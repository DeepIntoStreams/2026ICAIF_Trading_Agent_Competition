"""Tests for the competition platform (engine, runner, panel builder)."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT.parent / "src"))

from runner import run_agent, code_hash
import engine

DATA_ROOT = ROOT.parent / "data" / "stock_data_1y"


def _write_agent(tmp: Path, body: str) -> Path:
    (tmp / "agent.py").write_text(
        "from agent_base import BaseAgent\n"
        "class Agent(BaseAgent):\n" + body)
    return tmp


OBS = {"session_date": "2026-06-05", "assets": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
       "market_features": {"AAPL": {"momentum_60d": 0.2, "volatility_20d": 0.02},
                           "MSFT": {"momentum_60d": 0.1, "volatility_20d": 0.03}},
       "constraints": {"max_asset_weight": 0.30, "max_gross_exposure": 1.0}}


def test_runner_reproducibility(tmp_path):
    _write_agent(tmp_path, "    def decide(self, o):\n        return {'AAPL': 0.2, 'MSFT': 0.1}\n")
    r1 = run_agent(tmp_path, OBS)
    r2 = run_agent(tmp_path, OBS)
    assert r1["ok"] and r2["ok"]
    assert r1["target_weights"] == r2["target_weights"]
    assert r1["audit"]["code_hash"] == r2["audit"]["code_hash"]


def test_runner_reports_crash_not_raises(tmp_path):
    _write_agent(tmp_path, "    def decide(self, o):\n        raise ValueError('boom')\n")
    r = run_agent(tmp_path, OBS)
    assert r["ok"] is False
    assert "boom" in r["reason"]


def test_runner_timeout(tmp_path):
    _write_agent(tmp_path, "    def decide(self, o):\n        import time; time.sleep(5)\n        return {}\n")
    r = run_agent(tmp_path, OBS, timeout_seconds=1.0)
    assert r["ok"] is False and r["reason"] == "timeout"


def test_leaderboard_rank_direction(tmp_path):
    """Lower drawdown / higher return -> better average rank."""
    results = tmp_path / "results"
    results.mkdir()
    good = {"m1_cumulative_return": 0.10, "m2_daily_win_rate": 0.6, "m3_sharpe_ratio": 2.0,
            "m4_sortino_ratio": 2.0, "m5_maximum_drawdown": 0.03, "m6_value_at_risk_95": 0.01,
            "m7_expected_shortfall_95": 0.02, "m8_turnover": 2.0, "m9_violation_rate": 0.0}
    bad = {k: (v * 0.5 if k in ("m1_cumulative_return", "m2_daily_win_rate", "m3_sharpe_ratio",
               "m4_sortino_ratio") else v * 2) for k, v in good.items()}
    (results / "good.json").write_text(json.dumps({"team_id": "good", "metrics": good}))
    (results / "bad.json").write_text(json.dumps({"team_id": "bad", "metrics": bad}))
    board = engine.leaderboard(str(tmp_path))
    assert board[0]["team_id"] == "good"
    assert board[0]["avg_rank"] < board[1]["avg_rank"]


@pytest.mark.skipif(not DATA_ROOT.exists(), reason="market data not present")
def test_panel_has_no_lookahead():
    """The observation exposes only raw completed daily bars STRICTLY BEFORE the session date;
    the decision is made at the 9:00 AM ET cutoff (before the open), so no day-t price leaks."""
    from panel import PanelBuilder
    pb = PanelBuilder(str(DATA_ROOT))
    panel = pb.build("2026-06-05")
    bars = panel["market_history"]["AAPL"]
    assert bars, "expected raw OHLCV history"
    assert all(b["date"] < "2026-06-05" for b in bars)          # every bar strictly before t
    assert set(bars[-1]) == {"date", "open", "high", "low", "close", "volume"}
    assert "market_features" not in panel                       # raw-only: no engineered features
    assert "open_price" not in panel["assets"][0]               # no day-t price at all
