"""Codabench validation ingestion adapter."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def _find_alpha(root: Path) -> Path:
    candidates = [root, root / "alpha"]
    candidates.extend(p.parent for p in root.rglob("agent.py"))
    for candidate in candidates:
        if (candidate / "agent.py").is_file():
            return candidate
    raise ValueError("submission must contain agent.py at its root or under alpha/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--hidden", required=True)
    args = ap.parse_args()

    submission, input_dir = Path(args.submission), Path(args.input)
    output, hidden = Path(args.output), Path(args.hidden)
    output.mkdir(parents=True, exist_ok=True)
    work = output / "work"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(input_dir, work)

    # Codabench extracts this program under /app/program while the immutable evaluator
    # source is baked into the image under /opt/competition.
    repo = Path(os.environ.get("ICAIF_REPO_ROOT", "/opt/competition"))
    if not (repo / "competition" / "code" / "engine.py").exists():
        repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "competition" / "code"))
    import engine

    config_path = hidden / "evaluation_config.json"
    config = json.loads(config_path.read_text())
    result = engine.run_team(
        str(work), "validation", str(hidden / "market_data"),
        code_dir=str(_find_alpha(submission)),
        timeout_seconds=float(config.get("agent_timeout_seconds", 60)),
    )
    (output / "evaluation.json").write_text(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
