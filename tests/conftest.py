"""
Global test configuration.

IMPORTANT: sets DB_PATH to a temporary database BEFORE any test module
imports api.index, so the test suite NEVER touches the production database.
"""
import os
import pathlib
import tempfile

_TEST_DB_DIR = pathlib.Path(tempfile.gettempdir()) / "qtp_test_suite"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)

os.environ["DB_PATH"] = str(_TEST_DB_DIR / "quantum_trade_test.db")
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("FERNET_KEY", "")  # keep plaintext in tests unless a test overrides it
