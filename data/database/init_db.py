#!/usr/bin/env python3
"""Apply and validate the PostgreSQL live-competition schema."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "schema.sql"
DEFAULT_URL = "postgresql://competition:competition@127.0.0.1:5432/competition"


def initialize(database_url: str) -> None:
    schema = SCHEMA.read_text(encoding="utf-8")
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(schema)
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
            table_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.views WHERE table_schema='public'"
            )
            view_count = cursor.fetchone()[0]
    print("PostgreSQL database ready")
    print(f"Schema objects: {table_count} tables, {view_count} views")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("COMPETITION_DATABASE_URL", DEFAULT_URL),
    )
    args = parser.parse_args()
    initialize(args.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
