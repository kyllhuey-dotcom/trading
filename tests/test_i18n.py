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


def test_post_language_fr():
    r = client.post("/api/settings", json={"language": "fr"})
    assert r.status_code == 200
    assert r.json()["success"] is True
    s = client.get("/api/settings").json()
    assert s.get("language") == "fr"
    client.post("/api/settings", json={"language": "en"})
