"""Hot-reloadable settings schema (LOT 4)."""
from typing import Any, Dict, List, Tuple

SETTINGS_SPEC: Dict[str, Dict[str, Any]] = {
    "max_risk_pct": {"type": "float", "min": 0.1, "max": 10.0, "default": "1.0"},
    "max_leverage": {"type": "float", "min": 1, "max": 100, "default": "20"},
    "min_account_balance": {"type": "float", "min": 0.5, "max": 10000.0, "default": "1.0"},
    "min_trade_notional": {"type": "float", "min": 0.5, "max": 10000.0, "default": "1.0"},
    "max_daily_loss_pct": {"type": "float", "min": 0.5, "max": 20.0, "default": "3.0"},
    "cool_down_mins": {"type": "int", "min": 0, "max": 240, "default": "30"},
    "max_open_positions": {"type": "int", "min": 1, "max": 20, "default": "10"},
    "min_signal_score": {"type": "int", "min": 84, "max": 99, "default": "84"},
    "risk_reward_ratio": {"type": "float", "min": 0.5, "max": 10.0, "default": "2.0"},
    "atr_stop_multiplier": {"type": "float", "min": 0.1, "max": 10.0, "default": "1.5"},
    "max_spread_pct": {"type": "float", "min": 0.01, "max": 5.0, "default": "0.5"},
    "trailing_stop_active": {"type": "bool", "default": "true"},
    "trailing_stop_distance_atr": {"type": "float", "min": 0.1, "max": 10.0, "default": "1.5"},
    "emergency_stop_drawdown_pct": {"type": "float", "min": 1.0, "max": 50.0, "default": "10.0"},
    "auto_arm_on_startup": {"type": "bool", "default": "false"},
    "auto_start_on_startup": {"type": "bool", "default": "false"},
    "news_unavailable_policy": {
        "type": "enum",
        "choices": ("block_all", "block_tradfi_only", "allow_all"),
        "default": "block_all",
    },
    "active_strategies": {"type": "str", "default": "structure,arbitrage,tape,liquidity"},
    "scan_interval_seconds": {"type": "int", "min": 5, "max": 300, "default": "30"},
    "sim_latency_ms": {"type": "int", "min": 0, "max": 5000, "default": "100"},
    "sim_slippage_pct": {"type": "float", "min": 0.0, "max": 5.0, "default": "0.05"},
    "sim_rejection_prob": {"type": "float", "min": 0.0, "max": 1.0, "default": "0.01"},
    "partial_tp_ratio": {"type": "float", "min": 0.1, "max": 5.0, "default": "1.0"},
    "alpha_override_enabled": {"type": "bool", "default": "false"},
    "max_cost_ratio": {"type": "float", "min": 0.0, "max": 2.0, "default": "0.5"},
    "capital_profile_mode": {"type": "enum", "choices": ("manual", "auto"), "default": "manual"},
    "regime_adaptation_enabled": {"type": "bool", "default": "true"},
    "market_tuning": {"type": "str", "default": "{}"},
    "max_consecutive_losses": {"type": "int", "min": 1, "max": 20, "default": "3"},
    "max_trade_duration_minutes": {"type": "int", "min": 0, "max": 1440, "default": "0"},
    "fee_pct": {"type": "float", "min": 0.0, "max": 2.0, "default": "0.05"},
    "allow_delayed_data_trading": {"type": "bool", "default": "false"},
    "language": {"type": "enum", "choices": ("en", "fr", "es", "de"), "default": "en"},
    "timezone": {"type": "str", "default": "UTC"},
    "max_new_positions_per_scan": {"type": "int", "min": 1, "max": 3, "default": "3"},
    "opportunity_ttl_s": {"type": "float", "min": 5.0, "max": 120.0, "default": "30.0"},
}


def _as_bool_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return "true"
    if s in ("0", "false", "no", "off"):
        return "false"
    return "false"


def validate_settings(raw: Dict[str, Any]) -> Tuple[Dict[str, str], List[str]]:
    cleaned: Dict[str, str] = {}
    errors: List[str] = []
    for key, value in (raw or {}).items():
        spec = SETTINGS_SPEC.get(key)
        if spec is None:
            cleaned[key] = str(value)
            continue
        typ = spec["type"]
        try:
            if typ == "bool":
                cleaned[key] = _as_bool_str(value)
                continue
            if typ == "enum":
                s = str(value).strip().lower()
                if s not in spec["choices"]:
                    cleaned[key] = spec["default"]
                    errors.append(f"{key}: invalid choice, defaulted to {spec['default']}")
                else:
                    cleaned[key] = s
                continue
            if typ == "str":
                cleaned[key] = str(value)
                continue
            num = float(value)
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None and num < lo:
                num = lo
                errors.append(f"{key}: clamped to min {lo}")
            if hi is not None and num > hi:
                num = hi
                errors.append(f"{key}: clamped to max {hi}")
            if typ == "int":
                cleaned[key] = str(int(num))
            else:
                cleaned[key] = str(num)
        except (TypeError, ValueError):
            cleaned[key] = spec["default"]
            errors.append(f"{key}: invalid, using default {spec['default']}")
    return cleaned, errors


def ensure_defaults(existing: Dict[str, str]) -> Dict[str, str]:
    """Fill missing keys only — never overwrite a user value."""
    out = dict(existing or {})
    for key, spec in SETTINGS_SPEC.items():
        if key not in out or out[key] in (None, ""):
            out[key] = spec["default"]
    return out
