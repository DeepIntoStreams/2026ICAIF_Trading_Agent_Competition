import json
from datetime import datetime, timezone

import pandas as pd

from portfolio_agent.config import load_config
from portfolio_agent.evaluator import DailyTradingEvaluator
from portfolio_agent.news.models import NewsRecord


class CaptureAgent:
    def __init__(self):
        self.news_by_session = {}

    def reset(self, context=None):
        self.news_by_session = {}

    def observe(self, event):
        pass

    def decide(self, observation):
        self.news_by_session[observation["session_date"]] = [
            item["headline"] for item in observation["news"]
        ]
        return {"AAPL": 0.5}


def test_evaluator_does_not_show_post_cutoff_news_until_next_session(tmp_path):
    cfg = load_config(
        None,
        overrides=[
            "evaluation.horizon_trading_days=2",
            "evaluation.pre_roll_days=0",
        ],
    )
    dates = pd.to_datetime(["2026-06-05", "2026-06-08"])
    prices = {
        "AAPL": pd.DataFrame(
            {
                "date": dates,
                "adj_open": [100.0, 101.0],
                "adj_close": [101.0, 102.0],
                "volume": [1_000_000, 1_000_000],
            }
        ),
        "MSFT": pd.DataFrame(
            {
                "date": dates,
                "adj_open": [50.0, 51.0],
                "adj_close": [51.0, 52.0],
                "volume": [1_000_000, 1_000_000],
            }
        ),
    }
    visible_time = datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)
    post_cutoff_time = datetime(2026, 6, 5, 21, 0, tzinfo=timezone.utc)
    news = [
        NewsRecord(
            "unit",
            "visible",
            visible_time,
            visible_time,
            visible_time,
            visible_time,
            ["AAPL"],
            ["Apple Inc."],
            "visible news",
            "",
            "unit",
            "",
            "h1",
            "",
        ),
        NewsRecord(
            "unit",
            "post_cutoff",
            post_cutoff_time,
            post_cutoff_time,
            post_cutoff_time,
            post_cutoff_time,
            ["AAPL"],
            ["Apple Inc."],
            "post-cutoff news",
            "",
            "unit",
            "",
            "h2",
            "",
        ),
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

    assert "visible news" in agent.news_by_session["2026-06-05"]
    assert "post-cutoff news" not in agent.news_by_session["2026-06-05"]
    assert "post-cutoff news" in agent.news_by_session["2026-06-08"]


def test_evaluator_passes_risk_free_rate_to_metrics(tmp_path, monkeypatch):
    captured = {}

    def fake_compute_metrics(**kwargs):
        captured.update(kwargs)
        return {"m1_cumulative_return": 0.0}

    monkeypatch.setattr("portfolio_agent.evaluator.compute_metrics", fake_compute_metrics)
    cfg = load_config(
        None,
        overrides=[
            "evaluation.horizon_trading_days=1",
            "evaluation.risk_free_rate=0.05",
        ],
    )
    dates = pd.to_datetime(["2026-06-05"])
    prices = {
        "AAPL": pd.DataFrame({"date": dates, "adj_open": [100.0], "adj_close": [101.0], "volume": [1]}),
    }
    evaluator = DailyTradingEvaluator.from_frames(
        prices=prices,
        fundamentals=pd.DataFrame(),
        universe={"Technology": ["AAPL"]},
        config=cfg,
        news_records=[],
    )
    evaluator.run_agent(CaptureAgent(), "capture", tmp_path)
    assert captured["risk_free_rate"] == 0.05


def test_evaluator_counts_agent_reported_decision_failure_as_violation(tmp_path):
    class ReportFailureAgent:
        def reset(self, context=None):
            self.last_decision_violation = None

        def decide(self, observation):
            self.last_decision_violation = "llm_parse_failure"
            return {}

    cfg = load_config(None, overrides=["evaluation.horizon_trading_days=1"])
    dates = pd.to_datetime(["2026-06-05"])
    prices = {
        "AAPL": pd.DataFrame({"date": dates, "adj_open": [100.0], "adj_close": [101.0], "volume": [1]}),
    }
    evaluator = DailyTradingEvaluator.from_frames(
        prices=prices,
        fundamentals=pd.DataFrame(),
        universe={"Technology": ["AAPL"]},
        config=cfg,
        news_records=[],
    )
    result = evaluator.run_agent(ReportFailureAgent(), "failure", tmp_path)
    assert result["metrics"]["m9_violation_rate"] == 1.0


def test_evaluator_writes_complete_json_event_log(tmp_path):
    cfg = load_config(
        None,
        overrides=[
            "evaluation.horizon_trading_days=1",
            "evaluation.pre_roll_days=0",
        ],
    )
    session_time = datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)
    dates = pd.to_datetime(["2026-06-05"])
    prices = {
        "AAPL": pd.DataFrame(
            {
                "date": dates,
                "adj_open": [100.0],
                "adj_close": [101.0],
                "volume": [1_000_000],
            }
        ),
    }
    news = [
        NewsRecord(
            "unit",
            "news-1",
            session_time,
            session_time,
            session_time,
            session_time,
            ["AAPL"],
            ["Apple Inc."],
            "Apple wins a major contract",
            "Contract value exceeded expectations.",
            "unit",
            "https://example.com/news-1",
            "hash-1",
            "",
        )
    ]
    evaluator = DailyTradingEvaluator.from_frames(
        prices=prices,
        fundamentals=pd.DataFrame(),
        universe={"Technology": ["AAPL"]},
        config=cfg,
        news_records=news,
    )

    result = evaluator.run_agent(CaptureAgent(), "capture", tmp_path)

    event_log_path = tmp_path / "event_log.json"
    event_log = json.loads(event_log_path.read_text(encoding="utf-8"))
    assert event_log["agent_name"] == "capture"
    assert event_log["metrics"] == result["metrics"]
    assert event_log["run_manifest"] == result["run_manifest"]
    assert len(event_log["sessions"]) == 1

    session = event_log["sessions"][0]
    assert session["session_date"] == "2026-06-05"
    assert session["observation"]["news"][0]["headline"] == "Apple wins a major contract"
    assert session["raw_action"] == {"AAPL": 0.5}
    assert session["sanitized_action"] == {"AAPL": 0.3}
    assert session["portfolio_before"]["cash_ratio"] == 1.0
    assert session["portfolio_after"]["nav"] == session["daily_record"]["nav"]
    assert session["running_metrics"]["sessions_completed"] == 1
    assert session["trades"]
