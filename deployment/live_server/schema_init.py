"""Initialize the base schema, then apply Deployment-owned migrations."""

from __future__ import annotations

import os

import psycopg

from data.database.init_db import DEFAULT_URL, initialize

from .migration_runner import apply_migrations


def initialize_base_if_needed(database_url: str) -> bool:
    """Create the teammate-owned base schema only for an empty database."""
    with psycopg.connect(database_url) as connection:
        exists = connection.execute(
            "SELECT to_regclass('public.portfolio_snapshots') IS NOT NULL"
        ).fetchone()[0]
    if exists:
        return False
    initialize(database_url)
    return True


def main() -> int:
    database_url = os.environ.get("COMPETITION_DATABASE_URL", DEFAULT_URL)
    initialized = initialize_base_if_needed(database_url)
    applied = apply_migrations(database_url)
    print(f"Base schema initialized: {initialized}")
    print(f"Deployment migrations applied: {applied or 'none (already current)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
