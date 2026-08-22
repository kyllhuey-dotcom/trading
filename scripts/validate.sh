#!/bin/bash
set -e

echo "=== QUANTUM TRADE PRO VALIDATION PIPELINE ==="

# 1. Install dependencies
echo "[1/4] Installing dependencies..."
pip install -q -r requirements.txt -r requirements-dev.txt

# 2. Check for secrets in code
echo "[2/4] Scanning for hardcoded secrets..."
if grep -rE "(api_key|api_secret|password|token)\s*=\s*\"[a-zA-Z0-9]{10,}\"" api/ --include="*.py" | grep -v "os.getenv"; then
    echo "ERROR: Hardcoded secret detected!"
    exit 1
fi
echo "  - No hardcoded secrets found."

# 3. Run the test suite with coverage gate (60%)
echo "[3/4] Running automated tests with coverage gate..."
export TESTING=true
pytest tests/ --cov=api --cov-fail-under=60 -q

# 4. Mock build check (app imports & all routes registered)
echo "[4/4] Checking application entry point..."
python3 -c "from api.index import app; print(f'App OK — {len(app.routes)} routes')"

echo ""
echo "VALIDATION SUCCESSFUL: Ready for deployment."
