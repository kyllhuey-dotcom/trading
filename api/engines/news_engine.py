import httpx
import asyncio
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Any, Optional

from bs4 import BeautifulSoup

class EconomicCalendarProvider:
    def __init__(self):
        # Rule 15: Using reliable public source (Scraping the official HTML)
        self.url = "https://www.forexfactory.com/calendar.php?week=this"
        self.cache: List[Dict[str, Any]] = []
        self.last_update: Optional[datetime] = None

    async def fetch_events(self) -> List[Dict[str, Any]]:
        # Cache for 2 hours
        if self.last_update and (datetime.now() - self.last_update).total_seconds() < 7200:
            return self.cache
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
            }
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(self.url, headers=headers, timeout=15.0)
                if response.status_code == 200:
                    events = self._parse_html(response.text)
                    if events:
                        self.cache = events
                        self.last_update = datetime.now()
                        return self.cache
        except Exception as e:
            print(f"Calendar Scraper Error: {e}")
        
        return self.cache

    def _parse_html(self, html_content: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_content, 'lxml')
        table = soup.find('table', class_='calendar__table')
        if not table:
            return []

        events = []
        current_date = ""
        
        rows = table.find_all('tr', class_='calendar__row')
        for row in rows:
            # Handle Date (Rule: carry over if empty)
            date_cell = row.find('td', class_='calendar__date')
            if date_cell:
                date_text = date_cell.text.strip()
                if date_text:
                    current_date = date_text.replace("\n", " ") # "Thu Aug 20"

            # Handle Event
            title_cell = row.find('td', class_='calendar__event')
            if not title_cell: continue
            
            currency = row.find('td', class_='calendar__currency')
            impact_div = row.find('td', class_='calendar__impact').find('span')
            time_cell = row.find('td', class_='calendar__time')
            forecast = row.find('td', class_='calendar__forecast')
            previous = row.find('td', class_='calendar__previous')
            actual = row.find('td', class_='calendar__actual')

            impact = "Low"
            if impact_div:
                classes = impact_div.get('class', [])
                if any('high' in c.lower() for c in classes): impact = "High"
                elif any('medium' in c.lower() for c in classes): impact = "Medium"

            events.append({
                "title": title_cell.text.strip(),
                "country": currency.text.strip() if currency else "",
                "date": current_date, 
                "time": time_cell.text.strip() if time_cell else "",
                "impact": impact,
                "forecast": forecast.text.strip() if forecast else "",
                "previous": previous.text.strip() if previous else "",
                "actual": actual.text.strip() if actual else ""
            })
            
        return events

class NewsFilter:
    def __init__(self, safety_before_mins: int = 30, safety_after_mins: int = 60):
        self.safety_before = safety_before_mins
        self.safety_after = safety_after_mins

    def filter_high_impact(self, events: List[Dict[str, Any]], asset_currency: Optional[str] = None) -> List[Dict[str, Any]]:
        # Mission: Filter ONLY for "High" impact news (Rule: majeure uniquement)
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
        current_year = now.year
        
        for event in high_impact_events:
            try:
                # Parsing Scraped date/time
                # Example: "Thu Aug 20" + "8:30am"
                # Need to handle events without time (All Day)
                if not event['time'] or 'All Day' in event['time']:
                    continue

                event_time_str = f"{event['date']} {current_year} {event['time']}"
                # Format: "Thu Aug 20 2026 8:30am"
                # Note: FF time is US Eastern
                ny_tz = pytz.timezone('America/New_York')
                event_time = datetime.strptime(event_time_str, "%a %b %d %Y %I:%M%p")
                event_time = ny_tz.localize(event_time).astimezone(pytz.UTC)
                
                diff_mins = (event_time - now).total_seconds() / 60
                
                if -self.filter.safety_after < diff_mins < self.filter.safety_before:
                    blocking_event = event
                    blocking_event['time_utc'] = event_time
                    break
            except Exception as e:
                # print(f"Risk parsing error: {e}")
                continue
                
        return {
            "is_blocked": blocking_event is not None,
            "blocking_event": blocking_event,
            "next_events": high_impact_events[:5] 
        }

class SessionFilter:
    def __init__(self, timezone: str = 'Europe/Paris'):
        self.tz = pytz.timezone(timezone)
        # MISSION: Authorized to trade ALL DAYS (0-6) whenever the market is open
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
            session_ok = True # 24/7 (Data available, but trade depends on day_ok)
        elif asset_class == "FOREX":
            # Monday 00:00 to Friday 22:00
            session_ok = (0 <= day_of_week <= 4)
            if day_of_week == 4 and hour >= 22: session_ok = False
        else:
            session_ok = (9.0 <= current_time_float < 22.0) and (0 <= day_of_week <= 4)
        
        return {
            "day_ok": day_ok,
            "session_ok": session_ok,
            "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "day_name": now.strftime("%A"),
            "asset_class": asset_class,
            "timezone": "Europe/Paris"
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
        
        # Default safety values (Rule 15 fail-safe)
        news_ok = True
        blocking_event = None
        next_events = []
        status = "LIVE"

        if not all_events:
            # Rule 1 & 18: No trade if calendar unavailable
            status = "DATA_UNAVAILABLE"
            if session_status["day_ok"]:
                return {
                    "trading_allowed": False,
                    "day_ok": session_status["day_ok"],
                    "news_ok": False,
                    "session_ok": session_status["session_ok"],
                    "blocking_event": {"title": "Calendar Unavailable"},
                    "next_events": [],
                    "status": "DATA_UNAVAILABLE",
                    "source": self.provider.url,
                    "timestamp": int(datetime.now().timestamp() * 1000)
                }
             
        high_impact = self.filter.filter_high_impact(all_events, asset_currency)
        risk_status = self.risk_engine.check_risk(high_impact)
        
        news_ok = not risk_status["is_blocked"]
        blocking_event = risk_status["blocking_event"]
        
        # Rule 9: Full normalization of events
        next_events = [
            {
                "time": e.get('time'),
                "currency": e.get('country'), 
                "country": e.get('country'),
                "impact": e.get('impact'),
                "event": e.get('title'),
                "forecast": e.get('forecast'),
                "previous": e.get('previous'),
                "actual": e.get('actual')
            } for e in high_impact
        ][:5]

        trading_allowed = (
            session_status["day_ok"] and 
            session_status["session_ok"] and 
            news_ok
        )
        
        return {
            "trading_allowed": trading_allowed,
            "day_ok": session_status["day_ok"],
            "news_ok": news_ok,
            "session_ok": session_status["session_ok"],
            "blocking_event": blocking_event,
            "next_events": next_events,
            "status": status,
            "source": self.provider.url,
            "timestamp": int(datetime.now().timestamp() * 1000)
        }
