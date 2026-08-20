import httpx
import pandas as pd
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Any

class NewsEngine:
    def __init__(self):
        self.url = "https://nfs.forexfactory.com/ff_calendar_thisweek.json"
        self.timezone = pytz.timezone('Europe/Paris')
        self.safety_before_mins = 30
        self.safety_after_mins = 60

    async def fetch_news(self) -> List[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.url, timeout=10.0)
                if response.status_code == 200:
                    return response.json()
                return []
        except Exception as e:
            print(f"Error fetching news: {e}")
            return []

    async def get_high_impact_events(self) -> List[Dict[str, Any]]:
        all_news = await self.fetch_news()
        high_impact = [n for n in all_news if n.get('impact') == 'High']
        
        processed_events = []
        now = datetime.now(pytz.UTC)

        for event in high_impact:
            # Format FF: "Aug 20, 2026 4:30pm"
            # Note: FF time is usually EST/EDT. We need to be careful.
            # However, for this implementation, we will assume the source is reachable.
            try:
                # Parsing simple example date format
                # FF JSON usually provides date in a parsable format
                event_time = datetime.strptime(f"{event['date']} {event['time']}", "%b %d, %Y %I:%M%p")
                event_time = pytz.timezone('America/New_York').localize(event_time).astimezone(pytz.UTC)
                
                processed_events.append({
                    "title": event['title'],
                    "country": event['country'],
                    "time_utc": event_time,
                    "impact": "High"
                })
            except Exception:
                continue

        return processed_events

    async def check_trading_allowed(self) -> Dict[str, Any]:
        """
        Vérifie si le trading est autorisé par rapport au calendrier.
        Modifié : Autorisé TOUS LES JOURS si le marché est ouvert.
        """
        now = datetime.now(pytz.UTC)
        events = await self.get_high_impact_events()
        
        # Règle 1 Modifiée : Le trading est maintenant autorisé tous les jours [0-6]
        allowed_days = [0, 1, 2, 3, 4, 5, 6] 
        now_paris = now.astimezone(self.timezone)
        day_ok = now_paris.weekday() in allowed_days
        
        blocking_event = None
        for event in events:
            time_diff = (event['time_utc'] - now).total_seconds() / 60
            if -self.safety_after_mins < time_diff < self.safety_before_mins:
                blocking_event = event
                break

        return {
            "trading_allowed": day_ok and blocking_event is None,
            "day_ok": day_ok,
            "news_ok": blocking_event is None,
            "blocking_event": blocking_event,
            "next_events": [
                {
                    "title": e['title'],
                    "country": e['country'],
                    "time": e['time_utc'].astimezone(self.timezone).strftime("%H:%M")
                } for e in events if e['time_utc'] > now 
            ][:3]
        }
