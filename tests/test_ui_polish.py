def test_polish_hooks():
    html = open("public/index.html", encoding="utf-8").read()
    assert html.count("</html>") == 1
    for token in ("btn-premium.loading", "@keyframes spin", "skel", "setBtnLoading", "showToast"):
        assert token in html


def test_v28_interface_hooks():
    """v2.8: continuous-trading strip, terminal preview, brokers & wallets UI."""
    html = open("public/index.html", encoding="utf-8").read()
    for token in (
        # continuous-trading strip
        "trading-active-badge", "trades-today-count", "next-scan-countdown",
        'data-i18n="tradingState"', 'data-i18n="nextScan"', 'data-i18n="tradesToday"',
        # trade terminal risk preview + confirmation (no alert() on that path)
        "order-preview", "op-risk", "op-pct", "op-fees", "confirm-order-btn",
        "computeOrderPreview", "drawSlTpPreview", "sltp-preview",
        "manual-order-error", "tapToConfirm",
        # terminal positions + order history
        "terminal-pos-list", "terminal-orders-body", "closeTerminalPosition",
        "setTerminalOrdersFilter", "sortTerminalOrders",
        # brokers
        "passphrase-field", "broker-sandbox-toggle", "sandbox-badge",
        "test-connection-btn", "broker-test-result", "runtimeBadge",
        "/brokers/test", "testBrokerConnection",
        # wallets (watch-only)
        'data-i18n="addWatchOnly"', 'data-i18n="watchOnlyBadge"',
        "wallet-chain", "wallet-address-hint", "validateWalletAddress",
        "copyWalletAddress", "deleteWalletConfirm", 'data-wbal=',
        "/wallet-balances", "/qr\"",
    ):
        assert token in html, f"missing UI hook: {token}"
    # the manual order flow must not use blocking alert()/confirm() dialogs
    import re
    manual = re.search(r"async function manualTrade\(direction\).*?\n        \}\n", html, re.S)
    assert manual, "manualTrade not found"
    assert "alert(" not in manual.group(0)
    assert "confirm(" not in manual.group(0)
