#!/usr/bin/env python3
"""Create and validate a local SQLite competition database."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "competition.sqlite3"
SCHEMA = HERE / "schema.sql"


def initialize(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        if foreign_key_errors:
            raise RuntimeError(f"SQLite foreign-key check failed: {foreign_key_errors}")
        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        view_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'"
        ).fetchone()[0]
    print(f"Database ready: {db_path.resolve()}")
    print(f"Schema objects: {table_count} tables, {view_count} views")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    initialize(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
