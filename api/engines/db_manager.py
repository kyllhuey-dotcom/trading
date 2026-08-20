import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

class DatabaseManager:
    """
    Unified Data Persistence Manager (Rule 1, 27).
    Handles SQLite transactions for all trading data.
    """
    def __init__(self, db_path: str = "data/quantum_trade.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            # Accounts Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    mode TEXT PRIMARY KEY,
                    balance REAL DEFAULT 0.0,
                    currency TEXT DEFAULT 'EUR'
                )
            """)
            
            # Initial seed for accounts if empty
            cursor = conn.execute("SELECT COUNT(*) FROM accounts")
            if cursor.fetchone()[0] == 0:
                conn.execute("INSERT INTO accounts (mode, balance, currency) VALUES (?, ?, ?)", ("DEMO", 10000.0, "EUR"))
                conn.execute("INSERT INTO accounts (mode, balance, currency) VALUES (?, ?, ?)", ("REAL", 0.0, "EUR"))

            # Trades Table (Rule 38)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    mode TEXT,
                    symbol TEXT,
                    display_symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    quantity REAL,
                    sl REAL,
                    tp REAL,
                    leverage REAL,
                    fees REAL,
                    pnl REAL,
                    open_time TEXT,
                    close_time TEXT,
                    status TEXT,
                    metadata TEXT
                )
            """)
            conn.commit()

    # Account Operations
    def get_balance(self, mode: str) -> float:
        with self._get_connection() as conn:
            row = conn.execute("SELECT balance FROM accounts WHERE mode = ?", (mode,)).fetchone()
            return row["balance"] if row else 0.0

    def update_balance(self, mode: str, pnl: float):
        with self._get_connection() as conn:
            conn.execute("UPDATE accounts SET balance = balance + ? WHERE mode = ?", (pnl, mode))
            conn.commit()

    def set_balance(self, mode: str, amount: float):
        with self._get_connection() as conn:
            conn.execute("UPDATE accounts SET balance = ? WHERE mode = ?", (amount, mode))
            conn.commit()

    # Trade Operations
    def save_trade(self, trade: Dict[str, Any]):
        with self._get_connection() as conn:
            metadata = json.dumps(trade.get("metadata", {}))
            conn.execute("""
                INSERT OR REPLACE INTO trades 
                (id, mode, symbol, display_symbol, direction, entry_price, exit_price, quantity, sl, tp, leverage, fees, pnl, open_time, close_time, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get("id"), trade.get("mode"), trade.get("symbol"), trade.get("display_symbol"),
                trade.get("direction"), trade.get("entry_price"), trade.get("exit_price"),
                trade.get("quantity"), trade.get("sl"), trade.get("tp"), trade.get("leverage"),
                trade.get("fees"), trade.get("pnl"), trade.get("open_time"), trade.get("close_time"),
                trade.get("status"), metadata
            ))
            conn.commit()

    def get_active_positions(self, mode: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if mode:
                cursor = conn.execute("SELECT * FROM trades WHERE status = 'OPEN' AND mode = ?", (mode,))
            else:
                cursor = conn.execute("SELECT * FROM trades WHERE status = 'OPEN'")
            
            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    def get_history(self, mode: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM trades WHERE status = 'CLOSED'"
            params = []
            if mode:
                query += " AND mode = ?"
                params.append(mode)
            query += " ORDER BY close_time DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    def delete_history(self, mode: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM trades WHERE mode = ? AND status = 'CLOSED'", (mode,))
            conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        if d.get("metadata"):
            d["metadata"] = json.loads(d["metadata"])
        return d
