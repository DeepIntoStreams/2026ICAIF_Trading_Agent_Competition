from portfolio_agent.config import load_config
from portfolio_agent.evaluator import build_run_manifest


def test_run_manifest_records_resolved_config_and_llm_settings():
    cfg = load_config(
        None,
        overrides=[
            "evaluation.horizon_trading_days=5",
            "agents.llm.model=local/model",
        ],
    )
    manifest = build_run_manifest(
        config=cfg,
        agent_name="llm",
        market_hashes={"AAPL.csv": "abc"},
        news_hashes={"normalized.jsonl": "def"},
        code_state={"commit": "unit", "dirty": True},
    )
    assert manifest["resolved_config"]["evaluation"]["horizon_trading_days"] == 5
    assert manifest["resolved_config"]["agents"]["llm"]["model"] == "local/model"
    assert manifest["llm"]["model"] == "local/model"
    assert manifest["news_hashes"]["normalized.jsonl"] == "def"
