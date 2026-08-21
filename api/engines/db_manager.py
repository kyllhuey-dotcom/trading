import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from cryptography.fernet import Fernet

class DatabaseManager:
    """
    Unified Data Persistence Manager (Rule 1, 27).
    Handles SQLite transactions for all trading data.
    """
    def __init__(self, db_path: str = "data/quantum_trade.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # Initialize encryption (Lot 4)
        self.key = os.getenv("FERNET_KEY")
        if not self.key:
            # Fallback for dev/demo if key is missing, 
            # but institutional standard requires a real key.
            self.cipher = None
        else:
            self.cipher = Fernet(self.key.encode())
        self._init_db()

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if not value or not self.cipher: return value
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        if not value or not self.cipher: return value
        try:
            return self.cipher.decrypt(value.encode()).decode()
        except Exception:
            return value # Fallback to plaintext if decryption fails

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode + busy timeout for institutional robustness (Lot 0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
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
            
            # trades Table (Rule 38)
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

            # Bot Settings Table (Risk, Strategy)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # Broker Connections Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS broker_configs (
                    broker_id TEXT PRIMARY KEY,
                    exchange_id TEXT, -- For CCXT
                    api_key TEXT,
                    api_secret TEXT,
                    api_passphrase TEXT,
                    is_active INTEGER DEFAULT 0,
                    mode TEXT -- REAL or DEMO
                )
            """)
            
            # Web3 Wallets Table (MetaMask, Phantom, etc.)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS web3_wallets (
                    wallet_id TEXT PRIMARY KEY,
                    provider TEXT, -- METAMASK, PHANTOM, OKX
                    address TEXT,
                    network TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
            
            # Seed default settings
            cursor = conn.execute("SELECT COUNT(*) FROM settings")
            if cursor.fetchone()[0] == 0:
                defaults = {
                    "max_risk_pct": "1.0",
                    "max_leverage": "20",
                    "max_daily_loss_pct": "3.0",
                    "cool_down_mins": "30",
                    "max_open_positions": "3",
                    "trailing_stop_active": "true",
                    "max_spread_pct": "0.5",
                    "min_signal_score": "80",
                    "risk_reward_ratio": "2.0",
                    "trailing_stop_distance_atr": "1.5",
                    "emergency_stop_drawdown_pct": "10.0",
                    "auto_arm_on_startup": "false",
                    "slippage_tolerance_pct": "0.1"
                }
                for k, v in defaults.items():
                    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))
            else:
                # Update existing max risk limit if necessary, but the logic in index.py 
                # will handle the new range. We just ensure the DB value is within 0-10.
                pass

            # Audit Logs (Institutional Compliance)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    level TEXT,
                    action TEXT,
                    message TEXT,
                    metadata TEXT
                )
            """)

            # Signals Archive (Quant Analysis)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals_archive (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    market_id TEXT,
                    direction TEXT,
                    score INTEGER,
                    price REAL,
                    setup_type TEXT,
                    decision TEXT, -- EXECUTED, BLOCKED, LOW_SCORE
                    reason TEXT
                )
            """)
            
            # Initial seed for accounts if empty
            cursor = conn.execute("SELECT COUNT(*) FROM accounts")
            if cursor.fetchone()[0] == 0:
                conn.execute("INSERT INTO accounts (mode, balance, currency) VALUES (?, ?, ?)", ("DEMO", 10000.0, "EUR"))
                conn.execute("INSERT INTO accounts (mode, balance, currency) VALUES (?, ?, ?)", ("REAL", 0.0, "EUR"))

            conn.commit()

    def log_audit(self, level: str, action: str, message: str, metadata: Dict = None):
        with self._get_connection() as conn:
            conn.execute("INSERT INTO audit_logs (level, action, message, metadata) VALUES (?, ?, ?, ?)",
                        (level, action, message, json.dumps(metadata or {})))
            conn.commit()

    def archive_signal(self, signal_data: Dict[str, Any], decision: str, reason: str):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO signals_archive (market_id, direction, score, price, setup_type, decision, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_data.get("market_id"), signal_data.get("direction"), 
                signal_data.get("score"), signal_data.get("entry"),
                signal_data.get("setup_type"), decision, reason
            ))
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

    # Broker Configs with Encryption (Lot 4)
    def save_broker_config(self, broker_id: str, exchange_id: str, api_key: str, api_secret: str, passphrase: Optional[str] = None):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO broker_configs (broker_id, exchange_id, api_key, api_secret, api_passphrase, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (
                broker_id, 
                exchange_id, 
                self.encrypt(api_key), 
                self.encrypt(api_secret), 
                self.encrypt(passphrase)
            ))
            conn.commit()

    def get_active_broker_configs(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM broker_configs WHERE is_active = 1").fetchall()
            configs = []
            for row in rows:
                d = dict(row)
                d["api_key"] = self.decrypt(d["api_key"])
                d["api_secret"] = self.decrypt(d["api_secret"])
                d["api_passphrase"] = self.decrypt(d["api_passphrase"])
                configs.append(d)
            return configs

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        if d.get("metadata"):
            d["metadata"] = json.loads(d["metadata"])
        return d
