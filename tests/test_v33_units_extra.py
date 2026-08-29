"""v3.3 — branch-level unit tests for the new v3.3 engine modules.

Covers the defensive branches left uncovered by the behavioural tests:
pnl_engine.normalize_fill, protection_state liveness/naked vocabulary and
api.security fail-fast / session-token / lockout edge cases.
"""
import time as _time

import pytest

from api import security
from api.engines import pnl_engine, protection_state as ps


# --------------------------------------------------------------------------- #
# pnl_engine                                                                   #
# --------------------------------------------------------------------------- #

def test_normalize_fill_all_shapes():
    # dict fees + closed status + id
    f = pnl_engine.normalize_fill({
        "id": "o1", "fees": {"cost": "1.5", "currency": "USDT"},
        "filled": "0.5", "average": 100, "status": "closed",
    })
    assert f["order_id"] == "o1"
    assert f["fees"] == 1.5
    assert f["filled"] == 0.5
    assert f["average"] == 100.0
    assert f["status"] == "FILLED"

    # scalar fee + price fallback + unknown status
    f = pnl_engine.normalize_fill({
        "order_id": "o2", "fee": 2, "price": 50, "filled": 1, "status": None,
    })
    assert f["order_id"] == "o2"
    assert f["fees"] == 2.0
    assert f["average"] == 50.0
    assert f["status"] == "UNKNOWN"

    # every invalid numeric falls back to 0 (never raises)
    f = pnl_engine.normalize_fill({
        "fees": "abc", "filled": "zzz", "average": "qqq",
        "clientOrderId": "c9", "broker_order_id": "b7",
    })
    assert f["fees"] == 0.0
    assert f["filled"] == 0.0
    assert f["average"] == 0.0
    assert f["client_order_id"] == "c9"
    assert f["order_id"] == "b7"

    # None input is safe
    f = pnl_engine.normalize_fill(None)
    assert f["filled"] == 0.0 and f["fees"] == 0.0 and f["order_id"] is None


def test_residual_quantity_lot_tolerance_and_fee_portion():
    assert pnl_engine.residual_quantity(1.0, 1.0) == 0.0
    assert pnl_engine.residual_quantity(1.0, 0.5, lot_size=0.01) == 0.5
    assert pnl_engine.residual_quantity(1.0, 1.0 - 1e-9, lot_size=0.01) == 0.0
    assert pnl_engine.lot_tolerance(0.01) == 0.01
    assert pnl_engine.is_fully_closed(0.005, 0.01) is True
    assert pnl_engine.is_fully_closed(0.5, 0.01) is False
    # no total fill info -> the whole fee is accounted on this leg
    assert pnl_engine.fee_portion(10.0, 0.0, 0.0) == 10.0
    assert pnl_engine.fee_portion(10.0, 0.5, 2.0) == 2.5
    # over-filled ratio is clamped to 1
    assert pnl_engine.fee_portion(4.0, 5.0, 2.0) == 4.0


# --------------------------------------------------------------------------- #
# protection_state                                                             #
# --------------------------------------------------------------------------- #

def test_normalize_order_status_vocabulary():
    assert ps.normalize_order_status(None) == "UNKNOWN"
    assert ps.normalize_order_status("closed") == "FILLED"
    assert ps.normalize_order_status(" new ") == "OPEN"
    assert ps.normalize_order_status("partially_filled") == "PARTIALLY_FILLED"
    assert ps.normalize_order_status("totally-unknown") == "UNKNOWN"


def test_protection_liveness_all_verdicts():
    now = 1000.0
    # dead protection -> NAKED (legacy flag or explicit status)
    assert ps.protection_liveness({"sl_tp_failed": True}, now=now) == "NAKED"
    assert ps.protection_liveness({"protection_status": "NAKED"}, now=now) == "NAKED"
    for status in ("CANCELED", "EXPIRED", "REJECTED"):
        assert ps.protection_liveness(
            {"protection_status": status}, now=now) == "NAKED"
    # filled protection -> FILLED
    assert ps.protection_liveness({"protection_status": "FILLED"}, now=now) == "FILLED"
    # fresh open -> ALIVE (already exercised) ; stale open falls through
    assert ps.protection_liveness(
        {"protection_status": "OPEN", "protection_checked_at": 0.0},
        now=now) == "UNKNOWN"
    # too many consecutive errors -> UNKNOWN even with a recorded status
    assert ps.protection_liveness(
        {"protection_status": "OPEN", "protection_error_count": 3},
        now=now) == "UNKNOWN"
    # never checked at all -> UNKNOWN
    assert ps.protection_liveness({}, now=now) == "UNKNOWN"


def test_is_naked_and_has_any_protection():
    assert ps.is_naked(None) is True
    assert ps.is_naked({}) is True
    assert ps.is_naked({"sl_tp_failed": True}) is True
    assert ps.is_naked({"protection_status": "NAKED"}) is True
    assert ps.is_naked({"sl_order_id": "sl-1"}) is False
    assert ps.is_naked({"tp_order_status": "open"}) is False
    assert ps.has_any_protection({"tp_order_id": "tp-1"}) is True
    assert ps.has_any_protection({"sl_order_status": "open"}) is True
    assert ps.has_any_protection({}) is False


