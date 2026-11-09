# Mini-ERP multi-tenant base (spike starter)

A deliberately simple multi-tenant "ERP" for the exercise *Refinement of the Whale*.
It reproduces the situation from the exercise: **300 small tenants + 1 big customer
(tenant 1) on one shared PostgreSQL and one API process**. It is not a product.
It is a thing you can measure, break, and modify in a spike.

```
 loadtest.py ──HTTP──▶  FastAPI (1 process, 1 connection pool, max 10)  ──▶  PostgreSQL (shared)
  small tenants: GET/POST /t/{id}/orders      │                                orders, order_lines
  whale: GET /t/1/reports/monthly (heavy)     └─ /admin/metrics (p95 whale vs. small tenants)
```

## Quick start

```bash
docker compose up -d --build                  # Postgres + API on :8000
docker compose run --rm api python -m app.seed   # 300 small tenants + whale (+35% data)
curl localhost:8000/t/5/orders?limit=3        # a small tenant
curl localhost:8000/t/1/reports/monthly       # the heavy month-end report

pip install httpx                             # for the load test (runs on your machine)
python loadtest/loadtest.py --label baseline  # results saved to results/baseline.json
curl localhost:8000/admin/metrics             # whale vs. small tenants, pool pressure
python cost_model.py                          # margin per option
```

**Read the baseline first.** The load test runs two phases: small tenants alone, then
small tenants + whale report workers. The key number is the p95 of *small tenants*
between the two phases. In our reference run it grew about 6x (roughly 17 ms to 100 ms)
while nobody changed anything. Your numbers will differ with your laptop.

## What is deliberately naive

- One API process, one connection pool (`POOL_MAX=10`) shared by all tenants.
- One database, tenant separation only through the `tenant_id` column.
- Heavy report runs on the same database as the OLTP traffic.
- No limits, no throttling, no caching. Tenant comes from the URL, not from a token.

## Where to hook in

| Hook | File | What is already there |
|---|---|---|
| Tenant routing | `app/db.py` | `TENANT_DSN_MAP` sends chosen tenants to another database |
| Guardrail | `app/guardrail.py` | `check()` runs before every tenant endpoint and does nothing yet |
| Heavy query | `app/main.py` `monthly_report` | plain aggregate over `orders` + `order_lines` |
| Metrics | `app/guardrail.py`, `/admin/metrics` | p50/p95/p99 whale vs. small, pool `requests_waiting` |
| Cost | `cost_model.py` | edit the numbers, replace estimates with what you measured |

## Spike ideas (pick one question, do not try them all)

- **A. Dedicated database for the whale.** Uncomment `db_whale` and `TENANT_DSN_MAP` in
  `docker-compose.yml`, apply `db/init.sql` to it, and load the whale's data into it.
  Does the small-tenant p95 recover? Note: a second database on the *same* server shares
  CPU and disk, and the API process and its pool are still shared. Find out what is
  really isolated and what is not.
- **B. Per-tenant guardrail.** Implement `check()`: a concurrency cap or rate limit per
  tenant, maybe a separate budget for heavy reports. What happens to small-tenant p95,
  and what does the whale experience (latency, 429s)? Is that acceptable under a 99.9% SLA?
- **C. Make the report cheaper.** Pre-aggregation, materialized view, index change,
  or read replica. How much DB time does one report take before and after, and what
  does freshness cost you?
- **D. Bulkhead the pool.** Give reports their own small connection pool so they cannot
  starve normal requests. Cheapest fix, but what does it not fix?
- **E. What does it cost?** Combine any of the above with `cost_model.py` and your
  measurements to check whether the 60% margin survives.

## Rules of the spike

1. State the question and the no-go threshold **before** you change code.
2. Do not change `loadtest.py` between runs. Change one thing at a time.
3. Save every run with `--label`, so the before/after comparison is in `results/`.
4. Time-box it. A clear "we do not know, because..." is a valid result.

## 5-minute presentation

Question and hypothesis; what you changed (show the diff, not the whole code);
before/after table (small-tenant p95, whale report time, errors);
monthly cost impact; verdict: go / no-go / go-if, and which assumption is still open.
