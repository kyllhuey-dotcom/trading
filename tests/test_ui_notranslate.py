"""v3.3.1 — Google Translate hardening regression tests.

Symptom: enabling Google Translate in the browser made the UI flicker and
corrupted prices/PnL/scores/statuses. Root cause: the app re-renders dynamic
zones every 2 s (innerText/innerHTML writes) while Google Translate keeps its
own node↔translation mapping over the SAME text nodes — the two fight, the
DOM alternates between app text and translated text (flicker + corruption).

Contract (enforced here):
1. every dynamic data zone carries `notranslate` (Google Translate's own
   opt-out class) — statically in index.html AND preserved across the JS
   className rewrites that happen on re-render;
2. renderers are FULL rewrites (innerHTML = / textContent =) — never
   append-style — so any translator-mutated subtree is replaced, not merged;
3. i18n re-render (data-i18n zones) never overlaps the notranslate data zones;
4. document.documentElement.lang is set at load and on every UI language
   change (prevents double-translation);
5. the honest "no market data" banner is wired to scanner truth only.

These tests are static by design (no JS engine in the sandbox): they assert
the exact DOM/JS contract the browser depends on.
"""
import os
import re

from bs4 import BeautifulSoup

HTML_PATH = os.path.join("public", "index.html")
I18N_PATH = os.path.join("public", "js", "i18n.js")


def _html() -> str:
    with open(HTML_PATH, encoding="utf-8") as fh:
        return fh.read()


def _soup() -> BeautifulSoup:
    return BeautifulSoup(_html(), "html.parser")


def _script() -> str:
    """The inline application script (last <script> block of index.html)."""
    scripts = re.findall(r"<script>(.*?)</script>", _html(), re.S)
    assert scripts, "no inline script found"
    return max(scripts, key=len)


# --------------------------------------------------------------------------- #
# 1. Static data zones are notranslate                                        #
# --------------------------------------------------------------------------- #

CORE_DATA_IDS = [
    # money / PnL
    "header-balance", "dash-balance", "dash-equity", "dash-daily-pnl",
    "dash-drawdown", "prov-demo-val", "prov-real-val",
    # prices / market
    "trading-live-price", "market-status-tag", "active-symbol-header",
    "trading-active-symbol", "trading-asset-class", "orderbook-rows",
    # counts / scores / indicators
    "trades-today-count", "next-scan-countdown", "ind-rsi", "ind-ema",
    "ind-atr", "ana-trend", "ana-struct", "ana-mom", "ana-vol",
    "op-risk", "op-pct", "op-fees", "manual-risk-calc",
    # engine / radar statuses
    "system-status-text", "bot-mode-display", "execution-intent-badge",
    "eng-status", "eng-markets", "eng-scanned", "eng-analyzing",
    "eng-signals", "eng-tradable", "eng-duration",
    "radar-progress", "radar-unavailable", "radar-error", "radar-last-scan",
    # ids / codes / timestamps
    "diag-main-blocker", "diag-reason", "bos-choch-tag", "arm-label",
]


def test_static_data_zones_carry_notranslate():
    soup = _soup()
    missing = []
    for el_id in CORE_DATA_IDS:
        el = soup.find(id=el_id)
        if el is None:
            missing.append(f"{el_id} (element gone)")
        elif "notranslate" not in (el.get("class") or []):
            missing.append(el_id)
    assert not missing, f"data zones without notranslate: {missing}"


def test_dynamic_render_containers_carry_notranslate():
    """Containers fully rewritten via innerHTML shield their children."""
    soup = _soup()
    containers = ["scanner-table-body", "history-table-body",
                  "markets-full-grid", "broker-status-table", "wallet-list",
                  "terminal-news-list", "active-pos-list", "terminal-pos-list",
                  "terminal-orders-body", "trading-signal-box",
                  "terminal-blockers", "diag-checklist", "best-setups-list",
                  "dashboard-calendar", "terminal-audit-logs", "sltp-preview"]
    missing = [cid for cid in containers
               if (soup.find(id=cid) is None)
               or ("notranslate" not in (soup.find(id=cid).get("class") or []))]
    assert not missing, f"render containers without notranslate: {missing}"


def test_market_filter_options_are_notranslate():
    """setMarketFilter() compares innerText — a translated 'ALL' would break
    the filter selection permanently."""
    soup = _soup()
    host = soup.find(id="market-filters")
    assert host is not None
    options = host.find_all(class_="switch-option")
    labels = {o.get_text(strip=True) for o in options}
    assert {"ALL", "CRYPTO", "STOCKS"} <= labels
    bad = [o.get_text(strip=True) for o in options
           if "notranslate" not in (o.get("class") or [])]
    assert not bad, f"filter options translatable (breaks innerText match): {bad}"


# --------------------------------------------------------------------------- #
# 2. className rewrites preserve notranslate                                  #
# --------------------------------------------------------------------------- #

def test_classname_rewrites_keep_notranslate():
    """Re-renders that REPLACE className must keep the notranslate flag,
    otherwise the zone becomes translatable again after the first poll."""
    script = _script()
    # var names assigned from getElementById within the script
    var_sources = dict(re.findall(
        r"(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\('([\w-]+)'\)",
        script))
    allowlist = {
        # translatable UI (labels via our i18n) or text-less dots/icons
        "armBtn", "demoBtn", "liveBtn", "startBtn", "icon", "armDot", "dot",
        "b",      # order-history filter buttons (labels from i18n)
        "hint", "out",  # free-text messages (user-facing, translatable)
    }
    # vars created from scratch (createElement) are not pre-existing zones
    created = set(re.findall(
        r"(?:const|let|var)\s+(\w+)\s*=\s*document\.createElement", script))
    offenders = []
    for m in re.finditer(r"(\w+)\.className\s*=\s*([^;]+;)", script):
        var, assignment = m.group(1), m.group(2)
        if var in allowlist or var in created:
            continue
        if "notranslate" not in assignment:
            offenders.append(var_sources.get(var, var))
    assert not offenders, \
        f"className rewrites losing notranslate on: {sorted(set(offenders))}"