# --------------------------------------------------------------------------- #
# security — fail-fast, session tokens, client id, lockout                     #
# --------------------------------------------------------------------------- #

def test_weak_key_digit_and_repeated_chars():
    assert security.is_weak_key("1" * 20) is True          # digits
    assert security.is_weak_key("ab" * 10) is True         # <= 2 distinct chars
    assert security.is_weak_key("strong" * 3 + "xyz") is False


def test_validate_fernet_key_branches():
    from cryptography.fernet import Fernet
    good = Fernet.generate_key().decode()
    assert security.validate_fernet_key(good) is True
    assert security.validate_fernet_key(None) is False
    assert security.validate_fernet_key("") is False
    assert security.validate_fernet_key("not-a-fernet-key") is False


def test_production_config_errors_branches():
    from cryptography.fernet import Fernet
    good = Fernet.generate_key().decode()
    strong = "Str0ng!Passw0rd#Key"
    assert security.production_config_errors("production") == [
        "ADMIN_API_KEY is missing",
        "FERNET_KEY is missing (broker secrets would be stored in plaintext)",
    ]
    assert security.production_config_errors("production", strong, good) == []
    assert security.production_config_errors("development") == []
    errs = security.production_config_errors("production", strong, "bogus")
    assert errs == ["FERNET_KEY is not a valid Fernet key (32-byte url-safe base64)"]
    errs = security.production_config_errors("production", "admin" * 4, good)
    assert errs == ["ADMIN_API_KEY is manifestly weak (>=16 non-placeholder chars required)"]


def test_assert_production_ready_raises():
    from cryptography.fernet import Fernet
    good = Fernet.generate_key().decode()
    with pytest.raises(security.ProductionConfigError):
        security.assert_production_ready("production", None, None)
    with pytest.raises(security.ProductionConfigError):
        security.assert_production_ready("production", "Str0ng!Passw0rd#Key", "bad")
    # healthy config does not raise
    security.assert_production_ready("production", "Str0ng!Passw0rd#Key", good)


def test_verify_session_token_failure_paths():
    key = "Str0ng!Passw0rd#Key"
    good = security.issue_session_token(key)
    assert security.verify_session_token(good, key) is True

    exp_s, nonce, sig = good.split(".", 2)
    assert security.verify_session_token(None, key) is False
    assert security.verify_session_token("no-dots", key) is False
    assert security.verify_session_token(f"{exp_s}.{nonce}", key) is False  # 1 dot
    # nonce too short
    assert security.verify_session_token(f"{exp_s}.ab.{sig}", key) is False
    # non-integer expiry
    assert security.verify_session_token(f"abc.{nonce}.{sig}", key) is False
    # expired token (valid signature shape, past expiry)
    old_exp = str(int(_time.time()) - 3600)
    assert security.verify_session_token(f"{old_exp}.{nonce}.{sig}", key) is False
    # bad signature
    assert security.verify_session_token(f"{exp_s}.{nonce}.{'0' * 64}", key) is False


def test_credential_is_valid_paths():
    key = "Str0ng!Passw0rd#Key"
    assert security.credential_is_valid(None, "") is True     # auth disabled
    assert security.credential_is_valid(key, key) is True     # header key
    good = security.issue_session_token(key)
    assert security.credential_is_valid(good, key) is True    # session token
    assert security.credential_is_valid("wrong", key) is False


def test_client_id_from_request_variants():
    class Req:
        def __init__(self, headers=None, host=None, broken_client=False):
            self.headers = headers or {}
            if not broken_client:
                self.client = type("C", (), {"host": host})() if host else None

    fwd = Req(headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"}, host="9.9.9.9")
    assert security.client_id_from_request(fwd, trust_proxy=True) == "1.2.3.4"
    assert security.client_id_from_request(Req(headers={}, host="9.9.9.9"),
                                           trust_proxy=True) == "9.9.9.9"
    # whitespace-only forwarded value -> unknown
    assert security.client_id_from_request(
        Req(headers={"x-forwarded-for": " , "}, host="9.9.9.9"),
        trust_proxy=True) == "unknown"
    assert security.client_id_from_request(Req(headers={}, host=None)) == "unknown"
    assert security.client_id_from_request(Req(broken_client=True)) == "unknown"

    class BoomHeaders:
        def get(self, _k):
            raise RuntimeError("headers broken")

    class NoClient:
        headers = BoomHeaders()

    assert security.client_id_from_request(NoClient(), trust_proxy=True) == "unknown"


def test_auth_guard_lockout_lifecycle():
    clock = [0.0]
    guard = security.AuthGuard(max_failures=3, lockout_s=60.0,
                               clock=lambda: clock[0])
    assert guard.is_locked("1.2.3.4") is False
    locked, remaining = guard.note_failure("1.2.3.4")
    assert (locked, remaining) == (False, 2)
    guard.note_failure("1.2.3.4")
    locked, remaining = guard.note_failure("1.2.3.4")
    assert (locked, remaining) == (True, 0)
    assert guard.is_locked("1.2.3.4") is True
    # lockout expiry releases the client and resets the failure count
    clock[0] = 100.0
    assert guard.is_locked("1.2.3.4") is False
    locked, remaining = guard.note_failure("1.2.3.4")
    assert (locked, remaining) == (False, 2)
    # a different client is unaffected
    assert guard.is_locked("5.6.7.8") is False
