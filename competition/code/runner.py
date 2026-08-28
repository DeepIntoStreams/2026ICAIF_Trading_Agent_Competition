"""Server-side isolated runner: invoke a participant agent on one observation.

This is the local stand-in for the eventual web request. `run_agent` spawns the worker in
a fresh subprocess with a hard wall-clock timeout, captures the returned weights, the
agent's own log output (stderr), timing, and the code hash, and never lets participant code
crash or hang the server. On timeout / crash / bad output it returns ok=False so the caller
can apply the "retain previous weights" policy.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

WORKER = Path(__file__).resolve().parent / "_agent_worker.py"


def code_hash(code_dir: str | Path) -> str:
    """Deterministic hash of a submission's *.py files, for the reproducibility audit."""
    h = hashlib.sha256()
    for path in sorted(Path(code_dir).rglob("*.py")):
        h.update(path.relative_to(code_dir).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def run_agent(
    code_dir: str | Path,
    observation: dict[str, Any],
    universe: list[dict[str, Any]] | None = None,
    constraints: dict[str, Any] | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Run one team's agent on one observation. Returns an audited result dict."""
    code_dir = Path(code_dir)
    with tempfile.TemporaryDirectory() as tmp:
        obs_path = Path(tmp) / "observation.json"
        obs_path.write_text(json.dumps(observation))
        setup_path = Path(tmp) / "setup.json"
        setup_path.write_text(json.dumps(
            {"universe": universe or observation.get("assets", []),
             "constraints": constraints or observation.get("constraints", {})}))

        cmd = [sys.executable, str(WORKER), "--code-dir", str(code_dir),
               "--observation", str(obs_path), "--setup", str(setup_path)]
        audit: dict[str, Any] = {"code_hash": code_hash(code_dir),
                                 "session_date": observation.get("session_date")}
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            audit["reason"] = "timeout"
            return {"ok": False, "reason": "timeout", "audit": audit}

        audit["stderr"] = proc.stderr[-4000:]        # the agent's own log output
        audit["returncode"] = proc.returncode
        # The result is the last JSON line printed to stdout.
        line = next((l for l in reversed(proc.stdout.splitlines()) if l.strip().startswith("{")), "")
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            audit["reason"] = "no_valid_output"
            audit["stdout_tail"] = proc.stdout[-2000:]
            return {"ok": False, "reason": "no_valid_output", "audit": audit}

        if not result.get("ok"):
            audit["reason"] = result.get("error", "agent_error")
            audit["traceback"] = result.get("traceback", "")
            return {"ok": False, "reason": audit["reason"], "audit": audit}

        audit["agent_version"] = result.get("agent_version")
        audit["declared_news_sources"] = result.get("declared_news_sources", [])
        audit["compute_seconds"] = result.get("compute_seconds")
        return {"ok": True, "target_weights": result["target_weights"], "audit": audit}
