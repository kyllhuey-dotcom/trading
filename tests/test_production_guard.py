"""Production fail-fast security guard."""
import pytest

import api.index as idx


def test_prod_without_admin_key_raises(monkeypatch):
    monkeypatch.setattr(idx, "IS_PRODUCTION", True)
    monkeypatch.setattr(idx, "ADMIN_API_KEY", "")
    monkeypatch.setenv("FERNET_KEY", "dummy")
    with pytest.raises(RuntimeError, match="ADMIN_API_KEY is required in production"):
        idx.assert_production_security()


def test_prod_without_fernet_key_raises(monkeypatch):
    monkeypatch.setattr(idx, "IS_PRODUCTION", True)
    monkeypatch.setattr(idx, "ADMIN_API_KEY", "secret-admin")
    monkeypatch.delenv("FERNET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FERNET_KEY is required in production"):
        idx.assert_production_security()


def test_dev_without_keys_does_not_fail(monkeypatch):
    monkeypatch.setattr(idx, "IS_PRODUCTION", False)
    monkeypatch.setattr(idx, "ADMIN_API_KEY", "")
    monkeypatch.delenv("FERNET_KEY", raising=False)
    idx.assert_production_security()


def test_prod_with_both_keys_ok(monkeypatch):
    monkeypatch.setattr(idx, "IS_PRODUCTION", True)
    monkeypatch.setattr(idx, "ADMIN_API_KEY", "secret-admin")
    monkeypatch.setenv("FERNET_KEY", "dummy-fernet")
    idx.assert_production_security()
