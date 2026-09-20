"""FastAPI application for the PostgreSQL participant receiver."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from functools import partial
from typing import Annotated, Any

import psycopg
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from .request_control import (
    AdmissionGate,
    InvalidJsonValue,
    RequestBodyTimedOut,
    RequestBodyTooLarge,
    TokenBucketLimiter,
    read_bounded_body,
    validate_json_tree,
)
from .store import CompetitionStore


FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^decision_response$")
    protocol_version: str = Field(min_length=1, max_length=20)
    run_id: str = Field(min_length=1, max_length=100)
    session_date: date
    target_weights: dict[str, FiniteFloat]
    team_id: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] | None = None

    @field_validator("target_weights")
    @classmethod
    def validate_tickers(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > 1_000:
            raise ValueError("target_weights contains too many entries")
        if any(not ticker or len(ticker) > 64 for ticker in value):
            raise ValueError("target_weights contains an invalid ticker")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            agent_version = value.get("agent_version")
            if agent_version is not None and (
                not isinstance(agent_version, str) or len(agent_version) > 200
            ):
                raise ValueError("metadata.agent_version must be a string of at most 200 chars")
        return value


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


class ApiKeyRotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grace_seconds: int = Field(default=0, ge=0, le=3_600)


bearer = HTTPBearer(auto_error=False)


def create_app(
    store: CompetitionStore,
    admin_token: str,
    max_request_bytes: int = 65_536,
    *,
    current_run_id: str = "official_2026",
    supported_protocol_versions: frozenset[str] = frozenset({"0.1"}),
    body_read_timeout_seconds: float = 10.0,
    max_inflight_decisions: int = 256,
    intake_db_workers: int = 24,
    api_requests_per_minute: int = 120,
    decision_requests_per_minute: int = 30,
    ip_requests_per_minute: int = 600,
) -> FastAPI:
    current_run_id = current_run_id.strip()
    if not current_run_id:
        raise ValueError("current_run_id must not be empty")
    if not supported_protocol_versions:
        raise ValueError("at least one protocol version must be supported")
    if max_request_bytes <= 0 or body_read_timeout_seconds <= 0:
        raise ValueError("request size and timeout settings must be positive")
    if min(
        max_inflight_decisions,
        intake_db_workers,
        api_requests_per_minute,
        decision_requests_per_minute,
        ip_requests_per_minute,
    ) <= 0:
        raise ValueError("receiver capacity settings must be positive")

    intake_executor = ThreadPoolExecutor(
        max_workers=intake_db_workers, thread_name_prefix="receiver-intake-db"
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            intake_executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(
        title="ICAIF 2026 Competition Receiver API",
        version="0.2.0",
        description="PostgreSQL-backed participant observation and raw-decision receiver.",
        lifespan=lifespan,
    )

    decision_gate = AdmissionGate(max_inflight_decisions)
    api_limiter = TokenBucketLimiter(api_requests_per_minute, 60)
    decision_limiter = TokenBucketLimiter(decision_requests_per_minute, 60)
    ip_limiter = TokenBucketLimiter(ip_requests_per_minute, 60)

    async def intake_store_call(function, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            intake_executor, partial(function, *args, **kwargs)
        )

    def database_unavailable(exc: psycopg.Error) -> HTTPException:
        return HTTPException(
            status_code=503,
            detail="receiver database is temporarily unavailable",
            headers={"Retry-After": "1"},
        )

    @app.middleware("http")
    async def protect_api_ingress(request: Request, call_next):
        request.state.ingress_started_at = datetime.now(timezone.utc)
        request.state.request_id = str(uuid.uuid4())
        is_api = request.url.path.startswith("/api/v1/")
        is_decision = (
            request.method == "POST" and request.url.path == "/api/v1/decisions"
        )
        entered = False
        if is_decision:
            entered = await decision_gate.try_enter()
            if not entered:
                return JSONResponse(
                    {"detail": "receiver intake is temporarily at capacity"},
                    status_code=503,
                    headers={"Retry-After": "1", "X-Request-ID": request.state.request_id},
                )
        try:
            if is_api:
                authorization = request.headers.get("authorization", "")
                credential_fingerprint = authorization_fingerprint(authorization)
                client_ip = request.client.host if request.client else "unknown"
                allowed, retry_after = await api_limiter.allow(credential_fingerprint)
                if allowed and is_decision:
                    allowed, retry_after = await decision_limiter.allow(
                        credential_fingerprint
                    )
                if allowed:
                    allowed, retry_after = await ip_limiter.allow(client_ip)
                if not allowed:
                    return JSONResponse(
                        {"detail": "request rate limit exceeded"},
                        status_code=429,
                        headers={
                            "Retry-After": str(retry_after),
                            "X-Request-ID": request.state.request_id,
                        },
                    )
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            if entered:
                await decision_gate.leave()

    async def resolve_team(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> str:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        try:
            team = await intake_store_call(store.team_for_api_key, credentials.credentials)
        except psycopg.Error as exc:
            raise database_unavailable(exc) from exc
        if not team:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        return team

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

    decision_schema = DecisionRequest.model_json_schema()
    decision_schema["properties"]["protocol_version"]["enum"] = sorted(
        supported_protocol_versions
    )
    decision_schema["properties"]["run_id"]["const"] = current_run_id

    @app.post(
        "/api/v1/decisions",
        tags=["participant"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": decision_schema}
                },
            }
        },
    )
    async def submit_decision(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        request_id = request.state.request_id
        media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type != "application/json":
            raise HTTPException(status_code=415, detail="Content-Type must be application/json")
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_request_bytes:
            raise HTTPException(status_code=413, detail="request body too large")
        try:
            body = await read_bounded_body(
                request,
                max_bytes=max_request_bytes,
                timeout_seconds=body_read_timeout_seconds,
            )
        except RequestBodyTooLarge as exc:
            raise HTTPException(status_code=413, detail="request body too large") from exc
        except RequestBodyTimedOut as exc:
            raise HTTPException(
                status_code=408, detail="request body read timed out"
            ) from exc
        except ClientDisconnect as exc:
            raise HTTPException(status_code=400, detail="client disconnected") from exc

        # This is the authoritative competition receipt time. It is deliberately
        # recorded before authentication/DB queueing, after the complete bounded
        # body is in the trusted server process.
        received_at = datetime.now(timezone.utc)
        team = await resolve_team(credentials)
        raw_hash = hashlib.sha256(body).hexdigest()
        document: dict[str, Any] | None = None
        try:
            candidate = json.loads(body, parse_constant=reject_json_constant)
            validate_json_tree(candidate)
            if not isinstance(candidate, dict):
                raise ValueError("request body must be a JSON object")
            document = candidate
            parsed = DecisionRequest.model_validate(candidate)
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValidationError,
            InvalidJsonValue,
            RecursionError,
            TypeError,
            ValueError,
        ) as exc:
            try:
                await intake_store_call(
                    store.record_invalid_attempt,
                    team,
                    request_id,
                    idempotency_key if idempotency_key and len(idempotency_key) <= 200 else None,
                    received_at,
                    document,
                    raw_hash,
                    "invalid_decision_envelope",
                )
            except psycopg.Error as db_exc:
                raise database_unavailable(db_exc) from db_exc
            detail = (
                exc.errors(include_url=False, include_input=False)
                if isinstance(exc, ValidationError)
                else "invalid JSON object"
            )
            raise HTTPException(status_code=422, detail=detail) from exc
        if not idempotency_key or len(idempotency_key) > 200:
            try:
                await intake_store_call(
                    store.record_invalid_attempt,
                    team,
                    request_id,
                    None,
                    received_at,
                    document,
                    raw_hash,
                    "invalid_idempotency_key",
                )
            except psycopg.Error as exc:
                raise database_unavailable(exc) from exc
            raise HTTPException(status_code=400,
                                detail="a valid Idempotency-Key header is required")

        contract_reason = None
        if parsed.protocol_version not in supported_protocol_versions:
            contract_reason = "unsupported_protocol_version"
        elif parsed.run_id != current_run_id:
            contract_reason = "run_id_mismatch"
        if contract_reason:
            try:
                await intake_store_call(
                    store.record_invalid_attempt,
                    team,
                    request_id,
                    idempotency_key,
                    received_at,
                    document,
                    raw_hash,
                    contract_reason,
                )
            except psycopg.Error as exc:
                raise database_unavailable(exc) from exc
            raise HTTPException(status_code=422, detail=contract_reason)

        # Validation is structural only. Persist the participant's parsed JSON
        # exactly as received instead of Pydantic's coerced representation.
        assert document is not None
        try:
            result = await intake_store_call(
                store.submit,
                team,
                document,
                idempotency_key,
                received_at,
                request_id,
            )
        except psycopg.Error as exc:
            raise database_unavailable(exc) from exc
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
    def create_team(document: TeamRequest) -> JSONResponse:
        try:
            api_key = store.register_team(document.team_code, document.display_name)
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise HTTPException(status_code=409, detail="team already exists") from exc
            raise
        return JSONResponse(
            {"team_code": document.team_code, "api_key": api_key},
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/api/v1/admin/teams/{team_code}/api-keys/rotate",
        dependencies=[Depends(require_admin)],
        tags=["organizer"],
    )
    def rotate_team_api_key(
        team_code: str, document: ApiKeyRotationRequest | None = None
    ) -> JSONResponse:
        try:
            result = store.rotate_team_api_key(
                team_code, grace_seconds=(document.grace_seconds if document else 0)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="team not found") from exc
        except psycopg.Error as exc:
            raise database_unavailable(exc) from exc
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post(
        "/api/v1/admin/teams/{team_code}/api-keys/{credential_id}/revoke",
        dependencies=[Depends(require_admin)],
        tags=["organizer"],
    )
    def revoke_team_api_key(team_code: str, credential_id: int) -> dict[str, Any]:
        try:
            return store.revoke_team_api_key(team_code, credential_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="credential not found") from exc
        except psycopg.Error as exc:
            raise database_unavailable(exc) from exc

    @app.post("/api/v1/admin/trading-days", tags=["organizer"],
              dependencies=[Depends(require_admin)])
    def create_trading_day(document: TradingDayRequest):
        try:
            row, idempotent = store.create_trading_day(**document.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except psycopg.Error as exc:
            raise database_unavailable(exc) from exc
        return fastapi_json({
            "trading_date": row["trading_date"].isoformat(),
            "id": row["id"],
            "idempotent": idempotent,
        }, 200 if idempotent else 201)

    return app


def secrets_compare(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)


def reject_json_constant(value: str):
    raise InvalidJsonValue(f"non-standard JSON constant {value} is not allowed")


def authorization_fingerprint(header: str) -> str:
    parts = header.strip().split(None, 1)
    material = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else header
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def fastapi_json(content: Any, status_code: int):
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
    store = CompetitionStore(
        args.database_url,
        connect_timeout_seconds=int(os.environ.get("COMPETITION_DB_CONNECT_TIMEOUT_SECONDS", "3")),
        statement_timeout_ms=int(os.environ.get("COMPETITION_DB_STATEMENT_TIMEOUT_MS", "5000")),
        lock_timeout_ms=int(os.environ.get("COMPETITION_DB_LOCK_TIMEOUT_MS", "2000")),
    )
    supported_versions = frozenset(
        item.strip()
        for item in os.environ.get("COMPETITION_SUPPORTED_PROTOCOL_VERSIONS", "0.1").split(",")
        if item.strip()
    )
    app = create_app(
        store,
        admin_token,
        max_bytes,
        current_run_id=os.environ.get("COMPETITION_RUN_ID", "official_2026"),
        supported_protocol_versions=supported_versions,
        body_read_timeout_seconds=float(
            os.environ.get("COMPETITION_BODY_READ_TIMEOUT_SECONDS", "10")
        ),
        max_inflight_decisions=int(
            os.environ.get("COMPETITION_MAX_INFLIGHT_DECISIONS", "256")
        ),
        intake_db_workers=int(os.environ.get("COMPETITION_INTAKE_DB_WORKERS", "24")),
        api_requests_per_minute=int(
            os.environ.get("COMPETITION_API_REQUESTS_PER_MINUTE", "120")
        ),
        decision_requests_per_minute=int(
            os.environ.get("COMPETITION_DECISION_REQUESTS_PER_MINUTE", "30")
        ),
        ip_requests_per_minute=int(
            os.environ.get("COMPETITION_IP_REQUESTS_PER_MINUTE", "600")
        ),
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
