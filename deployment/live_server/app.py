"""FastAPI application for the PostgreSQL participant receiver."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .store import CompetitionStore


FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^decision_response$")
    protocol_version: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    run_id: str = Field(min_length=1)
    session_date: date
    target_weights: dict[str, FiniteFloat]
    team_id: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] | None = None


class TeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_code: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


class TradingDayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trading_date: date
    market_open_at: datetime
    market_close_at: datetime
    submission_open_at: datetime
    submission_deadline_at: datetime

    @field_validator("market_open_at", "market_close_at",
                     "submission_open_at", "submission_deadline_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value


bearer = HTTPBearer(auto_error=False)


def create_app(store: CompetitionStore, admin_token: str,
               max_request_bytes: int = 65_536) -> FastAPI:
    app = FastAPI(
        title="ICAIF 2026 Competition Receiver API",
        version="0.2.0",
        description="PostgreSQL-backed participant observation and raw-decision receiver.",
    )

    @app.middleware("http")
    async def stamp_request_arrival(request: Request, call_next):
        request.state.received_at = datetime.now(timezone.utc)
        return await call_next(request)

    def authenticated_team(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> str:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        team = store.team_for_api_key(credentials.credentials)
        if not team:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        return team

    def require_admin(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        if (not credentials or credentials.scheme.lower() != "bearer" or
                not secrets_compare(credentials.credentials, admin_token)):
            raise HTTPException(status_code=401, detail="invalid organizer credential")

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        if not store.health():
            raise HTTPException(status_code=503, detail="database unavailable")
        return {"status": "ok", "database": "ok"}

    @app.get("/api/v1/me/status", tags=["participant"])
    def team_status(team: str = Depends(authenticated_team)) -> dict[str, Any]:
        return store.team_status(team)

    @app.get("/api/v1/me/observation", tags=["participant"])
    def observation(team: str = Depends(authenticated_team)) -> dict[str, Any]:
        value = store.team_observation(team)
        if value is None:
            raise HTTPException(status_code=404, detail="no published observation is available")
        return value

    @app.post(
        "/api/v1/decisions",
        tags=["participant"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": DecisionRequest.model_json_schema()}
                },
            }
        },
    )
    async def submit_decision(
        request: Request,
        team: str = Depends(authenticated_team),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        received_at = request.state.received_at
        request_id = str(uuid.uuid4())
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_request_bytes:
            store.record_invalid_attempt(
                team, request_id, idempotency_key, received_at, None, None,
                "request_body_too_large",
            )
            raise HTTPException(status_code=413, detail="request body too large")
        body = await request.body()
        if len(body) > max_request_bytes:
            store.record_invalid_attempt(
                team, request_id, idempotency_key, received_at, None,
                hashlib.sha256(body).hexdigest(), "request_body_too_large",
            )
            raise HTTPException(status_code=413, detail="request body too large")
        raw_hash = hashlib.sha256(body).hexdigest()
        document: dict[str, Any] | None = None
        try:
            candidate = json.loads(body)
            if isinstance(candidate, dict):
                document = candidate
            parsed = DecisionRequest.model_validate(candidate)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            store.record_invalid_attempt(
                team, request_id, idempotency_key, received_at, document, raw_hash,
                "invalid_decision_envelope",
            )
            detail = json.loads(exc.json()) if isinstance(exc, ValidationError) else \
                "invalid JSON object"
            raise HTTPException(status_code=422, detail=detail) from exc
        if not idempotency_key or len(idempotency_key) > 200:
            store.record_invalid_attempt(
                team, request_id, idempotency_key, received_at, document, raw_hash,
                "invalid_idempotency_key",
            )
            raise HTTPException(status_code=400,
                                detail="a valid Idempotency-Key header is required")

        # Validation is structural only. Persist the participant's parsed JSON
        # exactly as received instead of Pydantic's coerced representation.
        assert document is not None
        result = store.submit(team, document, idempotency_key, received_at, request_id)
        if result["accepted"]:
            return fastapi_json(result, 200 if result["idempotent"] else 201)
        if result["reason"] in {
            "idempotency_key_reused", "decision_already_accepted",
            "outside_submission_window", "submission_window_not_configured",
        }:
            code = status.HTTP_409_CONFLICT
        elif result["reason"] == "team_mismatch":
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        else:
            code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=code, detail=result)

    @app.post("/api/v1/admin/teams", status_code=201,
              dependencies=[Depends(require_admin)], tags=["organizer"])
    def create_team(document: TeamRequest) -> dict[str, str]:
        try:
            api_key = store.register_team(document.team_code, document.display_name)
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise HTTPException(status_code=409, detail="team already exists") from exc
            raise
        return {"team_code": document.team_code, "api_key": api_key}

    @app.post("/api/v1/admin/trading-days", tags=["organizer"],
              dependencies=[Depends(require_admin)])
    def create_trading_day(document: TradingDayRequest):
        try:
            row, idempotent = store.create_trading_day(**document.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return fastapi_json({
            "trading_date": row["trading_date"].isoformat(),
            "id": row["id"],
            "idempotent": idempotent,
        }, 200 if idempotent else 201)

    return app


def secrets_compare(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)


def fastapi_json(content: Any, status_code: int):
    from fastapi.responses import JSONResponse
    return JSONResponse(content=content, status_code=status_code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default=os.environ.get("COMPETITION_DATABASE_URL"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("COMPETITION_DATABASE_URL is required")
    admin_token = os.environ.get("COMPETITION_ADMIN_TOKEN")
    if not admin_token:
        raise SystemExit("COMPETITION_ADMIN_TOKEN is required")
    max_bytes = int(os.environ.get("COMPETITION_MAX_REQUEST_BYTES", "65536"))
    uvicorn.run(create_app(CompetitionStore(args.database_url), admin_token, max_bytes),
                host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
