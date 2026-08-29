#!/usr/bin/env python3
"""v3.3 — SQLite backup / verify / restore (offline, copy-based).

Usage:
    python3 scripts/backup_db.py backup  --db data/quantum_trade.db [--out data/backups]
    python3 scripts/backup_db.py verify  --file data/backups/quantum_trade_....db
    python3 scripts/backup_db.py restore --file backup.db --to /tmp/restore/quantum_trade.db

Rules:
- backup: WAL checkpoint(TRUNCATE) + atomic copy + sha256 sidecar;
- verify: the copy must open, hold the expected tables and return one row;
- restore: NEVER overwrites a live DB in place — the copy is written to an
  explicit --to destination (idempotent on a copy).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import time

EXPECTED_TABLES = {
    "accounts", "trades", "settings", "broker_configs", "web3_wallets",
    "audit_logs", "signals_archive", "economic_calendar_cache",
    "scanner_cache", "last_quotes", "last_ohlcv", "order_intents",
}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_backup(db_path: str, out_dir: str) -> str:
    if not os.path.exists(db_path):
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        raise SystemExit(1)
    os.makedirs(out_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        # Force a WAL checkpoint so the main file is self-contained.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(out_dir, f"quantum_trade_{stamp}.db")
        tmp = dest + ".tmp"
        shutil.copyfile(db_path, tmp)
        os.replace(tmp, dest)  # atomic on the same filesystem
    finally:
        conn.close()
    digest = _sha256(dest)
    with open(dest + ".sha256", "w", encoding="utf-8") as fh:
        fh.write(digest + "  " + os.path.basename(dest) + "\n")
    print(f"backup ok: {dest} ({os.path.getsize(dest)} bytes)")
    return dest


def cmd_verify(file_path: str) -> bool:
    if not os.path.exists(file_path):
        print(f"ERROR: file not found: {file_path}", file=sys.stderr)
        return False
    sha_file = file_path + ".sha256"
    if os.path.exists(sha_file):
        expected = open(sha_file, encoding="utf-8").read().split()[0]
        actual = _sha256(file_path)
        if expected != actual:
            print("ERROR: sha256 mismatch — backup corrupted", file=sys.stderr)
            return False
        print("sha256 ok")
    conn = sqlite3.connect(file_path)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = EXPECTED_TABLES - tables
        if missing:
            print(f"ERROR: missing tables: {sorted(missing)}", file=sys.stderr)
            return False
        for row in conn.execute("SELECT COUNT(*) FROM trades"):
            trades = int(row[0])
            break
        else:
            trades = -1
        print(f"schema ok ({len(tables)} tables, {trades} trade rows)")
        return True
    finally:
        conn.close()


def cmd_restore(file_path: str, to_path: str) -> bool:
    """Restore ON A COPY: never touches the live database."""
    if not cmd_verify(file_path):
        return False
    parent = os.path.dirname(os.path.abspath(to_path))
    os.makedirs(parent, exist_ok=True)
    if os.path.abspath(file_path) == os.path.abspath(to_path):
        print("ERROR: refuse to restore onto the source file", file=sys.stderr)
        return False
    if os.path.exists(to_path):
        print("ERROR: destination exists — refusing to overwrite "
              f"{to_path} (restore on a fresh copy)", file=sys.stderr)
        return False
    tmp = to_path + ".tmp"
    shutil.copyfile(file_path, tmp)
    os.replace(tmp, to_path)
    print(f"restored copy: {to_path}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_backup = sub.add_parser("backup", help="WAL checkpoint + atomic copy")
    p_backup.add_argument("--db", default="data/quantum_trade.db")
    p_backup.add_argument("--out", default="data/backups")

    p_verify = sub.add_parser("verify", help="verify a backup copy")
    p_verify.add_argument("--file", required=True)

    p_restore = sub.add_parser("restore", help="restore onto a fresh copy")
    p_restore.add_argument("--file", required=True)
    p_restore.add_argument("--to", required=True)

    args = parser.parse_args()
    if args.cmd == "backup":
        cmd_backup(args.db, args.out)
    elif args.cmd == "verify":
        raise SystemExit(0 if cmd_verify(args.file) else 1)
    elif args.cmd == "restore":
        raise SystemExit(0 if cmd_restore(args.file, args.to) else 1)


if __name__ == "__main__":
    main()
