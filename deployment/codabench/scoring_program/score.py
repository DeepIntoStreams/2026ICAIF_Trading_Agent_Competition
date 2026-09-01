"""Turn ingestion output into Codabench scores."""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

KEYS = [f"m{i}_{name}" for i, name in enumerate((
    "cumulative_return", "daily_win_rate", "sharpe_ratio", "sortino_ratio",
    "maximum_drawdown", "value_at_risk_95", "expected_shortfall_95", "turnover",
    "violation_rate"), 1)]


def main(input_arg: str, output_arg: str) -> int:
    input_dir, output = Path(input_arg), Path(output_arg)
    candidates = list(input_dir.rglob("evaluation.json"))
    if not candidates:
        raise FileNotFoundError("ingestion did not produce evaluation.json")
    result = json.loads(candidates[0].read_text())
    metrics = {key: result["metrics"].get(key) for key in KEYS}
    output.mkdir(parents=True, exist_ok=True)
    (output / "scores.json").write_text(json.dumps(metrics, indent=2))
    (output / "scores.txt").write_text("\n".join(f"{k}: {v}" for k, v in metrics.items()) + "\n")
    rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>" for k, v in metrics.items())
    (output / "scores.html").write_text(f"<h1>ICAIF validation</h1><table>{rows}</table>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))

