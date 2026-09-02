"""FastAPI application for the unified competition service."""

from __future__ import annotations

import argparse
import os
from typing import Any, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .store import LiveStore


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^decision_response$")
    protocol_version: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    run_id: str = Field(min_length=1)
    session_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    target_weights: dict[str, float]
    team_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation: dict[str, Any]
    market: dict[str, Any]


class SettlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


bearer = HTTPBearer(auto_error=False)


def create_app(store: LiveStore, admin_token: str) -> FastAPI:
    app = FastAPI(
        title="ICAIF 2026 Competition API",
        version="0.1.0",
        description=(
            "Team-authenticated observation, status and decision API shared by Validation "
            "and the Official Competition. Participant agents always run locally."
        ),
    )

    def authenticated_team(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> str:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="invalid or missing API key")
        team = store.team_for_api_key(credentials.credentials)
        if not team:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="invalid or missing API key")
        return team

    def require_admin(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> None:
        if not credentials or credentials.credentials != admin_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="invalid organizer credential")

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/me/status", tags=["participant"])
    def team_status(team: str = Depends(authenticated_team)) -> dict[str, Any]:
        return store.team_status(team)

    @app.get("/api/v1/me/observation", tags=["participant"])
    def observation(team: str = Depends(authenticated_team)) -> dict[str, Any]:
        value = store.team_observation(team)
        if value is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="no observation is currently available")
        return value

    @app.get("/api/v1/me/state", tags=["participant"])
    def state(team: str = Depends(authenticated_team)) -> dict[str, Any]:
        return store.state(team)

    @app.post("/api/v1/decisions", status_code=status.HTTP_201_CREATED,
              tags=["participant"])
    def submit_decision(
        document: DecisionRequest,
        team: str = Depends(authenticated_team),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="a valid Idempotency-Key header is required")
        result = store.submit(idempotency_key, team, document.model_dump(exclude_none=True))
        if not result["accepted"]:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result)
        return result

    @app.get("/api/v1/leaderboard", tags=["public"])
    def leaderboard() -> list[dict[str, Any]]:
        return store.leaderboard()

    @app.post("/api/v1/admin/sessions", status_code=status.HTTP_201_CREATED,
              dependencies=[Depends(require_admin)], tags=["organizer"])
    def load_session(document: SessionRequest) -> dict[str, bool]:
        store.publish(document.observation, document.market)
        return {"loaded": True}

    @app.post("/api/v1/admin/settle", dependencies=[Depends(require_admin)],
              tags=["organizer"])
    def settle(document: SettlementRequest) -> list[dict[str, Any]]:
        try:
            return store.settle(document.session_date)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("COMPETITION_DB", "competition.sqlite3"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    admin_token = os.environ.get("COMPETITION_ADMIN_TOKEN")
    if not admin_token:
        raise SystemExit("COMPETITION_ADMIN_TOKEN is required")
    uvicorn.run(create_app(LiveStore(args.db), admin_token), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
