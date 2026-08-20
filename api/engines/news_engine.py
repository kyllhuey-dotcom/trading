import httpx
import asyncio
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Any, Optional

class EconomicCalendarProvider:
    def __init__(self):
        self.url = "https://nfs.forexfactory.com/ff_calendar_thisweek.json"
        self.cache: List[Dict[str, Any]] = []
        self.last_update: Optional[datetime] = None

    async def fetch_events(self) -> List[Dict[str, Any]]:
        # Cache for 1 hour
        if self.last_update and (datetime.now() - self.last_update).total_seconds() < 3600:
            return self.cache
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.url, timeout=10.0)
                if response.status_code == 200:
                    self.cache = response.json()
                    self.last_update = datetime.now()
                    return self.cache
        except Exception as e:
            print(f"Calendar Provider Error: {e}")
        
        return self.cache # Return old cache if failure

class NewsFilter:
    def __init__(self, safety_before_mins: int = 30, safety_after_mins: int = 60):
        self.safety_before = safety_before_mins
        self.safety_after = safety_after_mins

    def filter_high_impact(self, events: List[Dict[str, Any]], asset_currency: Optional[str] = None) -> List[Dict[str, Any]]:
        # FF format impact levels: "High", "Medium", "Low"
        high_impact = [e for e in events if e.get('impact') == 'High']
        
        # If asset_currency is provided (e.g., "USD"), filter for relevant news
        if asset_currency:
            # We also include events that impact the global market like USD news for crypto
            high_impact = [e for e in high_impact if e.get('country') in [asset_currency, 'USD', 'EUR']]
            
        return high_impact

class EventRiskEngine:
    def __init__(self, news_filter: NewsFilter):
        self.filter = news_filter

    def check_risk(self, high_impact_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        now = datetime.now(pytz.UTC)
        blocking_event = None
        
        for event in high_impact_events:
            try:
                # Parsing Forexfactory date/time
                event_time_str = f"{event['date']} {event['time']}"
                # FF time is usually EST/EDT
                ny_tz = pytz.timezone('America/New_York')
                event_time = datetime.strptime(event_time_str, "%b %d, %Y %I:%M%p")
                event_time = ny_tz.localize(event_time).astimezone(pytz.UTC)
                
                diff_mins = (event_time - now).total_seconds() / 60
                
                if -self.filter.safety_after < diff_mins < self.filter.safety_before:
                    blocking_event = event
                    blocking_event['time_utc'] = event_time
                    break
            except Exception:
                continue
                
        return {
            "is_blocked": blocking_event is not None,
            "blocking_event": blocking_event,
            "next_events": high_impact_events[:5] # Simplified for return
        }

class SessionFilter:
    def __init__(self, timezone: str = 'Europe/Paris'):
        self.tz = pytz.timezone(timezone)
        # Règle modifiée : Autorisé TOUS LES JOURS si le marché est ouvert
        self.allowed_days = [0, 1, 2, 3, 4, 5, 6] 

    def is_trading_allowed(self, asset_class: str = "CRYPTO") -> Dict[str, Any]:
        now = datetime.now(self.tz)
        
        day_of_week = now.weekday()
        day_ok = day_of_week in self.allowed_days
        
        # Heures de marché par classe d'actif (Rule 16)
        hour = now.hour
        minute = now.minute
        current_time_float = hour + minute / 60.0
        
        session_ok = False
        
        if asset_class == "CRYPTO":
            session_ok = True # 24/7
        elif asset_class == "FOREX":
            # Dimanche 23h - Vendredi 22h Paris
            if day_of_week == 6: # Sunday
                session_ok = hour >= 23
            elif day_of_week == 4: # Friday
                session_ok = hour < 22
            elif 0 <= day_of_week <= 3: # Mon-Thu
                session_ok = True
        else: # Indices & Commodities (Standard sessions)
            # Simplification : 9h00 - 22h00 pour le trading algo sécurisé
            session_ok = (9.0 <= current_time_float < 22.0) and (0 <= day_of_week <= 4)
        
        return {
            "day_ok": day_ok,
            "session_ok": session_ok,
            "current_time": now.strftime("%H:%M:%S"),
            "day_name": now.strftime("%A"),
            "asset_class": asset_class
        }

class NewsEngine:
    """
    Orchestrateur News + Session (Rule 15)
    """
    def __init__(self):
        self.provider = EconomicCalendarProvider()
        self.filter = NewsFilter()
        self.risk_engine = EventRiskEngine(self.filter)
        self.session_filter = SessionFilter()

    async def check_trading_allowed(self, asset_currency: Optional[str] = None, asset_class: str = "CRYPTO") -> Dict[str, Any]:
        session_status = self.session_filter.is_trading_allowed(asset_class=asset_class)
        
        all_events = await self.provider.fetch_events()
        if not all_events and session_status["day_ok"]:
             # If provider fails but it's a trading day, we must block per Rule 15
             return {
                "trading_allowed": False,
                "reason": "Calendar unavailable",
                **session_status
             }
             
        high_impact = self.filter.filter_high_impact(all_events, asset_currency)
        risk_status = self.risk_engine.check_risk(high_impact)
        
        trading_allowed = (
            session_status["day_ok"] and 
            session_status["session_ok"] and 
            not risk_status["is_blocked"]
        )
        
        return {
            "trading_allowed": trading_allowed,
            "day_ok": session_status["day_ok"],
            "news_ok": not risk_status["is_blocked"],
            "session_ok": session_status["session_ok"],
            "blocking_event": risk_status["blocking_event"],
            "next_events": [
                {
                    "title": e['title'],
                    "country": e['country'],
                    "time": e['time']
                } for e in high_impact if e.get('impact') == 'High'
            ][:3]
        }
