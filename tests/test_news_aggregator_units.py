"""Fully offline tests for news providers, normalization, and impact ranking."""
from types import SimpleNamespace

import pytest

import api.engines.news_aggregator as news_module
from api.engines.news_aggregator import (
    BaseNewsProvider,
    ForexLiveNewsProvider,
    INGNewsProvider,
    NationalNewsProvider,
    NewsAggregator,
    _extract_mentions,
    _timestamp_sort_key,
)


class FakeHTTPClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.response


async def test_base_provider_and_mention_helpers():
    provider = BaseNewsProvider("base", "https://example.test")
    assert provider.name == "base"
    assert provider.source_url == "https://example.test"
    assert await provider.fetch_raw() == []
    currencies, assets = _extract_mentions("EUR/USD: Bitcoin, gold and WTI rally")
    assert currencies == ["USD", "EUR"]
    assert assets == ["GOLD", "OIL", "BITCOIN"]
    assert _extract_mentions("") == ([], [])

    assert _timestamp_sort_key(123) == 123.0
    assert _timestamp_sort_key("2026-08-22T12:00:00+00:00") > 0
    assert _timestamp_sort_key("Sat, 22 Aug 2026 11:00:00 GMT") > 0
    assert _timestamp_sort_key("invalid") == 0.0
    assert _timestamp_sort_key(None) == 0.0


async def test_forexlive_fetches_all_feeds_and_isolates_one_failure(monkeypatch):
    provider = ForexLiveNewsProvider()
    provider.feeds = {"GENERAL": "good", "CRYPTO": "bad"}

    def parse(url):
        if url == "bad":
            raise RuntimeError("feed unavailable")
        return SimpleNamespace(entries=[
            SimpleNamespace(
                title="EUR and Bitcoin rise",
                published="Sat, 22 Aug 2026 10:00:00 GMT",
                link="https://example.test/one",
            ),
            SimpleNamespace(title="Gold update", link="https://example.test/two"),
        ])

    monkeypatch.setattr(news_module.feedparser, "parse", parse)
    rows = await provider.fetch_raw()
    assert len(rows) == 2
    assert rows[0]["category"] == "GENERAL"
    assert rows[0]["related_currencies"] == ["EUR"]
    assert rows[0]["related_assets"] == ["BITCOIN"]
    assert rows[1]["timestamp"]
    assert provider._extract_assets("NASDAQ and ETH") == ["ETHEREUM", "NASDAQ"]
    assert provider._extract_currencies("GBPJPY") == ["GBP", "JPY"]


@pytest.mark.parametrize("provider_cls", [INGNewsProvider, NationalNewsProvider])
async def test_http_news_providers_success_non_200_and_error(monkeypatch, provider_cls):
    entry = SimpleNamespace(
        title="USD rates and gold",
        published="Sat, 22 Aug 2026 10:00:00 GMT",
        link="https://example.test/article",
    )
    monkeypatch.setattr(
        news_module.feedparser,
        "parse",
        lambda text: SimpleNamespace(entries=[entry]),
    )

    response = SimpleNamespace(status_code=200, text="<rss />")
    monkeypatch.setattr(
        news_module.httpx,
        "AsyncClient",
        lambda **kwargs: FakeHTTPClient(response=response),
    )
    provider = provider_cls()
    rows = await provider.fetch_raw()
    assert len(rows) == 1
    assert rows[0]["related_currencies"] == ["USD"]
    assert rows[0]["related_assets"] == ["GOLD"]
    assert provider._extract_currencies("AUD and CAD") == ["AUD", "CAD"]
    assert provider._extract_assets("SPX and oil") == ["OIL", "SPX"]

    response.status_code = 503
    assert await provider.fetch_raw() == []

    monkeypatch.setattr(
        news_module.httpx,
        "AsyncClient",
        lambda **kwargs: FakeHTTPClient(error=RuntimeError("network down")),
    )
    assert await provider.fetch_raw() == []


async def test_national_provider_uses_environment(monkeypatch):
    monkeypatch.setenv("NEWS_PROVIDER_NATIONAL", "Local Business")
    monkeypatch.setenv("NEWS_PROVIDER_URL", "https://local.test/rss")
    provider = NationalNewsProvider()
    assert provider.name == "Local Business"
    assert provider.source_url == "https://local.test/rss"


class StaticProvider(BaseNewsProvider):
    def __init__(self, name, rows=None, error=None):
        super().__init__(name, "https://example.test")
        self.rows = rows or []
        self.error = error

    async def fetch_raw(self):
        if self.error:
            raise self.error
        return list(self.rows)


async def test_aggregator_isolates_failures_deduplicates_sorts_and_ranks():
    rows = [
        {"title": " Emergency   crisis ", "timestamp": "2026-08-22T12:00:00Z"},
        {"title": "emergency crisis", "timestamp": "2026-08-22T11:00:00Z"},
        {"title": "FOMC inflation decision", "timestamp": "2026-08-22T10:00:00Z"},
        {"title": "Rates forecast", "source": "ING Think", "timestamp": 9},
        {"title": "Policy hike", "category": "CENTRAL_BANK", "timestamp": 8},
        {"title": "Central bank speech", "category": "CENTRAL_BANK", "timestamp": 7},
        {"title": "PMI outlook", "timestamp": 6},
        {"title": "Routine company update", "timestamp": 5},
        {"title": "", "timestamp": 100},
        "invalid-row",
    ]
    aggregator = NewsAggregator()
    aggregator.providers = [
        StaticProvider("one", rows),
        StaticProvider("broken", error=RuntimeError("provider down")),
    ]

    result = await aggregator.get_latest_news()
    assert len(result) == 7
    assert result[0]["title"] == "Emergency   crisis"
    impacts = {item["title"]: item["impact"] for item in result}
    assert impacts["Emergency   crisis"] == "CRITICAL"
    assert impacts["FOMC inflation decision"] == "HIGH"
    assert impacts["Rates forecast"] == "HIGH"
    assert impacts["Policy hike"] == "HIGH"
    assert impacts["Central bank speech"] == "MEDIUM"
    assert impacts["PMI outlook"] == "MEDIUM"
    assert impacts["Routine company update"] == "LOW"
    assert aggregator.cache == result
    assert result is not aggregator.cache
