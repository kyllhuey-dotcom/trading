import sqlite3
import os
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Any, Optional, Iterator
from cryptography.fernet import Fernet

logger = logging.getLogger("DatabaseManager")

ENC_PREFIX = "enc:v1:"


class DatabaseManager:
    """
    Unified SQLite persistence layer.
    - Connections are always closed (context manager)
    - Broker API secrets are encrypted at rest when FERNET_KEY is set
    - Full CRUD: accounts, trades, settings, brokers, wallets, audit, signals
    """

    def __init__(self, db_path: Optional[str] = None):
        # An explicitly provided path always wins; otherwise use DB_PATH env or the default.
        self.db_path = db_path or os.getenv("DB_PATH", "data/quantum_trade.db")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self.key = os.getenv("FERNET_KEY")
        self.cipher: Optional[Fernet] = None
        if self.key:
            try:
                self.cipher = Fernet(self.key.encode())
            except Exception as e:
                logger.error(f"Invalid FERNET_KEY — encryption disabled: {e}")
                self.cipher = None
        if not self.cipher:
            logger.warning(
                "FERNET_KEY not set: broker API secrets will be stored UNENCRYPTED. "
                "Set FERNET_KEY in production.")
        self._init_db()

    # ------------------------------------------------------------------ #
    # Crypto                                                              #
    # ------------------------------------------------------------------ #
    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return value
        if value.startswith(ENC_PREFIX):
            return value  # already encrypted
        if self.cipher:
            return ENC_PREFIX + self.cipher.encrypt(value.encode()).decode()
        return value  # plaintext fallback (dev)

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return value
        if not value.startswith(ENC_PREFIX):
            return value  # stored plaintext (legacy/dev)
        if not self.cipher:
            logger.error("Encrypted secret found but FERNET_KEY is missing — cannot decrypt")
            return None
        try:
            return self.cipher.decrypt(value[len(ENC_PREFIX):].encode()).decode()
        except Exception as e:
            logger.error(f"Decryption failed (key changed?): {e}")
            return None

    # ------------------------------------------------------------------ #
    # Connection                                                          #
    # ------------------------------------------------------------------ #
    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """SQLite connection that is guaranteed to close (commit on success)."""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Schema                                                              #
    # ------------------------------------------------------------------ #
    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    mode TEXT PRIMARY KEY,
                    balance REAL DEFAULT 0.0,
                    currency TEXT DEFAULT 'EUR'
                )
            """)
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS broker_configs (
                    broker_id TEXT PRIMARY KEY,
                    exchange_id TEXT,
                    api_key TEXT,
                    api_secret TEXT,
                    api_passphrase TEXT,
                    is_active INTEGER DEFAULT 0,
                    mode TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS web3_wallets (
                    wallet_id TEXT PRIMARY KEY,
                    provider TEXT,
                    address TEXT,
                    network TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals_archive (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    market_id TEXT,
                    direction TEXT,
                    score INTEGER,
                    price REAL,
                    setup_type TEXT,
                    decision TEXT,
                    reason TEXT
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
                    "slippage_tolerance_pct": "0.1",
                    "active_strategies": "structure,arbitrage,tape,liquidity",
                    "sim_latency_ms": "100",
                    "sim_slippage_pct": "0.05",
                    "sim_rejection_prob": "0.01",
                    "partial_tp_ratio": "1.0",
                    "peak_balance": "0",
                    "scan_interval_seconds": "20",
                }
                for k, v in defaults.items():
                    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))

            # Seed default accounts
            cursor = conn.execute("SELECT COUNT(*) FROM accounts")
            if cursor.fetchone()[0] == 0:
                conn.execute("INSERT INTO accounts (mode, balance, currency) VALUES (?, ?, ?)",
                             ("DEMO", 10000.0, "EUR"))
                conn.execute("INSERT INTO accounts (mode, balance, currency) VALUES (?, ?, ?)",
                             ("REAL", 0.0, "EUR"))

    # ------------------------------------------------------------------ #
    # Settings                                                            #
    # ------------------------------------------------------------------ #
    def get_settings(self) -> Dict[str, str]:
        with self._get_connection() as conn:
            return {row["key"]: row["value"]
                    for row in conn.execute("SELECT * FROM settings").fetchall()}

    def set_setting(self, key: str, value: str) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    def save_settings(self, settings: Dict[str, str]) -> None:
        with self._get_connection() as conn:
            for k, v in settings.items():
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # ------------------------------------------------------------------ #
    # Audit & signals                                                     #
    # ------------------------------------------------------------------ #
    def log_audit(self, level: str, action: str, message: str, metadata: Optional[Dict] = None):
        with self._get_connection() as conn:
            conn.execute("INSERT INTO audit_logs (level, action, message, metadata) VALUES (?, ?, ?, ?)",
                         (level, action, message, json.dumps(metadata or {})))

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

    # ------------------------------------------------------------------ #
    # Accounts                                                            #
    # ------------------------------------------------------------------ #
    def get_balance(self, mode: str) -> float:
        with self._get_connection() as conn:
            row = conn.execute("SELECT balance FROM accounts WHERE mode = ?", (mode,)).fetchone()
            return row["balance"] if row else 0.0

    def update_balance(self, mode: str, pnl: float):
        with self._get_connection() as conn:
            conn.execute("UPDATE accounts SET balance = balance + ? WHERE mode = ?", (pnl, mode))

    def set_balance(self, mode: str, amount: float):
        with self._get_connection() as conn:
            conn.execute("UPDATE accounts SET balance = ? WHERE mode = ?", (amount, mode))

    # ------------------------------------------------------------------ #
    # Trades                                                              #
    # ------------------------------------------------------------------ #
    def save_trade(self, trade: Dict[str, Any]):
        metadata = json.dumps(trade.get("metadata", {}))
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades
                (id, mode, symbol, display_symbol, direction, entry_price, exit_price, quantity,
                 sl, tp, leverage, fees, pnl, open_time, close_time, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get("id"), trade.get("mode"), trade.get("symbol"), trade.get("display_symbol"),
                trade.get("direction"), trade.get("entry_price"), trade.get("exit_price"),
                trade.get("quantity"), trade.get("sl"), trade.get("tp"), trade.get("leverage"),
                trade.get("fees"), trade.get("pnl"), trade.get("open_time"), trade.get("close_time"),
                trade.get("status"), metadata
            ))

    def get_active_positions(self, mode: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if mode:
                cursor = conn.execute(
                    "SELECT * FROM trades WHERE status = 'OPEN' AND mode = ? ORDER BY open_time ASC", (mode,))
            else:
                cursor = conn.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY open_time ASC")
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_history(self, mode: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM trades WHERE status = 'CLOSED'"
            params: list = []
            if mode:
                query += " AND mode = ?"
                params.append(mode)
            query += " ORDER BY close_time DESC LIMIT ?"
            params.append(limit)
            cursor = conn.execute(query, tuple(params))
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_all_trades(self, mode: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        """All trades (open + closed) — used for statistics."""
        with self._get_connection() as conn:
            query = "SELECT * FROM trades"
            params: list = []
            if mode:
                query += " WHERE mode = ?"
                params.append(mode)
            query += " ORDER BY COALESCE(close_time, open_time) DESC LIMIT ?"
            params.append(limit)
            return [self._row_to_dict(row) for row in conn.execute(query, tuple(params)).fetchall()]

    def delete_history(self, mode: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM trades WHERE mode = ? AND status = 'CLOSED'", (mode,))

    def delete_all_trades(self, mode: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM trades WHERE mode = ?", (mode,))

    # ------------------------------------------------------------------ #
    # Brokers                                                             #
    # ------------------------------------------------------------------ #
    def save_broker_config(self, broker_id: str, exchange_id: str, api_key: str,
                           api_secret: str, passphrase: Optional[str] = None):
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM broker_configs WHERE broker_id = ?", (broker_id,)).fetchone()
            if existing:
                conn.execute("""
                    UPDATE broker_configs
                    SET exchange_id = ?, api_key = ?, api_secret = ?, api_passphrase = ?, is_active = 1
                    WHERE broker_id = ?
                """, (exchange_id, self.encrypt(api_key), self.encrypt(api_secret),
                      self.encrypt(passphrase), broker_id))
            else:
                conn.execute("""
                    INSERT INTO broker_configs (broker_id, exchange_id, api_key, api_secret, api_passphrase, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (broker_id, exchange_id, self.encrypt(api_key),
                      self.encrypt(api_secret), self.encrypt(passphrase)))

    def get_all_broker_configs(self) -> List[Dict[str, Any]]:
        """All broker configs with decrypted secrets (never expose via API)."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM broker_configs").fetchall()
        configs = []
        for row in rows:
            d = dict(row)
            d["api_key"] = self.decrypt(d["api_key"])
            d["api_secret"] = self.decrypt(d["api_secret"])
            d["api_passphrase"] = self.decrypt(d["api_passphrase"])
            configs.append(d)
        return configs

    def get_active_broker_configs(self) -> List[Dict[str, Any]]:
        return [c for c in self.get_all_broker_configs() if c.get("is_active") == 1]

    def get_broker_public_list(self) -> List[Dict[str, Any]]:
        """Broker list WITHOUT secrets — safe for the API."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT broker_id, exchange_id, is_active, mode FROM broker_configs").fetchall()
            return [dict(r) for r in rows]

    def set_broker_active(self, broker_id: str, is_active: bool) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE broker_configs SET is_active = ? WHERE broker_id = ?",
                (1 if is_active else 0, broker_id))
            return cur.rowcount > 0

    def delete_broker(self, broker_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM broker_configs WHERE broker_id = ?", (broker_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Web3 wallets                                                        #
    # ------------------------------------------------------------------ #
    def save_wallet(self, wallet_id: str, provider: str, address: str,
                    network: Optional[str] = None) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO web3_wallets (wallet_id, provider, address, network, is_active)
                VALUES (?, ?, ?, ?, 1)
            """, (wallet_id, provider, address, network or "mainnet"))

    def get_wallets(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM web3_wallets").fetchall()]

    def delete_wallet(self, wallet_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM web3_wallets WHERE wallet_id = ?", (wallet_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #
    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        if d.get("metadata"):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except json.JSONDecodeError:
                d["metadata"] = {}
        return d
