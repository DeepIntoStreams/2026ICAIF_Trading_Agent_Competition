"""Build a visible local database and exercise representative submissions.

Run directly from the repository root: python deployment/demo/quick_demo.py --reset
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make this file directly runnable without PYTHONPATH or package installation.
SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from deployment.live_server.store import LiveStore

TEAM_ID = "team_demo"


def observation(session_date: str, deadline: str) -> dict:
    return {
        "session_date": session_date,
        "event_time_utc": deadline,
        "assets": [
            {"ticker": "AAPL", "company_name": "Apple", "sector": "Technology",
             "open_price": 100.0},
            {"ticker": "MSFT", "company_name": "Microsoft", "sector": "Technology",
             "open_price": 100.0},
        ],
        "market_features": {
            "AAPL": {"momentum_20d": 0.12, "volatility_20d": 0.20},
            "MSFT": {"momentum_20d": 0.05, "volatility_20d": 0.16},
        },
        "fundamental_features": {
            "AAPL": {"profit_margin": 0.25},
            "MSFT": {"profit_margin": 0.31},
        },
        "constraints": {
            "long_only": True,
            "max_asset_weight": 0.10,
            "max_gross_exposure": 1.0,
            "fee_rate": 0.001,
        },
        "news": [],
    }


MARKET = {
    "AAPL": {"adj_open": 100.0, "adj_close": 110.0},
    "MSFT": {"adj_open": 100.0, "adj_close": 102.0},
}


def decision(session_date: str, weights: dict[str, float], team_id: str | None = None) -> dict:
    document = {
        "type": "decision_response",
        "protocol_version": "0.1",
        "run_id": "quick_demo",
        "session_date": session_date,
        "target_weights": weights,
        "metadata": {"agent_version": "demo-0.1"},
    }
    if team_id is not None:
        document["team_id"] = team_id
    return document


def build_database(path: Path, reset: bool) -> dict:
    if path.exists():
        if not reset:
            raise SystemExit(f"database already exists: {path} (pass --reset to replace it)")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    store = LiveStore(path)
    api_key = store.register_team(TEAM_ID)
    credentials_path = path.with_name("demo-credentials.json")
    credentials_path.write_text(
        json.dumps({"team_id": TEAM_ID, "api_key": api_key}, indent=2) + "\n",
        encoding="utf-8",
    )

    # A completed day gives the leaderboard some state to display.
    store.publish(observation("2026-10-27", "2026-10-27T20:00:00+00:00"), MARKET)
    valid = decision("2026-10-27", {"AAPL": 0.05})
    cases = {
        "late_rejected": store.submit(
            "case-late", TEAM_ID, valid, "2026-10-27T21:00:00+00:00"
        ),
        "team_mismatch_rejected": store.submit(
            "case-mismatch", TEAM_ID,
            decision("2026-10-27", {"AAPL": 0.05}, team_id="another_team"),
            "2026-10-27T19:00:00+00:00",
        ),
        "accepted": store.submit(
            "case-accepted", TEAM_ID, valid, "2026-10-27T19:01:00+00:00"
        ),
        "same_id_is_idempotent": store.submit(
            "case-accepted", TEAM_ID, valid, "2026-10-27T19:02:00+00:00"
        ),
        "second_decision_rejected": store.submit(
            "case-second", TEAM_ID, decision("2026-10-27", {"AAPL": 0.08}),
            "2026-10-27T19:03:00+00:00",
        ),
    }
    store.settle("2026-10-27")

    # Day two executes day one's target and demonstrates deterministic repair.
    store.publish(observation("2026-10-28", "2026-10-28T20:00:00+00:00"), MARKET)
    cases["invalid_weights_are_repaired"] = store.submit(
        "case-repair", TEAM_ID,
        decision("2026-10-28", {"AAPL": 0.25, "MSFT": -0.10, "UNKNOWN": 0.50}),
        "2026-10-28T19:00:00+00:00",
    )
    store.settle("2026-10-28")

    # The latest day has no decision and is ready for the real Starter Kit client.
    store.publish(observation("2026-10-29", "2099-10-29T20:00:00+00:00"), MARKET)

    tables = [
        row[0]
        for row in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    counts = {
        table: store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }
    result = {
        "database": str(path),
        "credentials_file": str(credentials_path),
        "credentials": {"team_id": TEAM_ID, "api_key": api_key},
        "warning": "the generated plaintext key is for this local demo only",
        "tables": counts,
        "submission_cases": cases,
        "team_status": store.team_status(TEAM_ID),
        "leaderboard": store.leaderboard(),
    }
    store.db.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=SCRIPT_DIR / "icaif-demo.sqlite3")
    parser.add_argument("--reset", action="store_true", help="replace an existing demo database")
    args = parser.parse_args()
    print(json.dumps(build_database(args.db, args.reset), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
