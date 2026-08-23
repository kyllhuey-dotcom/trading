"""Economic calendar, event-risk and market-session safeguards.

The calendar deliberately fails closed by default, but it is no longer tied to
one Cloudflare-protected HTML page.  Data is loaded from the public Fair
Economy JSON feed, then the ForexFactory HTML calendar, then a seven-day SQLite
snapshot.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import pytz
from bs4 import BeautifulSoup

logger = logging.getLogger("NewsEngine")


class EconomicCalendarProvider:
    JSON_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    HTML_URL = "https://www.forexfactory.com/calendar.php?week=this"
    MEMORY_TTL_S = 2 * 60 * 60
    PERSISTED_TTL_S = 7 * 24 * 60 * 60

    def __init__(self, db_manager: Any = None):
        # ``url`` remains available for compatibility with the old provider.
        self.url = self.HTML_URL
        self.db_manager = db_manager
        self.cache: List[Dict[str, Any]] = []
        self.last_update: Optional[datetime] = None
        self.source: Optional[str] = None
        self.status = "NOT_LOADED"
        self.source_fetched_at: Optional[float] = None
        self.last_attempt_at: Optional[float] = None

    async def fetch_events(self) -> List[Dict[str, Any]]:
        """Fetch and normalize events using JSON → HTML → persisted cache."""
        now = time.time()
        if (self.last_update and self.cache
                and now - self.last_update.timestamp() < self.MEMORY_TTL_S):
            return self.cache

        self.last_attempt_at = now
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        }

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                try:
                    response = await client.get(self.JSON_URL, headers=headers, timeout=10.0)
                    if response.status_code == 200:
                        events = self._parse_json_response(response)
                        if events:
                            self._store(events, "faireconomy_json", "LIVE")
                            return self.cache
                except Exception as exc:
                    logger.warning("Calendar JSON source failed: %s", exc)

                try:
                    response = await client.get(self.HTML_URL, headers=headers, timeout=15.0)
                    if response.status_code == 200:
                        events = self._parse_html(response.text)
                        if events:
                            self._store(events, "forexfactory_html", "FALLBACK")
                            return self.cache
                except Exception as exc:
                    logger.warning("Calendar HTML fallback failed: %s", exc)
        except Exception as exc:
            # Also covers clients that fail while entering the context manager.
            logger.warning("Calendar sources unavailable: %s", exc)

        persisted = self._load_persisted()
        if persisted:
            events, fetched_at, persisted_source = persisted
            self.cache = events
            self.last_update = datetime.fromtimestamp(fetched_at)
            self.source_fetched_at = fetched_at
            self.source = f"sqlite:{persisted_source or 'last_known'}"
            self.status = "CACHED"
            return self.cache

        # Never continue using an in-memory calendar older than seven days.
        if (self.cache and self.source_fetched_at
                and now - self.source_fetched_at <= self.PERSISTED_TTL_S):
            self.status = "CACHED"
            self.source = self.source or "memory:last_known"
            return self.cache

        self.cache = []
        self.source = None
        self.status = "DATA_UNAVAILABLE"
        return []

    def _parse_json_response(self, response: Any) -> List[Dict[str, Any]]:
        try:
            payload = response.json()
        except Exception:
            payload = json.loads(response.text)
        return self._parse_json(payload)

    def _parse_json(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            payload = payload.get("events") or payload.get("data") or []
        if not isinstance(payload, list):
            return []

        events: List[Dict[str, Any]] = []
        for raw in payload:
            if not isinstance(raw, dict) or not (raw.get("title") or raw.get("event")):
                continue
            date_raw = str(raw.get("date") or raw.get("datetime") or "").strip()
            event_date, event_time, timestamp_utc = date_raw, "", None
            if date_raw:
                try:
                    parsed = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = pytz.UTC.localize(parsed)
                    parsed_utc = parsed.astimezone(pytz.UTC)
                    event_date = parsed.strftime("%a %b %d")
                    event_time = parsed.strftime("%I:%M%p").lstrip("0").lower()
                    timestamp_utc = parsed_utc.isoformat()
                except (TypeError, ValueError):
                    pass
            impact = str(raw.get("impact") or "Low").strip().title()
            if impact not in ("Low", "Medium", "High"):
                impact = "Low"
            events.append({
                "title": str(raw.get("title") or raw.get("event") or "").strip(),
                "country": str(raw.get("country") or raw.get("currency") or "").strip(),
                "date": event_date,
                "time": event_time,
                "timestamp_utc": timestamp_utc,
                "impact": impact,
                "forecast": str(raw.get("forecast") or "").strip(),
                "previous": str(raw.get("previous") or "").strip(),
                "actual": str(raw.get("actual") or "").strip(),
            })
        return events

    def _store(self, events: List[Dict[str, Any]], source: str, status: str) -> None:
        fetched_at = time.time()
        self.cache = events
        self.last_update = datetime.fromtimestamp(fetched_at)
        self.source_fetched_at = fetched_at
        self.source = source
        self.status = status
        if self.db_manager and hasattr(self.db_manager, "save_calendar_cache"):
            try:
                self.db_manager.save_calendar_cache(events, source, fetched_at)
            except Exception as exc:
                logger.warning("Calendar cache persistence failed: %s", exc)

    def _load_persisted(self) -> Optional[tuple]:
        if not self.db_manager or not hasattr(self.db_manager, "load_calendar_cache"):
            return None
        try:
            cached = self.db_manager.load_calendar_cache(self.PERSISTED_TTL_S)
        except Exception as exc:
            logger.warning("Calendar cache read failed: %s", exc)
            return None
        if not cached or not cached.get("events"):
            return None
        return cached["events"], float(cached["fetched_at"]), cached.get("source")

    def get_state(self) -> Dict[str, Any]:
        now = time.time()
        age_s = None
        if self.source_fetched_at is not None:
            age_s = max(0, int(now - self.source_fetched_at))
        return {
            "source": self.source,
            "status": self.status,
            "age_s": age_s,
            "events": len(self.cache),
            "last_attempt_at": self.last_attempt_at,
            "valid_for_s": self.PERSISTED_TTL_S,
        }

    def _parse_html(self, html_content: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_content, "lxml")
        table = soup.find("table", class_="calendar__table")
        if not table:
            return []

        events = []
        current_date = ""
        rows = table.find_all("tr", class_="calendar__row")
        for row in rows:
            date_cell = row.find("td", class_="calendar__date")
            if date_cell:
                date_text = date_cell.text.strip()
                if date_text:
                    current_date = date_text.replace("\n", " ")

            title_cell = row.find("td", class_="calendar__event")
            if not title_cell:
                continue

            currency = row.find("td", class_="calendar__currency")
            impact_cell = row.find("td", class_="calendar__impact")
            impact_span = impact_cell.find("span") if impact_cell else None
            time_cell = row.find("td", class_="calendar__time")
            forecast = row.find("td", class_="calendar__forecast")
            previous = row.find("td", class_="calendar__previous")
            actual = row.find("td", class_="calendar__actual")

            impact = "Low"
            if impact_span:
                classes = impact_span.get("class", [])
                if any("high" in c.lower() for c in classes):
                    impact = "High"
                elif any("medium" in c.lower() for c in classes):
                    impact = "Medium"

            events.append({
                "title": title_cell.text.strip(),
                "country": currency.text.strip() if currency else "",
                "date": current_date,
                "time": time_cell.text.strip() if time_cell else "",
                "timestamp_utc": None,
                "impact": impact,
                "forecast": forecast.text.strip() if forecast else "",
                "previous": previous.text.strip() if previous else "",
                "actual": actual.text.strip() if actual else "",
            })
        return events


class NewsFilter:
    def __init__(self, safety_before_mins: int = 30, safety_after_mins: int = 60):
        self.safety_before = safety_before_mins
        self.safety_after = safety_after_mins

    def filter_high_impact(self, events: List[Dict[str, Any]],
                           asset_currency: Optional[str] = None) -> List[Dict[str, Any]]:
        high_impact = [event for event in events if event.get("impact") == "High"]
        if asset_currency:
            high_impact = [event for event in high_impact
                           if event.get("country") in (asset_currency, "USD", "EUR")]
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
                if event.get("timestamp_utc"):
                    event_time = datetime.fromisoformat(
                        str(event["timestamp_utc"]).replace("Z", "+00:00")
                    ).astimezone(pytz.UTC)
                else:
                    if not event.get("time") or "All Day" in event["time"]:
                        continue
                    event_time_str = f"{event['date']} {current_year} {event['time']}"
                    event_time = datetime.strptime(event_time_str, "%a %b %d %Y %I:%M%p")
                    event_time = pytz.timezone("America/New_York").localize(event_time)
                    event_time = event_time.astimezone(pytz.UTC)

                diff_mins = (event_time - now).total_seconds() / 60
                if -self.filter.safety_after < diff_mins < self.filter.safety_before:
                    blocking_event = dict(event)
                    blocking_event["time_utc"] = event_time
                    break
            except Exception:
                continue

        return {
            "is_blocked": blocking_event is not None,
            "blocking_event": blocking_event,
            "next_events": high_impact_events[:5],
        }


class SessionFilter:
    def __init__(self, timezone: str = "Europe/Paris"):
        self.tz = pytz.timezone(timezone)
        self.allowed_days = [0, 1, 2, 3, 4, 5, 6]

    def is_trading_allowed(self, asset_class: str = "CRYPTO") -> Dict[str, Any]:
        now = datetime.now(self.tz)
        day_of_week = now.weekday()
        day_ok = day_of_week in self.allowed_days
        current_time_float = now.hour + now.minute / 60.0

        if asset_class == "CRYPTO":
            session_ok = True
        elif asset_class == "FOREX":
            session_ok = 0 <= day_of_week <= 4
            if day_of_week == 4 and now.hour >= 22:
                session_ok = False
        else:
            session_ok = 9.0 <= current_time_float < 22.0 and 0 <= day_of_week <= 4

        return {
            "day_ok": day_ok,
            "session_ok": session_ok,
            "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "day_name": now.strftime("%A"),
            "asset_class": asset_class,
            "timezone": "Europe/Paris",
        }


class NewsEngine:
    """Orchestrate calendar, event risk and sessions with an explicit outage policy."""

    VALID_UNAVAILABLE_POLICIES = ("block_all", "block_tradfi_only", "allow_all")

    def __init__(self, db_manager: Any = None, unavailable_policy: str = "block_all"):
        self.provider = EconomicCalendarProvider(db_manager=db_manager)
        self.filter = NewsFilter()
        self.risk_engine = EventRiskEngine(self.filter)
        self.session_filter = SessionFilter()
        self.news_unavailable_policy = "block_all"
        self.set_unavailable_policy(unavailable_policy)

    def set_unavailable_policy(self, policy: str) -> None:
        normalized = str(policy or "block_all").lower()
        self.news_unavailable_policy = (
            normalized if normalized in self.VALID_UNAVAILABLE_POLICIES else "block_all"
        )

    def apply_settings(self, settings: Dict[str, str]) -> None:
        self.set_unavailable_policy(settings.get("news_unavailable_policy", "block_all"))

    def _outage_allows(self, asset_class: str) -> bool:
        if self.news_unavailable_policy == "allow_all":
            return True
        if self.news_unavailable_policy == "block_tradfi_only":
            return str(asset_class).upper() == "CRYPTO"
        return False

    def unavailable_status(self, asset_class: str = "CRYPTO",
                           title: str = "Calendar Unavailable") -> Dict[str, Any]:
        session_status = self.session_filter.is_trading_allowed(asset_class=asset_class)
        policy_allows = self._outage_allows(asset_class)
        news_ok = policy_allows
        return {
            "trading_allowed": bool(
                session_status["day_ok"] and session_status["session_ok"] and policy_allows
            ),
            "day_ok": session_status["day_ok"],
            "news_ok": news_ok,
            "session_ok": session_status["session_ok"],
            "blocking_event": None if policy_allows else {"title": title},
            "next_events": [],
            "status": "DATA_UNAVAILABLE",
            "source": self.provider.source,
            "calendar": self.provider.get_state(),
            "unavailable_policy": self.news_unavailable_policy,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }

    async def check_trading_allowed(self, asset_currency: Optional[str] = None,
                                    asset_class: str = "CRYPTO") -> Dict[str, Any]:
        session_status = self.session_filter.is_trading_allowed(asset_class=asset_class)
        all_events = await self.provider.fetch_events()
        if not all_events:
            return self.unavailable_status(asset_class=asset_class)

        high_impact = self.filter.filter_high_impact(all_events, asset_currency)
        risk_status = self.risk_engine.check_risk(high_impact)
        news_ok = not risk_status["is_blocked"]
        next_events = [
            {
                "time": event.get("time"),
                "currency": event.get("country"),
                "country": event.get("country"),
                "impact": event.get("impact"),
                "event": event.get("title"),
                "forecast": event.get("forecast"),
                "previous": event.get("previous"),
                "actual": event.get("actual"),
            }
            for event in high_impact
        ][:5]
        trading_allowed = bool(
            session_status["day_ok"] and session_status["session_ok"] and news_ok
        )
        return {
            "trading_allowed": trading_allowed,
            "day_ok": session_status["day_ok"],
            "news_ok": news_ok,
            "session_ok": session_status["session_ok"],
            "blocking_event": risk_status["blocking_event"],
            "next_events": next_events,
            "status": self.provider.status,
            "source": self.provider.source,
            "calendar": self.provider.get_state(),
            "unavailable_policy": self.news_unavailable_policy,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
