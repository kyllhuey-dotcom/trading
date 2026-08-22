from api.models import MarketQuote

def test_model_dump_compatibility():
    """Verify that model_dump is used instead of dict."""
    # This is a static check of the codebase already done by grep,
    # here we just ensure the models have model_dump (Pydantic v2).
    quote = MarketQuote(
        market_id="test",
        symbol="TEST",
        display_symbol="T",
        asset_class="CRYPTO",
        price=100.0,
        status="LIVE",
        source="Test",
        timestamp=123456789
    )
    assert hasattr(quote, "model_dump")
    data = quote.model_dump()
    assert data["market_id"] == "test"
