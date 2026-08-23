"""Coinbase public (no-key) crypto market data."""
import ccxt.async_support as ccxt

from .public_ccxt_provider import PublicCCXTProvider


class CoinbaseProvider(PublicCCXTProvider):
    def __init__(self):
        super().__init__(ccxt.coinbase({"enableRateLimit": True}), "Coinbase")
