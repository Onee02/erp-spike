"""Deterministic INSERT-only fixtures for fresh, isolated worker-test databases."""


def sql(service, small_orders=3, whale_orders=200):
    if service not in {"db", "db_whale"} or min(small_orders, whale_orders) < 1:
        raise ValueError("Invalid fixture parameters")
    if service == "db":
        tenants = "SELECT n, 'Small Co ' || n, 'small' FROM generate_series(2, 301) n"
        count = small_orders
    else:
        tenants = "VALUES (1, 'Whale Distribution', 'enterprise')"
        count = whale_orders
    return f"""
INSERT INTO tenants (id, name, plan) {tenants};
INSERT INTO orders (tenant_id, order_date, status, total)
SELECT t.id, DATE '2026-01-01' + ((g - 1) % 300), 'paid', 100.00
FROM tenants t CROSS JOIN generate_series(1, {count}) g ORDER BY t.id, g;
INSERT INTO order_lines (tenant_id, order_id, product, qty, unit_price)
SELECT o.tenant_id, o.id, 'SKU-' || (1 + ((o.id + n) % 200)), 1, 25.00
FROM orders o CROSS JOIN generate_series(1, 4) n ORDER BY o.id, n;
ANALYZE;
"""
