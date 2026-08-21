import httpx
import time
import sys

def run_smoke_test(url="http://localhost:8000"):
    print(f"Starting post-deployment smoke test on {url}...")
    try:
        # 1. Healthz check
        res = httpx.get(f"{url}/healthz")
        if res.status_code == 200 and res.json().get("status") == "OK":
            print("  [PASS] /healthz is alive.")
        else:
            print(f"  [FAIL] /healthz failed: {res.status_code} {res.text}")
            return False

        # 2. Status check
        res = httpx.get(f"{url}/api/status?market_id=btc_usdt")
        if res.status_code == 200:
            print("  [PASS] /api/status is responsive.")
        else:
            print(f"  [FAIL] /api/status failed: {res.status_code}")
            return False

        # 3. Market Hub check
        res = httpx.get(f"{url}/api/markets")
        if res.status_code == 200:
            print("  [PASS] /api/markets is providing data.")
        else:
            print(f"  [FAIL] /api/markets failed.")
            return False

        print("\nSMOKE TEST SUCCESSFUL: Environment is stable.")
        return True
    except Exception as e:
        print(f"  [ERROR] Smoke test crashed: {e}")
        return False

if __name__ == "__main__":
    host = "http://localhost:8000"
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if not run_smoke_test(host):
        sys.exit(1)
