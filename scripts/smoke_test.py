import httpx
import time
import sys


def run_smoke_test(url="http://localhost:8000"):
    print(f"Starting post-deployment smoke test on {url}...")
    checks = [
        ("/healthz", "GET", None, None),
        ("/api/status?market_id=btc_usdt", "GET", None, None),
        ("/api/markets", "GET", None, None),
        ("/api/scanner", "GET", None, None),
        ("/api/settings", "GET", None, None),
        ("/api/brokers", "GET", None, None),
        ("/api/wallets", "GET", None, None),
        ("/api/performance?mode=DEMO", "GET", None, None),
        ("/api/metrics", "GET", None, None),
        ("/api/health", "GET", None, None),
    ]
    failures = 0
    for path, method, _, _ in checks:
        try:
            res = getattr(httpx, method.lower())(f"{url}{path}", timeout=30.0)
            if res.status_code == 200:
                print(f"  [PASS] {method} {path} -> 200")
            else:
                print(f"  [FAIL] {method} {path} -> {res.status_code}")
                failures += 1
        except Exception as e:
            print(f"  [FAIL] {method} {path} -> {e}")
            failures += 1

    if failures == 0:
        print("\nSMOKE TEST PASSED: all core endpoints are responsive.")
        return True
    print(f"\nSMOKE TEST FAILED: {failures} endpoint(s) unhealthy.")
    return False


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    sys.exit(0 if run_smoke_test(url) else 1)
