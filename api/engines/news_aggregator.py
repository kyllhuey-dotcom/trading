import asyncio
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import logging
import os
from typing import Any, Dict, List

import feedparser
import httpx

logger = logging.getLogger("NewsAggregator")

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD")
ASSET_KEYWORDS = {
    "GOLD": ("GOLD", "XAU"),
    "OIL": ("OIL", "WTI", "BRENT"),
    "BITCOIN": ("BTC", "BITCOIN"),
    "ETHEREUM": ("ETH", "ETHEREUM"),
    "SPX": ("S&P", "SP500", "SPX"),
    "NASDAQ": ("NASDAQ", "NAS100"),
}


def _extract_mentions(title: str) -> tuple[List[str], List[str]]:
    text = str(title or "").upper()
    currencies = [currency for currency in CURRENCIES if currency in text]
    assets = [asset for asset, keywords in ASSET_KEYWORDS.items()
              if any(keyword in text for keyword in keywords)]
    return currencies, assets


def _timestamp_sort_key(value: Any) -> float:
    """Best-effort ordering for RFC-2822, ISO-8601, and epoch timestamps."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0


class BaseNewsProvider:
    def __init__(self, name: str, source_url: str):
        self.name = name
        self.source_url = source_url

    async def fetch_raw(self) -> List[Dict[str, Any]]:
        return []

class ForexLiveNewsProvider(BaseNewsProvider):
    """Rule 14, 15: Authorized ForexLive news integration with multiple categories."""
    def __init__(self):
        # Multiple specialized feeds for better categorization (Rule 14)
        self.feeds = {
            "GENERAL": "https://www.forexlive.com/feed",
            "CRYPTO": "https://www.forexlive.com/feed/cryptocurrency",
            "CENTRAL_BANK": "https://www.forexlive.com/feed/centralbank",
            "ORDERS": "https://www.forexlive.com/feed/forexorders",
            "TECHNICAL": "https://www.forexlive.com/feed/technicalanalysis"
        }
        super().__init__("ForexLive", self.feeds["GENERAL"])

    async def fetch_raw(self) -> List[Dict[str, Any]]:
        all_news = []
        tasks = []
        
        async def fetch_feed(cat, url):
            try:
                feed = await asyncio.to_thread(feedparser.parse, url)
                items = []
                for entry in feed.entries:
                    items.append({
                        "title": entry.title,
                        "timestamp": entry.published if hasattr(entry, 'published') else datetime.now().isoformat(),
                        "url": entry.link,
                        "source": self.name,
                        "category": cat,
                        "related_currencies": self._extract_currencies(entry.title),
                        "related_assets": self._extract_assets(entry.title)
                    })
                return items
            except Exception as exc:
                logger.warning("ForexLive feed error (%s): %s", cat, exc)
                return []

        for cat, url in self.feeds.items():
            tasks.append(fetch_feed(cat, url))
        
        results = await asyncio.gather(*tasks)
        for res in results:
            all_news.extend(res)
            
        return all_news

    def _extract_currencies(self, title: str) -> List[str]:
        return _extract_mentions(title)[0]

    def _extract_assets(self, title: str) -> List[str]:
        return _extract_mentions(title)[1]

class INGNewsProvider(BaseNewsProvider):
    """Rule 16: ING Think authorized analysis integration."""
    def __init__(self):
        # Official ING Think RSS for market analysis (FX, Rates, Credit)
        super().__init__("ING Think", "https://think.ing.com/rss")

    async def fetch_raw(self) -> List[Dict[str, Any]]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(self.source_url, headers=headers, timeout=15.0)
                if response.status_code == 200:
                    feed = feedparser.parse(response.text)
                    news = []
                    for entry in feed.entries:
                        news.append({
                            "title": entry.title,
                            "timestamp": entry.published if hasattr(entry, 'published') else datetime.now().isoformat(),
                            "url": entry.link,
                            "source": self.name,
                            "category": "MACRO",
                            "related_currencies": self._extract_currencies(entry.title),
                            "related_assets": self._extract_assets(entry.title)
                        })
                    return news
                return []
        except Exception as exc:
            logger.warning("ING Think provider error: %s", exc)
            return []

    def _extract_currencies(self, title: str) -> List[str]:
        return _extract_mentions(title)[0]

    def _extract_assets(self, title: str) -> List[str]:
        return _extract_mentions(title)[1]

class NationalNewsProvider(BaseNewsProvider):
    """Rule 17: Configurable National/Global news integration."""
    def __init__(self):
        # Defaulting to BBC Business for maximum reliability and global/national coverage
        self.config_name = os.getenv("NEWS_PROVIDER_NATIONAL", "BBC Business")
        self.config_url = os.getenv("NEWS_PROVIDER_URL", "http://feeds.bbci.co.uk/news/business/rss.xml")
        super().__init__(self.config_name, self.config_url)

    async def fetch_raw(self) -> List[Dict[str, Any]]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(self.source_url, headers=headers, timeout=15.0)
                if response.status_code == 200:
                    feed = feedparser.parse(response.text)
                    return [{
                        "title": entry.title,
                        "timestamp": entry.published if hasattr(entry, 'published') else datetime.now().isoformat(),
                        "url": entry.link,
                        "source": self.name,
                        "category": "GENERAL",
                        "related_currencies": self._extract_currencies(entry.title),
                        "related_assets": self._extract_assets(entry.title)
                    } for entry in feed.entries]
                return []
        except Exception as exc:
            logger.warning("National news provider error (%s): %s", self.name, exc)
            return []

    def _extract_currencies(self, title: str) -> List[str]:
        return _extract_mentions(title)[0]

    def _extract_assets(self, title: str) -> List[str]:
        return _extract_mentions(title)[1]

class NewsAggregator:
    """Rule 18, 19: Aggregates, deduplicates and ranks news impact."""
    def __init__(self):
        self.providers = [
            ForexLiveNewsProvider(), 
            INGNewsProvider(),
            NationalNewsProvider() # Rule 17 added
        ]
        self.cache: List[Dict[str, Any]] = []

    async def get_latest_news(self) -> List[Dict[str, Any]]:
        tasks = [provider.fetch_raw() for provider in self.providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        flat_list: List[Dict[str, Any]] = []
        for provider, result in zip(self.providers, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                logger.warning("News provider %s failed: %s", provider.name, result)
                continue
            if result:
                flat_list.extend(item for item in result if isinstance(item, dict))

        # Rule 19: normalize case/whitespace before title-based deduplication.
        seen_hashes = set()
        deduped = []
        for item in flat_list:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            normalized_title = " ".join(title.casefold().split())
            title_hash = hashlib.sha256(normalized_title.encode("utf-8")).digest()
            if title_hash in seen_hashes:
                continue
            seen_hashes.add(title_hash)
            enriched = dict(item)
            enriched["title"] = title
            enriched.setdefault("timestamp", datetime.now().isoformat())
            enriched["impact"] = self._calculate_impact(enriched)
            deduped.append(enriched)

        self.cache = sorted(
            deduped,
            key=lambda item: _timestamp_sort_key(item.get("timestamp")),
            reverse=True,
        )[:20]
        return list(self.cache)

    def _calculate_impact(self, item: Dict[str, Any]) -> str:
        title = item['title'].lower()
        cat = item.get('category', 'GENERAL')
        src = item.get('source', '')
        
        # Rule 20: News Impact Engine
        # Critical keywords (Protocol Override level)
        if any(word in title for word in ["intervention", "emergency", "crisis", "black swan", "halt", "default", "recession"]):
            return "CRITICAL"
        
        # High impact keywords
        if any(word in title for word in ["fomc", "cpi", "nfp", "payroll", "rate decision", "powell", "lagarde", "inflation", "gdp", "hawkish", "dovish"]):
            return "HIGH"
        
        # Source-specific High impact (e.g., ING Macro calls)
        if src == "ING Think" and any(word in title for word in ["rates", "forecast", "cut", "hike"]):
             return "HIGH"

        # Category based high impact
        if cat == "CENTRAL_BANK":
            if any(word in title for word in ["hike", "cut", "pivot", "stance", "policy"]):
                return "HIGH"
            return "MEDIUM"
            
        # Medium impact keywords
        if any(word in title for word in ["consumer confidence", "pmi", "retail sales", "inventory", "outlook", "sentiment"]):
            return "MEDIUM"
            
        return "LOW"
