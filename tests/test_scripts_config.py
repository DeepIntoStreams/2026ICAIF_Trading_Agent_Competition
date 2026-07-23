from scripts.run_evaluation import parse_args


def test_run_evaluation_accepts_repeated_overrides():
    args = parse_args(
        [
            "--data-root",
            "data/stock_data_1y",
            "--output-root",
            "outputs/test",
            "--set",
            "evaluation.horizon_trading_days=5",
            "--set",
            "agents.llm.model=local/model",
        ]
    )
    assert args.set == [
        "evaluation.horizon_trading_days=5",
        "agents.llm.model=local/model",
    ]
