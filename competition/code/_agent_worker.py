"""Subprocess entrypoint that runs ONE participant agent on ONE observation.

Invoked by runner.run_agent in an isolated process so a crash, hang, or memory blow-up in
a team's code cannot take down the server. Prints a single JSON result line to stdout;
everything the agent prints goes to stderr and is captured as the audit log.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


def _load_agent_class(code_dir: Path):
    # Make `from agent_base import BaseAgent` work for participant code.
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # sdk/
    sys.path.insert(0, str(code_dir))                          # team code
    spec = importlib.util.spec_from_file_location("participant_agent", code_dir / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "Agent"):
        raise RuntimeError("agent.py must define a class named `Agent`")
    return module.Agent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--observation", required=True)
    ap.add_argument("--setup", help="JSON with {universe, constraints} for setup()")
    args = ap.parse_args()

    try:
        Agent = _load_agent_class(Path(args.code_dir))
        agent = Agent()
        if args.setup:
            s = json.loads(Path(args.setup).read_text())
            agent.setup(s.get("universe", []), s.get("constraints", {}))
        observation = json.loads(Path(args.observation).read_text())
        t0 = time.time()
        weights = agent.decide(observation)
        compute_seconds = time.time() - t0
        result = {
            "ok": True,
            "target_weights": {str(k): float(v) for k, v in dict(weights).items()},
            "agent_version": getattr(agent, "agent_version", "?"),
            "declared_news_sources": list(getattr(agent, "declared_news_sources", lambda: [])()),
            "compute_seconds": round(compute_seconds, 4),
        }
    except Exception as exc:  # noqa: BLE001 -- any failure is reported, never crashes server
        import traceback
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc()}
        print(json.dumps(result))
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
