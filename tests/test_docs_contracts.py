def _read(path):
    return open(path, encoding="utf-8").read()


def test_docs_mention_v23_contracts():
    cl = _read("CHANGELOG.md")
    assert "[2.3.0]" in cl
    assert "Radar" in cl or "radar" in cl.lower()
    readme = _read("README.md")
    assert "v2.3" in readme
    api = _read("docs/api_contract.md")
    for token in ("execution_intent", "sort=score", "execute-signal", "orderbook",
                  "ohlcv", "Parameters deployed live", '"10"', "language", "data_age_ms"):
        assert token in api, token
    audit = _read("docs/AUDIT_ROBUSTESSE.md")
    assert "test_radar.py" in audit and "test_institutional.py" in audit
