from fastapi.testclient import TestClient
from api.engines.settings_schema import validate_settings, ensure_defaults
from api.index import app, signal_engine, risk_engine, settings_provider

client = TestClient(app)


def test_clamp_invalid_language():
    cleaned, errors = validate_settings({"min_signal_score": "120", "language": "it", "unknown_key": "x"})
    assert cleaned["min_signal_score"] == "99"
    assert cleaned["language"] == "en"
    assert cleaned["unknown_key"] == "x"
    assert errors


def test_ensure_defaults_no_overwrite():
    out = ensure_defaults({"max_open_positions": "3"})
    assert out["max_open_positions"] == "3"
    assert out["min_signal_score"] == "80" or "min_signal_score" in out


def test_post_applies_live():
    # v2.7: min_signal_score floor is 80, so 77 gets clamped to 80
    r = client.post("/api/settings", json={"min_signal_score": "85", "max_open_positions": "12"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["message"] == "Parameters deployed live"
    assert signal_engine.min_score == 85
    assert risk_engine.max_open_positions == 12
    # restore shared state
    client.post("/api/settings", json={"min_signal_score": "80", "max_open_positions": "10"})
    settings_provider.invalidate()
    settings_provider.apply()
    assert signal_engine.min_score == 80
    assert risk_engine.max_open_positions == 10


def test_html_banner_validation():
    html = open("public/index.html", encoding="utf-8").read()
    assert "settings-live-banner" in html
    assert "min_signal_score must be 50" in html or "50–99" in html
