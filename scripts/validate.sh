#!/bin/bash
set -e

echo "=== QUANTUM TRADE PRO VALIDATION PIPELINE ==="

# 1. Install dependencies
echo "[1/4] Installing dependencies..."
pip install -q -r requirements.txt

# 2. Check for secrets in code
echo "[2/4] Scanning for hardcoded secrets..."
# Search for patterns like api_key = "..." or secret = "..."
if grep -rE "api_key\s*=\s*\"[a-zA-Z0-9]{10,}\"" api/ | grep -v "os.getenv"; then
    echo "ERROR: Hardcoded API Key detected!"
    exit 1
fi
echo "  - No hardcoded secrets found."

# 3. Run Test Suite
echo "[3/4] Running automated tests..."
export TESTING=true
pytest tests/ -v

# 4. Mock Build check (Check if app starts)
echo "[4/4] Checking application entry point..."
python3 -c "from api.index import app; print('App import successful')"

echo ""
echo "VALIDATION SUCCESSFUL: Ready for deployment."
