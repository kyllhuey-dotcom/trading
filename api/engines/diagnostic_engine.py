from typing import Dict, Any, List, Optional
from datetime import datetime

class DiagnosticEngine:
    """
    Engine responsible for diagnosing why a trade was NOT taken (Rule 1).
    Instruments the decision process and returns a detailed report.
    """
    
    def diagnose(self, 
                 symbol: str,
                 data_valid: bool,
                 day_allowed: bool,
                 session_allowed: bool,
                 news_clear: bool,
                 market_open: bool,
                 not_range: bool,
                 trend_valid: bool,
                 structure_valid: bool,
                 signal_valid: bool,
                 spread_valid: bool,
                 liquidity_valid: bool,
                 risk_valid: bool,
                 leverage_valid: bool,
                 broker_valid: bool,
                 system_armed: bool,
                 reasons: Dict[str, str]) -> Dict[str, Any]:
        
        checks = {
            "DATA_VALID": "PASS" if data_valid else "FAIL",
            "DAY_ALLOWED": "PASS" if day_allowed else "FAIL",
            "SESSION_ALLOWED": "PASS" if session_allowed else "FAIL",
            "NEWS_CLEAR": "PASS" if news_clear else "FAIL",
            "MARKET_OPEN": "PASS" if market_open else "FAIL",
            "NOT_RANGE": "PASS" if not_range else "FAIL",
            "TREND_VALID": "PASS" if trend_valid else "FAIL",
            "STRUCTURE_VALID": "PASS" if structure_valid else "FAIL",
            "SIGNAL_VALID": "PASS" if signal_valid else "FAIL",
            "SPREAD_VALID": "PASS" if spread_valid else "FAIL",
            "LIQUIDITY_VALID": "PASS" if liquidity_valid else "FAIL",
            "RISK_VALID": "PASS" if risk_valid else "FAIL",
            "LEVERAGE_VALID": "PASS" if leverage_valid else "FAIL",
            "BROKER_VALID": "PASS" if broker_valid else "FAIL",
            "SYSTEM_ARMED": "PASS" if system_armed else "FAIL",
        }
        
        # Find the main blocker (the first FAIL in the priority order)
        priority_order = [
            "SYSTEM_ARMED", "DATA_VALID", "BROKER_VALID", "MARKET_OPEN",
            "DAY_ALLOWED", "SESSION_ALLOWED", "NEWS_CLEAR", "RISK_VALID",
            "LEVERAGE_VALID", "NOT_RANGE", "TREND_VALID", "STRUCTURE_VALID",
            "SPREAD_VALID", "LIQUIDITY_VALID", "SIGNAL_VALID"
        ]
        
        main_blocker = "NONE"
        secondary_blockers = []
        
        for check in priority_order:
            if checks.get(check) == "FAIL":
                if main_blocker == "NONE":
                    main_blocker = check
                else:
                    secondary_blockers.append(check)
        
        return {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "checks": checks,
            "main_blocker": main_blocker,
            "main_reason": reasons.get(main_blocker, "No specific reason provided"),
            "secondary_blockers": secondary_blockers
        }
