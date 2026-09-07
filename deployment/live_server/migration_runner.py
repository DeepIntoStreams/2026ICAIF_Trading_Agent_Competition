"""Apply Deployment-owned PostgreSQL migrations exactly once."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import psycopg


MIGRATIONS = Path(__file__).with_name("migrations")
LOCK_NAME = "icaif2026_deployment_schema_migrations"


def apply_migrations(database_url: str) -> list[str]:
    """Apply pending SQL files transactionally and verify recorded checksums."""
    applied_now: list[str] = []
    with psycopg.connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_NAME,))
        connection.execute(
            """CREATE TABLE IF NOT EXISTS deployment_schema_migrations (
                   version TEXT PRIMARY KEY,
                   sha256 TEXT NOT NULL,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        for path in sorted(MIGRATIONS.glob("*.sql")):
            version = path.stem
            sql = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            row = connection.execute(
                "SELECT sha256 FROM deployment_schema_migrations WHERE version=%s",
                (version,),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise RuntimeError(
                        f"applied migration {version} has checksum {row[0]}, expected {digest}"
                    )
                continue
            connection.execute(sql)
            connection.execute(
                "INSERT INTO deployment_schema_migrations(version, sha256) VALUES (%s,%s)",
                (version, digest),
            )
            applied_now.append(version)
    return applied_now


def migration_status(connection: Any) -> dict[str, Any]:
    """Return the production readiness state for Deployment migrations."""
    table_exists = connection.execute(
        "SELECT to_regclass('public.deployment_schema_migrations') IS NOT NULL"
    ).fetchone()[0]
    applied = set()
    if table_exists:
        applied = {
            str(row[0])
            for row in connection.execute(
                "SELECT version FROM deployment_schema_migrations"
            ).fetchall()
        }
    expected = {path.stem for path in MIGRATIONS.glob("*.sql")}
    return {
        "ready": expected <= applied,
        "expected": sorted(expected),
        "applied": sorted(applied),
        "pending": sorted(expected - applied),
    }
