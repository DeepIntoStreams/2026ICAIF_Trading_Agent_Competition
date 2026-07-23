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
