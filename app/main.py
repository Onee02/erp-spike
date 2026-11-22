"""Mini-ERP, multi-tenant, shared stack. Deliberately simple: a base for spikes.

Tenant comes from the URL (/t/{tenant_id}/...) to keep the code readable.
In a real system it would come from the auth token, never from the client.
"""
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, guardrail, simulation, whale, worker

app = FastAPI(title="Mini-ERP multi-tenant base (spike starter)")


@app.middleware("http")
async def measure(request: Request, call_next):
    tenant_id = guardrail.tenant_from_path(request.url.path)
    start = time.perf_counter()
    try:
        if tenant_id is not None:
            worker.require_tenant(tenant_id)
        if request.url.path.startswith(("/admin/whale", "/admin/simulation")):
            worker.require_whale()
    except HTTPException as exc:
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    else:
        try:
            response = await call_next(request)
        except Exception:
            if tenant_id is not None:
                guardrail.record(tenant_id, (time.perf_counter() - start) * 1000, 500)
            raise
    response.headers["X-ERP-Worker"] = worker.GROUP
    response.headers["X-ERP-Instance"] = worker.INSTANCE
    if tenant_id is not None:
        guardrail.record(tenant_id, (time.perf_counter() - start) * 1000, response.status_code)
    return response


class Line(BaseModel):
    product: str
    qty: int = Field(gt=0)
    unit_price: Decimal = Field(gt=0)


class OrderIn(BaseModel):
    lines: list[Line] = Field(min_length=1)


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def page_client():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/ops", include_in_schema=False)
def page_ops():
    return FileResponse(STATIC_DIR / "ops.html")


@app.get("/api/tenants")
def list_tenants():
    """The shared UI must not depend on the whale database being available."""
    directory = [{"id": 1, "name": "Whale Distribution", "plan": "enterprise"}]
    if worker.GROUP != "whale":
        with db.tenant_conn(2) as conn:
            directory += conn.execute(
                "SELECT id, name, plan FROM tenants WHERE id BETWEEN 2 AND 301 ORDER BY id"
            ).fetchall()
    return directory


@app.get("/health")
async def health():
    """Process liveness; deliberately does not query either database."""
    return {"ok": True, "worker": worker.info()}


@app.get("/t/{tenant_id}/orders")
def list_orders(tenant_id: int = Depends(guardrail.check), limit: int = Query(50, le=500)):
    """Light, OLTP-style query: what small tenants do all day."""
    with db.tenant_conn(tenant_id) as conn:
        return conn.execute(
            "SELECT id, order_date, status, total FROM orders "
            "WHERE tenant_id = %s ORDER BY order_date DESC, id DESC LIMIT %s",
            (tenant_id, limit),
        ).fetchall()


@app.post("/t/{tenant_id}/orders", status_code=201)
def create_order(order: OrderIn, tenant_id: int = Depends(guardrail.check)):
    total = sum(l.qty * l.unit_price for l in order.lines)
    try:
        with db.tenant_conn(tenant_id) as conn:
            row = conn.execute(
                "INSERT INTO orders (tenant_id, order_date, status, total) "
                "VALUES (%s, CURRENT_DATE, 'new', %s) RETURNING id",
                (tenant_id, total),
            ).fetchone()
            conn.cursor().executemany(
                "INSERT INTO order_lines (tenant_id, order_id, product, qty, unit_price) "
                "VALUES (%s, %s, %s, %s, %s)",
                [(tenant_id, row["id"], l.product, l.qty, l.unit_price) for l in order.lines],
            )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(status_code=404, detail="unknown tenant")
    return {"id": row["id"], "total": total}


@app.get("/t/{tenant_id}/reports/monthly")
def monthly_report(tenant_id: int = Depends(guardrail.check), year: int = 2026):
    """Heavy, OLAP-style query: the month-end report that slowed everyone down."""
    with db.tenant_conn(tenant_id) as conn:
        return conn.execute(
            """
            SELECT date_trunc('month', o.order_date)::date AS month,
                   l.product,
                   sum(l.qty * l.unit_price) AS revenue,
                   sum(l.qty)                AS units
            FROM orders o
            JOIN order_lines l ON l.order_id = o.id AND l.tenant_id = o.tenant_id
            WHERE o.tenant_id = %s AND o.order_date >= %s AND o.order_date < %s
            GROUP BY 1, 2
            ORDER BY 1, revenue DESC
            LIMIT 200
            """,
            (tenant_id, date(year, 1, 1), date(year + 1, 1, 1)),
        ).fetchall()


def local_metrics():
    """Whale vs. small tenants latency + connection pool pressure."""
    return {"worker": worker.info(), "sample_scope": "last 1000 requests per tenant in this process",
            **guardrail.snapshot(), "pools": db.pool_stats()}


@app.get("/admin/metrics")
async def metrics():
    return local_metrics()


@app.get("/admin/metrics/{group}")
async def group_metrics(group: str):
    check_metrics_group(group)
    return local_metrics()


def check_metrics_group(group: str):
    if group not in {"small", "whale"}:
        raise HTTPException(status_code=404, detail="unknown worker group")
    worker.require_tenant(1 if group == "whale" else 2)


@app.post("/admin/metrics/reset")
async def metrics_reset():
    guardrail.reset()
    return {"reset": True}


@app.post("/admin/metrics/{group}/reset")
async def group_metrics_reset(group: str):
    check_metrics_group(group)
    guardrail.reset()
    return {"reset": True, "worker": worker.info()}


@app.get("/admin/whale")
def whale_status():
    return whale.status()


@app.post("/admin/whale/remove")
def whale_remove():
    whale.remove()
    return whale.status()


@app.post("/admin/whale/add")
def whale_add():
    whale.add()
    return whale.status()


@app.get("/admin/simulation")
async def simulation_status():
    return simulation.status()


@app.post("/admin/simulation/start")
async def simulation_start():
    simulation.start()
    return simulation.status()


@app.post("/admin/simulation/stop")
async def simulation_stop():
    simulation.stop()
    return simulation.status()
