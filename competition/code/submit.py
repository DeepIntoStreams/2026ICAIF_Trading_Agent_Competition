"""Participant tool: read the organizer's request, run YOUR agent, POST your weights.

In the live competition each team runs this on its OWN machine each day. It reads the day's
decision_request (published by the organizer), runs your `agent.py`, and writes a
decision_response to the posts directory the organizer collects from. The organizer never
runs your code during the live event -- only your posted weights are used (your code is
re-run only for the end-of-competition reproducibility audit).

Live, the two disk steps become an HTTP GET of the request and an HTTP POST of the response;
nothing else changes.

  python competition/sdk/submit.py --requests-dir competition/requests \
      --code competition/code --team-id team_example --posts-dir competition/posts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner import run_agent


def submit_all(requests_dir: str, code_dir: str, team_id: str, posts_dir: str,
               timeout_seconds: float = 60.0) -> int:
    reqs = sorted(Path(requests_dir, team_id).glob("*.json"))
    out = Path(posts_dir, team_id)
    out.mkdir(parents=True, exist_ok=True)
    posted = 0
    for rp in reqs:
        req = json.loads(rp.read_text())
        obs = req["observation"]
        res = run_agent(code_dir, obs, obs.get("assets"), obs.get("constraints"),
                        timeout_seconds)
        if not res["ok"]:
            continue                                   # team-side failure -> post nothing
        (out / rp.name).write_text(json.dumps({
            "type": "decision_response", "protocol_version": "0.1",
            "team_id": team_id, "session_date": req["session_date"],
            "target_weights": res["target_weights"],
            "metadata": {"agent_version": res["audit"].get("agent_version")},
        }, indent=2))
        posted += 1
    return posted


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--requests-dir", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--team-id", required=True)
    p.add_argument("--posts-dir", required=True)
    p.add_argument("--timeout", type=float, default=60.0)
    args = p.parse_args(argv)
    n = submit_all(args.requests_dir, args.code, args.team_id, args.posts_dir, args.timeout)
    print(f"[submit] {args.team_id}: posted {n} decision_responses to {args.posts_dir}/{args.team_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
