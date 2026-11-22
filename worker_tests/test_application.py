"""API/ownership tests using fake database connections; no live data is accessed."""
from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import db, guardrail, simulation, whale, worker
from app.main import app


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        guardrail.reset()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_wrong_group_get_post_and_report_never_open_a_pool(self):
        for group, tenant in (("small", 1), ("whale", 2), ("whale", 301)):
            with self.subTest(group=group, tenant=tenant), patch.object(worker, "GROUP", group), \
                    patch.object(db, "_pool_for") as pool:
                for method, path in (("GET", "orders"), ("POST", "orders"), ("GET", "reports/monthly")):
                    response = self.client.request(method, f"/t/{tenant}/{path}", json={"lines": []})
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.headers["x-erp-worker"], group)
                pool.assert_not_called()

    def test_database_router_and_whale_helpers_enforce_ownership(self):
        with patch.object(worker, "GROUP", "small"), patch.object(db, "_pool_for") as pool, \
                patch.object(whale.psycopg, "connect") as connect:
            for action in (lambda: db.dsn_for(1), whale.status, whale.add, whale.remove):
                with self.assertRaises(HTTPException) as exc:
                    action()
                self.assertEqual(exc.exception.status_code, 403)
            pool.assert_not_called()
            connect.assert_not_called()
        with patch.object(worker, "GROUP", "whale"), self.assertRaises(HTTPException):
            db.dsn_for(5)

    def test_small_worker_rejects_whale_admin_and_simulator_without_starting_threads(self):
        with patch.object(worker, "GROUP", "small"), patch.object(simulation, "start") as start, \
                patch.object(whale.psycopg, "connect") as connect:
            for path in ("/admin/whale", "/admin/whale/add", "/admin/whale/remove",
                         "/admin/simulation", "/admin/simulation/start", "/admin/simulation/stop"):
                method = "GET" if path in ("/admin/whale", "/admin/simulation") else "POST"
                self.assertEqual(self.client.request(method, path).status_code, 403)
            start.assert_not_called()
            connect.assert_not_called()
        with patch.object(worker, "GROUP", "small"), patch.object(simulation.threading, "Thread") as thread:
            with self.assertRaises(HTTPException):
                simulation.start()
            thread.assert_not_called()

    def test_allowed_tenants_use_parameterized_queries_and_their_own_dsn(self):
        for group, tenant in (("small", 2), ("small", 301), ("whale", 1)):
            connection = MagicMock()
            connection.execute.return_value.fetchall.return_value = []
            with patch.object(worker, "GROUP", group), patch.object(db, "DEFAULT_DSN", "own-db"), \
                    patch.object(db, "TENANT_DSN_MAP", {}), patch.object(db, "_pool_for") as pool:
                pool.return_value.connection.return_value.__enter__.return_value = connection
                self.assertEqual(self.client.get(f"/t/{tenant}/orders?limit=7").status_code, 200)
                pool.assert_called_with("own-db")
                self.assertEqual(connection.execute.call_args.args[1], (tenant, 7))
                self.assertEqual(self.client.get(f"/t/{tenant}/reports/monthly?year=2025").status_code, 200)
                self.assertEqual(connection.execute.call_args.args[1], (tenant, date(2025, 1, 1), date(2026, 1, 1)))

    def test_create_order_preserves_totals_lines_and_status(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"id": 99}
        with patch.object(worker, "GROUP", "small"), patch.object(db, "tenant_conn") as tenant_conn:
            tenant_conn.return_value.__enter__.return_value = connection
            response = self.client.post("/t/5/orders", json={"lines": [
                {"product": "SKU-1", "qty": 2, "unit_price": "9.99"}]})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(str(response.json()["total"])), Decimal("19.98"))
        self.assertEqual(connection.cursor.return_value.executemany.call_args.args[1],
                         [(5, 99, "SKU-1", 2, Decimal("9.99"))])

    def test_directory_keeps_whale_and_small_tenants_without_touching_whale_db(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [{"id": 5, "name": "Small Co 5", "plan": "small"}]
        with patch.object(worker, "GROUP", "small"), patch.object(db, "tenant_conn") as tenant_conn:
            tenant_conn.return_value.__enter__.return_value = connection
            response = self.client.get("/api/tenants")
            self.assertEqual([t["id"] for t in response.json()], [1, 5])
            tenant_conn.assert_called_once_with(2)
        with patch.object(worker, "GROUP", "whale"), patch.object(db, "tenant_conn") as tenant_conn:
            self.assertEqual([t["id"] for t in self.client.get("/api/tenants").json()], [1])
            tenant_conn.assert_not_called()

    def test_metrics_and_reset_are_local_and_group_protected(self):
        with patch.object(worker, "GROUP", "small"):
            guardrail.record(5, 12, 200)
            result = self.client.get("/admin/metrics/small").json()
            self.assertEqual(result["worker"]["group"], "small")
            self.assertEqual(result["small_tenants"]["requests"], 1)
            self.assertEqual(self.client.get("/admin/metrics/whale").status_code, 403)
            self.assertEqual(self.client.post("/admin/metrics/whale/reset").status_code, 403)
            self.assertEqual(self.client.post("/admin/metrics/small/reset").status_code, 200)
            self.assertEqual(guardrail.snapshot()["small_tenants"]["requests"], 0)

    def test_whale_demo_actions_remain_available_only_with_mocked_data_changes(self):
        with patch.object(worker, "GROUP", "whale"), patch.object(whale, "add") as add, \
                patch.object(whale, "remove") as remove, \
                patch.object(whale, "status", return_value={"present": True, "orders": 2}):
            self.assertEqual(self.client.post("/admin/whale/add").status_code, 200)
            self.assertEqual(self.client.post("/admin/whale/remove").status_code, 200)
            add.assert_called_once_with()
            remove.assert_called_once_with()

    def test_health_and_pages_work_without_a_database(self):
        with patch.object(db, "_pool_for") as pool:
            for path in ("/", "/ops", "/static/client.js", "/static/ops.js", "/health"):
                self.assertEqual(self.client.get(path).status_code, 200)
            pool.assert_not_called()

    def test_invalid_tenants_are_rejected(self):
        with patch.object(worker, "GROUP", "small"), patch.object(db, "_pool_for") as pool:
            for tenant in (0, -1, 302):
                self.assertEqual(self.client.get(f"/t/{tenant}/orders").status_code, 404)
            pool.assert_not_called()

    def test_simulation_uses_local_whale_http(self):
        with patch.object(worker, "GROUP", "whale"), patch.object(simulation, "_stop") as stop, \
                patch.object(simulation.httpx, "Client") as client:
            stop.is_set.side_effect = [False, True]
            simulation._worker()
            client.assert_called_once_with(base_url="http://127.0.0.1:8000", timeout=60)
            client.return_value.__enter__.return_value.get.assert_called_once_with(
                "/t/1/reports/monthly", params={"year": 2026})

    def test_simulation_backs_off_when_the_whale_report_fails(self):
        with patch.object(simulation, "_stop") as stop, patch.object(simulation.httpx, "Client") as client:
            stop.is_set.side_effect = [False, True]
            response = client.return_value.__enter__.return_value.get.return_value
            response.raise_for_status.side_effect = httpx.HTTPError("report failed")
            simulation._worker()
            stop.wait.assert_called_once_with(0.5)

    def test_repeated_simulation_start_does_not_duplicate_clients(self):
        with patch.object(worker, "GROUP", "whale"), patch.object(simulation, "_threads", []), \
                patch.object(simulation, "WORKERS", 2), patch.object(simulation, "_stop") as stop, \
                patch.object(simulation.threading, "Thread") as thread:
            thread.return_value.is_alive.return_value = True
            simulation.start()
            simulation.start()
            self.assertEqual(thread.call_count, 2)
            stop.clear.assert_called_once_with()
            simulation.stop()
            stop.set.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
