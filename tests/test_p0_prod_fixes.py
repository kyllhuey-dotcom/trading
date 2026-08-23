"""Production outage fixes (2026-08-23) — 0 trades + WS disconnects in prod.

P0-1  Calendar outage default `block_tradfi_only` (crypto keeps trading).

Subsequent sections (P0-2/P0-3/P0-4/P1) are appended with their fixes —
one theme per commit, every commit stays green offline.
"""
from unittest.mock import AsyncMock

import pytest

from api.engines.db_manager import DatabaseManager
from api.engines.news_engine import NewsEngine
from api.engines.settings_schema import SETTINGS_SPEC, ensure_defaults


# --------------------------------------------------------------------------- #
# P0-1 — Calendar outage: default block_tradfi_only
# --------------------------------------------------------------------------- #
def test_settings_schema_default_is_block_tradfi_only():
    """P0-1: the schema default no longer blocks crypto on calendar outage."""
    assert SETTINGS_SPEC["news_unavailable_policy"]["default"] == "block_tradfi_only"
    assert SETTINGS_SPEC["news_unavailable_policy"]["choices"] == (
        "block_all", "block_tradfi_only", "allow_all")


def test_news_engine_default_policy_is_block_tradfi_only():
    engine = NewsEngine()
    assert engine.news_unavailable_policy == "block_tradfi_only"


@pytest.mark.asyncio
async def test_calendar_outage_default_crypto_allowed_forex_blocked():
    """P0-1: calendar HS + default policy → CRYPTO news_ok True, FOREX False."""
    engine = NewsEngine()  # no explicit policy: production default path
    assert engine.news_unavailable_policy == "block_tradfi_only"
    engine.provider.fetch_events = AsyncMock(return_value=[])

    crypto = await engine.check_trading_allowed(asset_class="CRYPTO")
    assert crypto["status"] == "DATA_UNAVAILABLE"
    assert crypto["news_ok"] is True          # crypto trades during outage
    assert crypto["trading_allowed"] is True   # 24/7 market, session always ok

    forex = await engine.check_trading_allowed(asset_class="FOREX")
    assert forex["status"] == "DATA_UNAVAILABLE"
    assert forex["news_ok"] is False
    assert forex["trading_allowed"] is False   # tradfi stays fail-safe


@pytest.mark.asyncio
async def test_calendar_outage_block_all_still_blocks_crypto():
    """P0-1: allow_all unchanged; explicit block_all keeps blocking crypto."""
    engine = NewsEngine(unavailable_policy="block_all")
    engine.provider.fetch_events = AsyncMock(return_value=[])
    crypto = await engine.check_trading_allowed(asset_class="CRYPTO")
    assert crypto["news_ok"] is False
    assert crypto["trading_allowed"] is False

    allow = NewsEngine(unavailable_policy="allow_all")
    allow.provider.fetch_events = AsyncMock(return_value=[])
    assert (await allow.check_trading_allowed(asset_class="FOREX"))["news_ok"] is True


def test_db_seed_and_migration_use_block_tradfi_only(tmp_path):
    """P0-1: fresh seeds AND existing block_all DBs land on block_tradfi_only."""
    fresh = DatabaseManager(str(tmp_path / "fresh.db"))
    assert fresh.get_settings()["news_unavailable_policy"] == "block_tradfi_only"

    legacy = DatabaseManager(str(tmp_path / "legacy.db"))
    legacy.set_setting("news_unavailable_policy", "block_all")  # v2.9 seed
    legacy.set_setting("strategy_marker", "keep-me")
    legacy = DatabaseManager(str(tmp_path / "legacy.db"))  # re-init → migration
    settings = legacy.get_settings()
    assert settings["news_unavailable_policy"] == "block_tradfi_only"
    assert settings["strategy_marker"] == "keep-me"  # other rows untouched
    # An explicit operator choice (non-seed value) is preserved as-is.
    legacy.set_setting("news_unavailable_policy", "allow_all")
    assert DatabaseManager(str(tmp_path / "legacy.db")).get_settings()[
        "news_unavailable_policy"] == "allow_all"


def test_ensure_defaults_fills_block_tradfi_only():
    out = ensure_defaults({})
    assert out["news_unavailable_policy"] == "block_tradfi_only"
