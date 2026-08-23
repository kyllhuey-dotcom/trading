"""OKX public (no-key) crypto market data."""
import ccxt.async_support as ccxt

from .public_ccxt_provider import PublicCCXTProvider


class OKXProvider(PublicCCXTProvider):
    def __init__(self):
        super().__init__(ccxt.okx({"enableRateLimit": True}), "OKX")


OkxProvider = OKXProvider
