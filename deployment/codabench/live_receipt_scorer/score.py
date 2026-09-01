"""Syntax-only live receipt scorer. Codabench receipt time remains authoritative."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(input_arg: str, output_arg: str) -> int:
    root, output = Path(input_arg), Path(output_arg)
    files = [p for p in (root / "res").rglob("*.json") if p.is_file()]
    accepted, detail = 0, "submission.json not found"
    if len(files) == 1:
        try:
            doc = json.loads(files[0].read_text())
            required = {"type", "protocol_version", "team_id", "session_date", "target_weights"}
            missing = sorted(required - set(doc))
            if doc.get("type") != "decision_response":
                detail = "type must be decision_response"
            elif missing:
                detail = "missing: " + ", ".join(missing)
            elif not isinstance(doc.get("target_weights"), dict):
                detail = "target_weights must be an object"
            else:
                accepted, detail = 1, "accepted for bridge import"
        except (ValueError, OSError) as exc:
            detail = str(exc)
    output.mkdir(parents=True, exist_ok=True)
    (output / "scores.txt").write_text(f"receipt_accepted: {accepted}\n")
    (output / "scores.json").write_text(json.dumps({"receipt_accepted": accepted}))
    (output / "scores.html").write_text(f"<p>{detail}</p>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))

