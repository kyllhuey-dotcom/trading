#!/bin/bash
set -e

echo "=== QUANTUM TRADE PRO VALIDATION PIPELINE ==="

# 1. Install dependencies
echo "[1/5] Installing dependencies..."
pip install -q -r requirements.txt -r requirements-dev.txt

# 2. Check for secrets in code
echo "[2/5] Scanning for hardcoded secrets..."
if grep -rE "(api_key|api_secret|password|token)\s*=\s*\"[a-zA-Z0-9]{10,}\"" api/ --include="*.py" | grep -v "os.getenv"; then
    echo "ERROR: Hardcoded secret detected!"
    exit 1
fi
echo "  - No hardcoded secrets found."

# 3. Full test suite + global coverage gate (60%)
echo "[3/5] Running automated tests (global coverage gate 60%)..."
export TESTING=true
pytest tests/ --cov=api --cov-fail-under=60 -q

# 4. Critical engines coverage gate (80%) — the trading core must stay covered
echo "[4/5] Critical engines coverage gate (80%)..."
pytest tests/ --cov=api/engines --cov-fail-under=80 -q

# 5. App entry point check (all routes registered)
echo "[5/5] Checking application entry point..."
python3 -c "from api.index import app; print(f'App OK — {len(app.routes)} routes')"

echo ""
echo "VALIDATION SUCCESSFUL: Ready for deployment."
