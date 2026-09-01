"""Provider-neutral Codabench export importer.

The Codabench API client remains intentionally isolated: export submissions as JSONL with the
fields below, then import them here. Once the target Codabench instance/API version is confirmed,
only the exporter needs to change; live settlement remains stable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .store import LiveStore


def import_jsonl(store: LiveStore, path: str | Path) -> dict[str, int]:
    counts = {"accepted": 0, "rejected": 0, "idempotent": 0}
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip(): continue
        item = json.loads(line)
        for key in ("codabench_submission_id", "codabench_team_id", "submitted_at", "submission"):
            if key not in item: raise ValueError(f"line {number}: missing {key}")
        result = store.submit(str(item["codabench_submission_id"]), str(item["codabench_team_id"]),
                              item["submission"], item["submitted_at"])
        if result.get("idempotent"): counts["idempotent"] += 1
        elif result["accepted"]: counts["accepted"] += 1
        else: counts["rejected"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--db", default="live.sqlite3")
    ap.add_argument("export_jsonl"); args = ap.parse_args()
    print(json.dumps(import_jsonl(LiveStore(args.db), args.export_jsonl), indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())

