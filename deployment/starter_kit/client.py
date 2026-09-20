"""One-shot reference client using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import os
import uuid
import urllib.error
import urllib.request

from .agent import Agent


class CompetitionClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0,
                 run_id: str = "official_2026"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.run_id = run_id

    def request(self, method: str, path: str, body: dict | None = None,
                idempotency_key: str | None = None) -> dict:
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(self.base_url + path, data=payload,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = json.loads(exc.read() or b"{}")
            raise RuntimeError(f"server returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"cannot reach competition server: {exc.reason}") from exc

    def status(self) -> dict:
        return self.request("GET", "/api/v1/me/status")

    def observation(self) -> dict:
        return self.request("GET", "/api/v1/me/observation")

    def submit(self, decision: dict, idempotency_key: str | None = None) -> dict:
        return self.request("POST", "/api/v1/decisions", decision,
                            idempotency_key or str(uuid.uuid4()))


def run_once(client: CompetitionClient, agent: Agent) -> dict:
    status = client.status()
    session = status.get("session")
    if not session or not session.get("observation_available"):
        return {"action": "wait", "reason": "no_observation_available", "status": status}
    if session.get("decision_accepted"):
        return {"action": "wait", "reason": "decision_already_accepted", "status": status}

    observation = client.observation()
    decision = {
        "type": "decision_response",
        "protocol_version": "0.1",
        "run_id": client.run_id,
        "session_date": observation["session_date"],
        "target_weights": agent.decide(observation),
        "metadata": {"agent_version": agent.agent_version},
    }
    return {"action": "submitted", "receipt": client.submit(decision)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("COMPETITION_BASE_URL",
                                                             "http://127.0.0.1:8080"))
    parser.add_argument("--api-key", default=os.environ.get("COMPETITION_API_KEY"))
    parser.add_argument("--run-id", default=os.environ.get("COMPETITION_RUN_ID",
                                                           "official_2026"))
    args = parser.parse_args()
    if not args.api_key:
        raise SystemExit("set COMPETITION_API_KEY or pass --api-key")
    print(json.dumps(run_once(
        CompetitionClient(args.base_url, args.api_key, run_id=args.run_id), Agent()
    ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
