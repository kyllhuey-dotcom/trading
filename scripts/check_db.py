#!/usr/bin/env python3
"""Inspect the SQLite schema used by Quantum Trade Pro."""
from pathlib import Path
import sqlite3
import sys
from typing import List


def list_tables(db_path: str) -> List[str]:
    """Return user and internal table names, sorted by SQLite."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    try:
        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Cannot inspect database {path}: {exc}") from exc
    return [str(row[0]) for row in rows]


def main(argv: List[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    db_path = args[0] if args else "data/quantum_trade.db"
    try:
        tables = list_tables(db_path)
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Tables in database:")
    for table in tables:
        print(f" - {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
