"""Railway 502 mitigations: batched scan, yahoo threads, memory guard, lifespan."""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.engines.scanner_engine import SCAN_BATCH_SIZE, ScannerEngine
from api.engines.data_providers import yahoo_provider as yp


def test_yahoo_download_threads_false():
    src = inspect.getsource(yp.YahooProvider.prepare_cycle)
    assert "threads=False" in src
    assert "group_by=\"ticker\"" in src or "group_by='ticker'" in src
    assert "threads=True" not in src


@pytest.mark.asyncio
async def test_scanner_batching(monkeypatch):
    assert SCAN_BATCH_SIZE == 30
    universe = MagicMock()
    crypto = [f"c{i}" for i in range(35)]
    tradfi = [f"t{i}" for i in range(28)]
    ids = crypto + tradfi

    def get_info(sym):
        return {"asset_class": "CRYPTO" if str(sym).startswith("c") else "FX"}

    universe.get_all_ids.return_value = ids
    universe.get_info.side_effect = get_info

    data = MagicMock()
    data.universe = universe
    data.prepare_scan_cycle = AsyncMock()

    engine = ScannerEngine(data, MagicMock(), MagicMock(), MagicMock(), max_concurrent=4)

    async def fake_scan(symbol, semaphore, strategy_mode=None):
        return {"symbol": symbol, "status": "OK"}

    engine.scan_asset = fake_scan  # type: ignore[method-assign]

    gc_calls = []
    monkeypatch.setattr("api.engines.scanner_engine.gc.collect", lambda: gc_calls.append(1))

    seen = []

    def cb(row, done, total):
        seen.append((row["symbol"], done, total))

    results = await engine.scan_all(progress_callback=cb)
    assert len(results) == 63
    assert [r["symbol"] for r in results] == ids
    assert len(seen) == 63
    assert seen[-1] == (ids[-1], 63, 63)
    # crypto 35 -> 2 batches, tradfi 28 -> 1 batch
    assert data.prepare_scan_cycle.await_count == 3
    assert len(gc_calls) == 3


@pytest.mark.asyncio
async def test_scanner_loop_memory_guard(monkeypatch):
    import api.index as idx

    monkeypatch.setattr(idx, "_read_vmrss_mb", lambda: 500.0)
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(idx.asyncio, "sleep", fake_sleep)
    tick = AsyncMock()
    monkeypatch.setattr(idx, "tick_scanner", tick)
    with pytest.raises(asyncio.CancelledError):
        await idx.scanner_loop()
    tick.assert_not_awaited()
    assert slept == [60]


def test_read_vmrss_parses_proc(tmp_path, monkeypatch):
    import api.index as idx

    proc = tmp_path / "status"
    proc.write_text("Name:\tpython\nVmRSS:\t430080 kB\n", encoding="utf-8")
    orig_open = open

    def fake_open(path, *a, **k):
        if str(path) == "/proc/self/status":
            return orig_open(proc, *a, **k)
        return orig_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert abs(idx._read_vmrss_mb() - 420.0) < 0.1


@pytest.mark.asyncio
async def test_lifespan_survives_settings_error(monkeypatch):
    import api.index as idx

    monkeypatch.setattr(idx.broker_connector, "initialize_from_db", AsyncMock())
    monkeypatch.setattr(idx.broker_connector, "shutdown", AsyncMock())
    monkeypatch.setattr(idx.data_engine, "shutdown", AsyncMock())
    monkeypatch.setattr(idx.settings_provider, "apply", MagicMock(side_effect=RuntimeError("settings boom")))

    created = []
    orig = asyncio.create_task

    def track(coro):
        created.append(True)
        return orig(coro)

    monkeypatch.setattr(idx.asyncio, "create_task", track)
    async with idx.lifespan(idx.app):
        pass
    assert created == []
