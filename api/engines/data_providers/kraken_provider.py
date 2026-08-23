"""Kraken public (no-key) crypto market data."""
import ccxt.async_support as ccxt

from .public_ccxt_provider import PublicCCXTProvider


class KrakenProvider(PublicCCXTProvider):
    def __init__(self):
        super().__init__(ccxt.kraken({"enableRateLimit": True}), "Kraken")
