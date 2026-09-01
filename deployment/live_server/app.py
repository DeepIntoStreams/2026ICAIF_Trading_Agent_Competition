"""Minimal authenticated HTTP API for the unified competition service."""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .store import LiveStore


def handler_for(store: LiveStore, admin_token: str):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body):
            payload = json.dumps(body, default=str).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

        def body(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size > 1_000_000: raise ValueError("request too large")
            return json.loads(self.rfile.read(size) or b"{}")

        def auth(self, team):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            return store.authenticate(team, token)

        def team(self):
            value = self.headers.get("Authorization", "")
            if not value.startswith("Bearer "):
                return None
            return store.team_for_api_key(value.removeprefix("Bearer "))

        def admin(self):
            return self.headers.get("Authorization") == f"Bearer {admin_token}"

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health": return self.reply(200, {"status": "ok"})
            if path == "/api/v1/leaderboard": return self.reply(200, store.leaderboard())
            if path in ("/api/v1/me/observation", "/api/v1/me/state"):
                team = self.team()
                if not team: return self.reply(401, {"error": "unauthorized"})
                value = store.team_observation(team) if path.endswith("observation") else store.state(team)
                return self.reply(200 if value else 404, value or {"error": "not_found"})
            match = re.fullmatch(r"/api/v1/teams/([^/]+)/(observation|state)", path)
            if match and self.auth(match.group(1)):
                value = store.team_observation(match.group(1)) if match.group(2) == "observation" \
                        else store.state(match.group(1))
                return self.reply(200 if value else 404, value or {"error": "not_found"})
            return self.reply(401 if match else 404, {"error": "unauthorized" if match else "not_found"})

        def do_POST(self):
            path = urlparse(self.path).path
            try: doc = self.body()
            except (ValueError, json.JSONDecodeError) as exc: return self.reply(400, {"error": str(exc)})
            if path == "/api/v1/decisions":
                team = self.team()
                if not team: return self.reply(401, {"error": "unauthorized"})
                source_id = self.headers.get("Idempotency-Key")
                if not source_id:
                    return self.reply(400, {"error": "missing_idempotency_key"})
                result = store.submit(source_id, team, doc)
                return self.reply(201 if result["accepted"] else 422, result)
            if path == "/api/v1/admin/submissions/import" and self.admin():
                result = store.submit(doc["source_id"], doc["authenticated_team"],
                                      doc["submission"], doc.get("received_at"))
                return self.reply(200 if result["accepted"] else 422, result)
            if path == "/api/v1/admin/sessions" and self.admin():
                store.publish(doc["observation"], doc["market"]); return self.reply(201, {"published": True})
            if path == "/api/v1/admin/settle" and self.admin():
                return self.reply(200, store.settle(doc["session_date"]))
            return self.reply(401 if path.startswith("/api/v1/admin") else 404, {"error": "unauthorized"})

        def log_message(self, fmt, *args):
            print("[live-api]", fmt % args)
    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("LIVE_DB", "live.sqlite3"))
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    token = os.environ.get("LIVE_ADMIN_TOKEN")
    if not token: raise SystemExit("LIVE_ADMIN_TOKEN is required")
    server = ThreadingHTTPServer((args.host, args.port), handler_for(LiveStore(args.db), token))
    print(f"live API listening on http://{args.host}:{args.port}")
    server.serve_forever(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
