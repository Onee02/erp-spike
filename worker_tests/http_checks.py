"""Real HTTP checks, invoked only inside the disposable worker-test client."""
import argparse
import os
import re
import time

import httpx


BODY = {"lines": [{"product": "ISOLATION-TEST", "qty": 2, "unit_price": "9.99"}]}


def request(client, path, *, host="proxy", method="GET", status=200, group=None, **kwargs):
    response = client.request(method, f"http://{host}:8000{path}", **kwargs)
    if response.status_code != status:
        raise AssertionError(f"{host}{path}: expected {status}, got {response.status_code}: {response.text[:300]}")
    if group is not None and response.headers.get("x-erp-worker") != group:
        raise AssertionError(f"Wrong worker for {host}{path}: {dict(response.headers)}")
    return response


def wait_until(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise AssertionError("Timed out waiting for the expected worker state")


def small_requests(client):
    for path in ("/", "/ops", "/static/client.js", "/static/ops.js", "/health",
                 "/t/5/orders", "/t/5/reports/monthly", "/admin/metrics/small"):
        request(client, path, group="small")
    directory = request(client, "/api/tenants", group="small").json()
    assert {t["id"] for t in directory} == set(range(1, 302))
    request(client, "/t/5/orders", method="POST", json=BODY, status=201, group="small")


def suite(client):
    small = request(client, "/health", host="api", group="small").json()["worker"]
    whale = request(client, "/health", host="api_whale", group="whale").json()["worker"]
    assert small["instance"] != whale["instance"], "Workers must be independent app instances"
    small_requests(client)
    for tenant in (1, "01", "%31"):
        assert request(client, f"/t/{tenant}/orders?limit=1", group="whale").json()
    for tenant in (2, 10, 100, 301):
        response = request(client, f"/t/{tenant}/orders?limit=1", group="small")
        assert len(response.json()) == 1, "Proxy lost query parameters"
    assert request(client, "/t/1/reports/monthly?year=2026", group="whale").json()
    assert request(client, "/t/1/reports/monthly?year=2025", group="whale").json() == []
    created = request(client, "/t/1/orders", method="POST", json=BODY, status=201, group="whale").json()
    assert float(created["total"]) == 19.98
    for host, tenant in (("api", 1), ("api_whale", 5)):
        for path in ("orders", "reports/monthly"):
            request(client, f"/t/{tenant}/{path}", host=host, status=403)
        request(client, f"/t/{tenant}/orders", host=host, method="POST", json=BODY, status=403)
    assert request(client, "/admin/whale", group="whale").json()["present"]
    for action in ("add", "remove"):
        # GET proves routing without invoking the destructive demo POST actions.
        request(client, f"/admin/whale/{action}", status=405, group="whale")
        request(client, f"/admin/whale/{action}", host="api", method="POST", status=403)
    for action in ("start", "stop"):
        request(client, f"/admin/simulation/{action}", host="api", method="POST", status=403)

    for group in ("small", "whale"):
        request(client, f"/admin/metrics/{group}/reset", method="POST", group=group)
    request(client, "/admin/simulation/start", method="POST", group="whale")
    wait_until(lambda: request(client, "/admin/metrics/whale").json()["whale"]["requests"] > 0)
    small_metrics = request(client, "/admin/metrics/small").json()
    assert small_metrics["whale"]["requests"] == 0, "Simulation reached the small process"
    assert small_metrics["small_tenants"]["requests"] == 0
    request(client, "/t/5/orders", group="small")
    request(client, "/admin/simulation/stop", method="POST", group="whale")
    wait_until(lambda: not request(client, "/admin/simulation").json()["running"], seconds=70)
    for group, pool_label in (("small", "db:5432/erp"), ("whale", "db_whale:5432/erp")):
        metrics = request(client, f"/admin/metrics/{group}", group=group).json()
        assert set(metrics["pools"]) == {pool_label}, metrics["pools"]
    request(client, "/admin/metrics/small/reset", method="POST", group="small")
    assert request(client, "/admin/metrics/whale").json()["whale"]["requests"] > 0


def main():
    if not re.fullmatch(r"erp-worker-test-[0-9a-f]{12}", os.getenv("WORKER_TEST_PROJECT", "")):
        raise RuntimeError("HTTP checks may only run in the disposable worker-test project")
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("suite", "whale-stopped", "whale-db-stopped", "warmup"))
    args = parser.parse_args()
    with httpx.Client(timeout=30) as client:
        if args.phase == "suite":
            suite(client)
        elif args.phase in {"whale-stopped", "whale-db-stopped"}:
            small_requests(client)
            if args.phase == "whale-stopped":
                response = client.get("http://proxy:8000/t/1/orders")
                assert response.status_code in {502, 504}, "Whale request must fail without falling back to api"
        else:
            for tenant in (2, 5, 10, 100, 301):
                assert request(client, f"/t/{tenant}/orders").json()
            for _ in range(3):
                assert request(client, "/t/1/reports/monthly?year=2026").json()
    print(f"PASS: HTTP checks ({args.phase})")


if __name__ == "__main__":
    main()
