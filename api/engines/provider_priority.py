"""Deterministic provider cascade used by every market-data path."""
from typing import Any, Iterable, List, Tuple

# Required crypto cascade, followed by optional keyed tradfi and keyless Yahoo.
PRIORITY = {
    "binance": 0,
    "bybit": 1,
    "okx": 2,
    "kraken": 3,
    "coinbase": 4,
    "gate": 5,
    "twelvedata": 10,
    "finnhub": 11,
}


def prioritize_providers(items: Iterable) -> List[Tuple[str, Any]]:
    pairs = []
    for item in list(items or []):
        if isinstance(item, tuple) and len(item) == 2:
            pairs.append(item)
    return sorted(pairs, key=lambda pair: PRIORITY.get(str(pair[0]).lower(), 50))
