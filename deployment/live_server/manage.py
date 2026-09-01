"""Organizer CLI for team registration and local JSON imports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .store import LiveStore


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--db", default="competition.sqlite3")
    sub = ap.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register-team"); reg.add_argument("team_id")
    pub = sub.add_parser("publish"); pub.add_argument("observation"); pub.add_argument("market")
    imp = sub.add_parser("import-submission"); imp.add_argument("source_id"); imp.add_argument("team_id")
    imp.add_argument("submission"); imp.add_argument("--received-at")
    settle = sub.add_parser("settle"); settle.add_argument("session_date")
    sub.add_parser("leaderboard")
    args = ap.parse_args(); store = LiveStore(args.db)
    if args.command == "register-team": print(store.register_team(args.team_id))
    elif args.command == "publish":
        store.publish(json.loads(Path(args.observation).read_text()), json.loads(Path(args.market).read_text()))
    elif args.command == "import-submission":
        print(json.dumps(store.submit(args.source_id, args.team_id,
              json.loads(Path(args.submission).read_text()), args.received_at), indent=2))
    elif args.command == "settle": print(json.dumps(store.settle(args.session_date), indent=2))
    else: print(json.dumps(store.leaderboard(), indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
