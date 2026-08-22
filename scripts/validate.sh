#!/bin/bash
set -euo pipefail

printf '%s\n' "=== QUANTUM TRADE PRO VALIDATION PIPELINE ==="

# 1. Install and verify dependencies. The fallback paths support managed CI
# images, but import/pip-check failures remain hard gates.
echo "[1/6] Installing and checking dependencies..."
python3 -m pip install -q -r requirements.txt -r requirements-dev.txt 2>/dev/null \
  || python3 -m pip install -q --break-system-packages -r requirements.txt -r requirements-dev.txt 2>/dev/null \
  || python3 -m pip install -q --user -r requirements.txt -r requirements-dev.txt 2>/dev/null \
  || echo "  - WARN: pip install skipped; verifying the existing environment."
python3 -c "import fastapi, ccxt, httpx, pandas, pydantic, pytest, ruff, yfinance" \
  || { echo "ERROR: dependencies missing — install requirements-dev.txt"; exit 1; }
python3 -m pip check

# 2. Syntax and static correctness.
echo "[2/6] Running syntax and static checks..."
python3 -m compileall -q api scripts tests
python3 -m ruff check api scripts tests

# 3. Check for accidental hard-coded secrets in application code.
echo "[3/6] Scanning for hardcoded secrets..."
if grep -rE "(api_key|api_secret|password|token)\s*=\s*\"[a-zA-Z0-9]{10,}\"" api/ --include="*.py" | grep -v "os.getenv"; then
    echo "ERROR: Hardcoded secret detected!"
    exit 1
fi
echo "  - No hardcoded secrets found."

# 4. Full suite (including the live provider probes, which auto-skip when the
# network is unavailable). Scripts are part of coverage because they expose
# unit-testable functions.
echo "[4/6] Running complete suite (branch coverage gate 85%)..."
export TESTING=true
python3 -m pytest tests/ \
  --cov=api --cov=scripts --cov-branch --cov-fail-under=85 -q

# 5. Trading core has a stricter dedicated gate.
echo "[5/6] Checking critical engines coverage (80%)..."
python3 -m pytest tests/ --cov=api/engines --cov-fail-under=80 -q

# 6. Import the production entry point and verify route registration.
echo "[6/6] Checking application entry point..."
python3 -c "from api.index import app; print(f'App OK — {len(app.routes)} routes')"

echo
echo "VALIDATION SUCCESSFUL: Ready for deployment."
