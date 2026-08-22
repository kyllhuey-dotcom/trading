"""Provider priority: Binance → Bybit → Gate → rest (LOT 7)."""
from typing import Any, Iterable, List, Tuple

PRIORITY = {"binance": 0, "bybit": 1, "gate": 2}


def prioritize_providers(items: Iterable) -> List[Tuple[str, Any]]:
    seq = list(items or [])
    # accept dict.items() or list of pairs
    pairs = []
    for it in seq:
        if isinstance(it, tuple) and len(it) == 2:
            pairs.append(it)
        else:
            continue
    return sorted(pairs, key=lambda kv: PRIORITY.get(str(kv[0]).lower(), 50))
