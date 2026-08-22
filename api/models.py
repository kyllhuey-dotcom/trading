from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class MarketQuote(BaseModel):
    market_id: str
    symbol: str
    display_symbol: str
    asset_class: str
    price: float
    change_24h: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread: Optional[float] = None
    volume: Optional[float] = None
    status: str # LIVE, DELAYED, STALE, ERROR
    source: str
    timestamp: int

class NewsEvent(BaseModel):
    time: Optional[str] = None
    currency: Optional[str] = None
    impact: Optional[str] = None
    title: Optional[str] = None
    event: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None

class NewsStatus(BaseModel):
    trading_allowed: bool
    day_ok: bool
    news_ok: bool
    session_ok: bool
    blocking_event: Optional[Dict[str, Any]] = None
    next_events: List[Dict[str, Any]] = Field(default_factory=list)
    status: Optional[str] = None
    source: Optional[str] = None
    timestamp: Optional[int] = None

class AnalysisResult(BaseModel):
    status: str
    trend: Optional[str] = None
    market_state: Optional[str] = None
    momentum: float = 0.0
    is_hh: bool = False
    is_hl: bool = False
    is_lh: bool = False
    is_ll: bool = False
    bos: bool = False
    choch: bool = False
    df_preview: Optional[List[Dict[str, Any]]] = None
    htf_bias: Optional[str] = None
    volatility: Optional[str] = None
    atr: Optional[float] = None
    indicators: Optional[Dict[str, Any]] = None

class SignalResult(BaseModel):
    status: str
    direction: Optional[str] = None
    score: int = 0
    reason: str
    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    market_id: Optional[str] = None
    display_symbol: Optional[str] = None
    setup_type: Optional[str] = None
    confidence: Optional[str] = None
    atr: Optional[float] = None
    risk_reward: Optional[float] = None

class DiagnosisReport(BaseModel):
    symbol: str
    main_blocker: str
    main_reason: str
    checks: Dict[str, str]
    secondary_blockers: List[str] = Field(default_factory=list)

class StatusResponse(BaseModel):
    status: str
    status_display: str
    is_running: bool
    mode: str
    armed: bool
    balance: float
    equity: float
    daily_pnl: float = 0.0
    drawdown: float = 0.0
    news: NewsStatus
    selected_market: str
    analysis: Optional[AnalysisResult] = None
    signal: Optional[SignalResult] = None
    diagnosis: Optional[DiagnosisReport] = None
    active_trade: Optional[Dict[str, Any]] = None
    active_trades: List[Dict[str, Any]] = Field(default_factory=list)
    history: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any]
    broker_info: Dict[str, Any]
    broker_connected: bool
    asset_info: Optional[Dict[str, Any]] = None
    best_setups: List[Dict[str, Any]] = Field(default_factory=list)
