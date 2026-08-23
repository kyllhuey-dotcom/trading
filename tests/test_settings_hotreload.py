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
    assert out["min_signal_score"] == "84" or "min_signal_score" in out


def test_post_applies_live():
    # v2.8: min_signal_score floor is 84, values below are clamped to 84
    r = client.post("/api/settings", json={"min_signal_score": "85", "max_open_positions": "12"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["message"] == "Parameters deployed live"
    assert signal_engine.min_score == 85
    assert risk_engine.max_open_positions == 12
    # restore shared state
    client.post("/api/settings", json={"min_signal_score": "84", "max_open_positions": "10"})
    settings_provider.invalidate()
    settings_provider.apply()
    assert signal_engine.min_score == 84
    assert risk_engine.max_open_positions == 10


def test_html_banner_validation():
    html = open("public/index.html", encoding="utf-8").read()
    assert "settings-live-banner" in html
    assert "min_signal_score must be 84" in html or "84–99" in html
