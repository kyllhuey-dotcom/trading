from fastapi.testclient import TestClient
from api.index import app

client = TestClient(app)
CRITICAL = ["dashboard", "scanner", "trading", "settings", "radarTitle", "deployed", "waitingSetup"]


def test_four_languages_and_keys():
    js = open("public/js/i18n.js", encoding="utf-8").read()
    for lang in ("en", "fr", "es", "de"):
        assert f"{lang}:" in js
    for key in CRITICAL:
        assert f"{key}:" in js


def test_html_i18n_wire():
    html = open("public/index.html", encoding="utf-8").read()
    assert "/js/i18n.js" in html
    assert "data-i18n" in html
    assert "changeLanguage" in html
    assert "QTP_I18N.applyLanguage" in html


def test_strict_parity_of_four_dictionaries():
    """v2.8: en/fr/es/de must expose EXACTLY the same key set."""
    import re
    js = open("public/js/i18n.js", encoding="utf-8").read()
    langs = ["en", "fr", "es", "de"]
    positions = {lang: js.find(f"        {lang}: {{") for lang in langs}
    assert all(pos > 0 for pos in positions.values())
    keysets = {}
    for lang in langs:
        start = positions[lang]
        later = [positions[lng] for lng in langs if positions[lng] > start]
        end = min(later + [js.find("    };", start)])
        keysets[lang] = set(re.findall(r"^\s{12}([A-Za-z0-9_]+):", js[start:end], re.M))
    base = keysets["en"]
    for lang in langs:
        missing = base - keysets[lang]
        extra = keysets[lang] - base
        assert not missing, f"{lang} missing keys: {sorted(missing)}"
        assert not extra, f"{lang} extra keys: {sorted(extra)}"


def test_v28_strings_present_in_all_languages():
    """v2.8: every new string exists in the 4 dictionaries."""
    import re
    js = open("public/js/i18n.js", encoding="utf-8").read()
    langs = ["en", "fr", "es", "de"]
    positions = {lang: js.find(f"        {lang}: {{") for lang in langs}
    blocks = {}
    for lang in langs:
        start = positions[lang]
        later = [positions[lng] for lng in langs if positions[lng] > start]
        end = min(later + [js.find("    };", start)])
        blocks[lang] = js[start:end]
    required = ["tradingActive", "paused", "tradesToday", "nextScan", "confirmOrder",
                "estimatedRisk", "watchOnlyBadge", "addWatchOnly", "testConnection",
                "connectedBadge", "degradedBadge", "errorBadge", "inactiveBadge",
                "orderHistory", "filterAll", "filterOpen", "filterClosed", "scanSec"]
    for lang in langs:
        for key in required:
            assert re.search(rf"^\s{{12}}{key}:\s*\"", blocks[lang], re.M), f"{lang}.{key} missing"
    # canonical badge translations
    assert 'tradingActive: "TRADING ACTIVE"' in blocks["en"]
    assert 'tradingActive: "TRADING ACTIF"' in blocks["fr"]
    assert 'tradingActive: "TRADING ACTIVO"' in blocks["es"]
    assert 'tradingActive: "TRADING AKTIV"' in blocks["de"]
    assert 'paused: "PAUSED"' in blocks["en"]
    assert 'paused: "EN PAUSE"' in blocks["fr"]
    assert 'paused: "EN PAUSA"' in blocks["es"]
    assert 'paused: "PAUSIERT"' in blocks["de"]
    assert blocks["en"].count('watchOnlyBadge: "WATCH-ONLY"') == 1
    for lang in langs:
        assert 'watchOnlyBadge: "WATCH-ONLY"' in blocks[lang]
    # honesty: no dictionary may promise profitability or frame the score as
    # a probability
    for lang in langs:
        low = blocks[lang].lower()
        assert "probability" not in low
        assert "probabilité" not in low
        assert "wahrscheinlichkeit" not in low
        assert "probabilidad" not in low


def test_post_language_fr():
    r = client.post("/api/settings", json={"language": "fr"})
    assert r.status_code == 200
    assert r.json()["success"] is True
    s = client.get("/api/settings").json()
    assert s.get("language") == "fr"
    client.post("/api/settings", json={"language": "en"})
