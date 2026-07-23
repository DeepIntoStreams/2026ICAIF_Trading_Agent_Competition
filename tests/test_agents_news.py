from portfolio_agent.agents.hybrid_rule import HybridRuleAgent
from portfolio_agent.agents.llm_allocation import LLMAllocationAgent
from portfolio_agent.agents.ppo_portfolio import NewsTiltedPPOAgent


def _observation(news_headline: str):
    return {
        "session_date": "2026-06-05",
        "event_time_utc": "2026-06-05T19:50:00+00:00",
        "assets": [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "open_price": 100.0,
            },
            {
                "ticker": "MSFT",
                "company_name": "Microsoft Corp.",
                "open_price": 50.0,
            },
        ],
        "market_features": {
            "AAPL": {
                "return_20d": 0.05,
                "momentum_60d": 0.10,
                "volatility_20d": 0.02,
                "sma_distance_50": 0.05,
            },
            "MSFT": {
                "return_20d": 0.05,
                "momentum_60d": 0.10,
                "volatility_20d": 0.02,
                "sma_distance_50": 0.05,
            },
        },
        "fundamental_features": {
            "AAPL": {
                "revenue_yoy": 0.05,
                "fcf_margin": 0.2,
                "net_margin": 0.2,
                "roe": 0.2,
                "debt_to_assets": 0.2,
            },
            "MSFT": {
                "revenue_yoy": 0.05,
                "fcf_margin": 0.2,
                "net_margin": 0.2,
                "roe": 0.2,
                "debt_to_assets": 0.2,
            },
        },
        "portfolio": {
            "weights": {"AAPL": 0.0, "MSFT": 0.0},
            "cash_ratio": 1.0,
            "nav": 1_000_000.0,
        },
        "constraints": {
            "max_asset_weight": 0.30,
            "max_gross_exposure": 1.0,
        },
        "news": [
            {
                "ticker": "AAPL",
                "headline": news_headline,
                "summary": "",
                "available_at_utc": "2026-06-05T18:00:00+00:00",
            }
        ],
    }


def test_hybrid_agent_reacts_to_positive_news():
    agent = HybridRuleAgent(rebalance_frequency=1, news_weight=0.20)
    weights = agent.decide(_observation("Apple raises guidance after earnings beat"))
    assert weights.get("AAPL", 0.0) >= weights.get("MSFT", 0.0)


def test_news_tilted_ppo_keeps_output_feasible():
    agent = NewsTiltedPPOAgent(news_beta=0.5, news_count_gamma=0.1)
    weights = agent.decide(_observation("Apple raises guidance after earnings beat"))
    assert sum(weights.values()) <= 1.0
    assert all(value >= 0 for value in weights.values())


def test_llm_agent_uses_configured_endpoint_and_news(monkeypatch):
    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        assert "Apple Inc." in kwargs["user_prompt"]
        assert "Apple raises guidance" in kwargs["user_prompt"]
        return '{"target_weights": {"AAPL": 0.3}}'

    monkeypatch.setattr(
        "portfolio_agent.agents.llm_allocation._call_llm",
        fake_call_llm,
    )
    agent = LLMAllocationAgent(
        base_url="http://localhost:8000/v1",
        model_name="google/gemma-4-31B-it",
    )
    weights = agent.decide(_observation("Apple raises guidance after earnings beat"))
    assert captured["base_url"] == "http://localhost:8000/v1"
    assert captured["model"] == "google/gemma-4-31B-it"
    assert weights == {"AAPL": 0.3}
