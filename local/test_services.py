"""
ローカル動作確認スクリプト
使い方: python local/test_services.py
前提: docker compose -f local/docker-compose.services.yml up --build -d
"""

import json
import sys
import urllib.request
import urllib.error

BASE_A = "http://localhost:8080"
BASE_B = "http://localhost:8001"
BASE_C = "http://localhost:8002"
BASE_D = "http://localhost:8003"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def check(label: str, status: int, body: dict, expect_status: int = 200, check_keys: list[str] | None = None):
    ok = status == expect_status
    if ok and check_keys:
        ok = all(k in body for k in check_keys)
    icon = PASS if ok else FAIL
    print(f"  {icon}  [{status}] {label}")
    if not ok:
        print(f"         body={json.dumps(body)[:120]}")
    return ok


results = []

print("\n=== Health checks ===")
for name, base in [("service-a", BASE_A), ("service-b", BASE_B), ("service-c", BASE_C), ("service-d", BASE_D)]:
    s, b = get(f"{base}/health")
    results.append(check(f"{name} /health", s, b, check_keys=["status"]))

print("\n=== service-c: /reviews/{product_id} ===")
s, b = get(f"{BASE_C}/reviews/p-001")
results.append(check("p-001 reviews (fresh)", s, b, check_keys=["rating_distribution", "recommendations"]))

s, b = get(f"{BASE_C}/reviews/p-999")
results.append(check("p-999 reviews (404)", s, b, expect_status=404))

print("\n=== service-d: /inventory/{product_id} ===")
s, b = get(f"{BASE_D}/inventory/p-002")
results.append(check("p-002 inventory (fresh)", s, b, check_keys=["stock", "price", "available"]))

s, b = get(f"{BASE_D}/inventory/p-999")
results.append(check("p-999 inventory (404)", s, b, expect_status=404))

print("\n=== service-b: /products/{product_id} (b→c chain) ===")
s, b = get(f"{BASE_B}/products/p-001")
results.append(check("p-001 product (b→c fresh, has reviews)", s, b, check_keys=["name", "reviews"]))
if "reviews" in b and b["reviews"]:
    print(f"         reviews._source={b['reviews'].get('_source')} keywords={b['reviews'].get('top_keywords', [])[:2]}")

s, b = get(f"{BASE_B}/products/p-002")
results.append(check("p-002 product (has sale_price in reviews path)", s, b))

print("\n=== service-a: /aggregate/products/{product_id} (a→{b→c, d} topology) ===")
s, b = get(f"{BASE_A}/aggregate/products/p-001")
results.append(check(
    "p-001 aggregate (product + inventory)",
    s, b, check_keys=["product", "inventory", "resilience"],
))
if s == 200:
    r = b.get("resilience", {})
    print(f"         product_source={r.get('product_source')}  inventory_source={r.get('inventory_source')}")
    print(f"         b_latency={r.get('service_b_latency_ms')}ms  d_latency={r.get('service_d_latency_ms')}ms")
    print(f"         circuit_state={r.get('circuit_state')}  inventory_circuit={r.get('inventory_circuit_state')}")
    inv = b.get("inventory", {})
    if inv:
        print(f"         inventory: stock={inv.get('stock')} price={inv.get('price')} sale_price={inv.get('sale_price')}")

print("\n=== fault injection: LATENCY_MS on service-c ===")
print("  (service-c に LATENCY_MS=200ms を注入すると service-b の 100ms timeout で reviews=null になることを確認)")
print("  手動: docker compose exec service-c env LATENCY_MS=200 を確認するには restart が必要")

print("\n=== aggregate endpoint with service-a /aggregate/{item_id} ===")
s, b = get(f"{BASE_A}/aggregate/1")
results.append(check("aggregate item_id=1", s, b))

total = len(results)
passed = sum(results)
print(f"\n{'='*40}")
print(f"Result: {passed}/{total} passed")
if passed < total:
    sys.exit(1)