# --------------------------------------------------------------------------- #
# 3. Renderers are full rewrites (translator mutations get replaced)          #
# --------------------------------------------------------------------------- #

def test_renderers_never_append_into_translated_dom():
    script = _script()
    assert "insertAdjacentHTML" not in script, \
        "append-style DOM writes merge with translator <font> nodes"
    assert "innerHTML +=" not in script, \
        "innerHTML += merges app output with translator-mutated content"
    assert "outerHTML +=" not in script


def test_core_renderers_replace_their_zone():
    """Every renderer that owns a dynamic zone reassigns the zone wholesale —
    so a DOM mutated by the translator is wiped on the next render, and with
    notranslate zones the translator never fights the app in the first place."""
    script = _script()
    for fn, zone in [
        ("updateScanner", "scanner-table-body"),
        ("updateHistory", "history-table-body"),
        ("initMarkets", "markets-full-grid"),
        ("updateBrokers", "broker-status-table"),
        ("renderCalendar", "dashboard-calendar"),
        ("renderAuditLogs", "terminal-audit-logs"),
        ("updateNews", "terminal-news-list"),
        ("renderTerminalPanels", "terminal-orders-body"),
    ]:
        m = re.search(r"(?:async\s+)?function\s+%s\b" % fn, script)
        assert m, f"renderer {fn} missing"
        body = script[m.start():script.find("\n        function", m.start())
                      if script.find("\n        function", m.start()) > 0
                      else len(script)]
        assert zone in body, f"{fn} no longer renders {zone}"
        assert "innerHTML =" in body, f"{fn} must fully rewrite {zone}"


# --------------------------------------------------------------------------- #
# 4. i18n re-render never overlaps data zones; document lang stays in sync    #
# --------------------------------------------------------------------------- #

def test_no_zone_is_both_i18n_and_notranslate():
    soup = _soup()
    clash = [el.get("id") or el.get("data-i18n")
             for el in soup.find_all(attrs={"data-i18n": True})
             if "notranslate" in (el.get("class") or [])]
    assert not clash, f"data-i18n zones marked notranslate (dead translations): {clash}"


def test_document_lang_set_at_load_and_on_change():
    html = _html()
    assert re.search(r'<html\s+lang="[a-z]{2}"', html), "missing <html lang>"
    assert "QTP_I18N.applyLanguage(localStorage.getItem('qtp-lang') || 'en')" in html, \
        "applyLanguage not called at load"
    i18n = open(I18N_PATH, encoding="utf-8").read()
    apply_fn = i18n[i18n.find("function applyLanguage"):i18n.find("global.QTP_I18N")]
    assert 'document.documentElement.setAttribute("lang", currentLang)' in apply_fn, \
        "applyLanguage must sync document.documentElement.lang"
    assert "changeLanguage" in html and "applyLanguage(val)" in html, \
        "UI language switch must route through applyLanguage"


# --------------------------------------------------------------------------- #
# 5. Honest "no market data" banner                                           #
# --------------------------------------------------------------------------- #

def test_no_market_data_banner_wired():
    soup = _soup()
    banner = soup.find(id="no-market-data-banner")
    assert banner is not None, "banner element missing"
    assert "hidden" in (banner.get("class") or []), "banner must start hidden"
    assert banner.get("data-i18n") == "noMarketData"
    i18n = open(I18N_PATH, encoding="utf-8").read()
    assert i18n.count("noMarketData:") == 4, "noMarketData must exist in 4 langs"
    script = _script()
    assert "no-market-data-banner" in script, "banner never toggled by JS"
    # honesty: driven by scanner completion/unavailability, nothing invented
    gate = re.search(r"const scanDone =(.*?)ndBanner\.classList\.toggle",
                     script, re.S)
    assert gate, "banner toggle not driven by scanner truth"
    signals = gate.group(1)
    assert "progress_count" in signals and "markets_unavailable" in signals \
        and "scan_error" in signals and "DATA_UNAVAILABLE" in signals, \
        "banner gate must use scanner completion + unavailability signals"


# --------------------------------------------------------------------------- #
# 6. i18n parity untouched (fr/en/es/de) with the new key                     #
# --------------------------------------------------------------------------- #

def test_i18n_parity_with_no_market_data_key():
    import json
    js = open(I18N_PATH, encoding="utf-8").read()
    langs = ("en", "fr", "es", "de")
    blocks = {}
    for lang in langs:
        start = js.find(f"        {lang}: {{")
        assert start > 0
        ends = [js.find(f"        {lng}: {{", start) for lng in langs]
        end = min([e for e in ends if e > start] + [js.find("    };", start)])
        block = js[start:end]
        keys = set(re.findall(r"^ {12}([A-Za-z0-9_]+):", block, re.M))
        blocks[lang] = keys
        assert "noMarketData" in keys
    base = blocks["en"]
    for lang in langs:
        assert blocks[lang] == base, f"{lang} parity broken"

    # the banner sentence must be a real translation (not the key/English copy)
    fr = re.search(r"noMarketData: \"([^\"]+)\"", js[js.find("        fr: {"):])
    assert fr and "DONNÉE" in fr.group(1).upper()
    assert json.dumps(fr.group(1))  # sanity: valid string literal
