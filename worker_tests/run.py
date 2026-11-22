"""Run isolated worker tests, optionally followed by paired performance trials.

python worker_tests/run.py
python worker_tests/run.py --performance --duration 60 --repeats 3

Requires only Python's standard library on the host, plus Docker Compose v2.
Never selects the working Compose project, runs app.seed, or reuses database data.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker_tests.fixture import sql as fixture_sql

LABEL = "org.erp-spike.worker-test"
SERVICES = {"db", "db_whale", "api", "api_whale", "proxy", "client"}


class Lab:
    def __init__(self, mode, directory):
        self.mode = mode
        self.directory = directory
        directory.mkdir(parents=True)
        self.project = "erp-worker-test-" + uuid4().hex[:12]
        self.reserved = False
        self.log = (directory / "commands.log").open("w", encoding="utf-8")
        self.env = dict(os.environ, APP_MODE="all" if mode == "shared" else "small",
                        APP_CPUS="2" if mode == "shared" else "1",
                        APP_MEMORY="1g" if mode == "shared" else "512m",
                        APP_DSN_MAP=json.dumps({"1": "postgresql://erp:worker-test-only@db_whale:5432/erp"})
                        if mode == "shared" else "{}",
                        TEST_WHALE_UPSTREAM="api:8000" if mode == "shared" else "api_whale:8000",
                        WORKER_TEST_PROJECT=self.project, WORKER_TEST_RESULTS=directory.as_posix(),
                        COMPOSE_PROFILES="", COMPOSE_DISABLE_ENV_FILE="1")
        self.compose = ["docker", "compose", "--project-name", self.project,
                        "--env-file", str(ROOT / "worker_tests" / "test.env"),
                        "-f", str(ROOT / "worker_tests" / "compose.yml"), "--profile", "tools"]
        if mode == "split":
            self.compose += ["--profile", "split"]

    def command(self, args, input_bytes=None):
        self.log.write("COMMAND: " + " ".join(map(str, args)) + "\n")
        self.log.flush()
        result = subprocess.run(args, cwd=ROOT, env=self.env, input=input_bytes,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = result.stdout.decode("utf-8", errors="replace")
        errors = result.stderr.decode("utf-8", errors="replace")
        self.log.write(output + errors + f"\nEXIT: {result.returncode}\n")
        self.log.flush()
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {output}\n{errors}")
        return output

    def dc(self, *args, input_bytes=None):
        return self.command([*self.compose, *args], input_bytes)

    def client(self, *args):
        return self.dc("run", "--rm", "--no-deps", "client", *args)

    def containers(self):
        ids = self.command(["docker", "ps", "-aq", "--filter",
                            f"label=com.docker.compose.project={self.project}"]).split()
        return [] if not ids else json.loads(self.command(["docker", "inspect", *ids]))

    def check_ownership(self):
        containers = self.containers()
        for container in containers:
            labels = container.get("Config", {}).get("Labels", {})
            service = labels.get("com.docker.compose.service")
            if (labels.get("com.docker.compose.project") != self.project
                    or labels.get(LABEL) != "true" or service not in SERVICES):
                raise RuntimeError("Refusing access/cleanup: foreign container found in test project")
            if service in {"db", "db_whale"}:
                if any(m.get("Type") != "tmpfs" for m in container.get("Mounts", [])):
                    raise RuntimeError("Refusing access/cleanup: database has persistent or shared storage")
        return containers

    def start(self, small_orders, whale_orders):
        config = json.loads(self.dc("config", "--format", "json"))
        services = config.get("services", {})
        if (config.get("name") != self.project or not set(services).issubset(SERVICES)
                or config.get("volumes") or config.get("networks", {}).get("default", {}).get("internal") is not True):
            raise RuntimeError("Unsafe disposable Compose configuration")
        for name, service in services.items():
            if service.get("ports") or service.get("container_name") or service.get("labels", {}).get(LABEL) != "true":
                raise RuntimeError("Test services must be private and marked as disposable")
            if name in {"db", "db_whale"} and (service.get("volumes")
                    or service.get("tmpfs") != ["/var/lib/postgresql/data:size=536870912"]):
                raise RuntimeError("Test databases must use only temporary memory storage")
        (self.directory / "compose.resolved.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        if self.containers():
            raise RuntimeError("Generated project name is already in use; refusing to change it")
        self.reserved = True
        self.dc("build", "api")
        self.dc("up", "-d", "--wait", "--wait-timeout", "120", "db", "db_whale")
        self.check_ownership()
        schema = (ROOT / "db" / "init.sql").read_text(encoding="utf-8")
        for service in ("db", "db_whale"):
            sql = schema + "\n" + fixture_sql(service, small_orders, whale_orders)
            self.dc("exec", "-T", service, "psql", "-X", "-U", "erp", "-d", "erp",
                    "-v", "ON_ERROR_STOP=1", "--single-transaction", "--file", "-", input_bytes=sql.encode())
        apps = ["api", "proxy"] + (["api_whale"] if self.mode == "split" else [])
        self.dc("up", "-d", "--wait", "--wait-timeout", "120", *apps)
        self.dc("exec", "-T", "proxy", "nginx", "-t")
        containers = self.check_ownership()
        metadata = []
        for container in containers:
            service = container["Config"]["Labels"]["com.docker.compose.service"]
            metadata.append({"service": service, "id": container["Id"], "image": container["Image"],
                             "host_pid": container["State"]["Pid"],
                             "cpus": container["HostConfig"]["NanoCpus"],
                             "memory": container["HostConfig"]["Memory"]})
        (self.directory / "containers.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        apps_meta = [c for c in metadata if c["service"] in {"api", "api_whale"}]
        if self.mode == "split":
            if len(apps_meta) != 2 or len({c["host_pid"] for c in apps_meta}) != 2:
                raise RuntimeError("The two APIs must run in separate containers and OS processes")
            if any(c["cpus"] != 1_000_000_000 or c["memory"] != 512 * 1024**2 for c in apps_meta):
                raise RuntimeError("Separate worker CPU/memory limits are not active")
        elif len(apps_meta) != 1 or apps_meta[0]["cpus"] != 2_000_000_000 or apps_meta[0]["memory"] != 1024**3:
            raise RuntimeError("The shared baseline must have the same total application CPU/memory budget")

    def close(self):
        if self.log.closed:
            return
        try:
            if self.reserved:
                self.check_ownership()
                self.dc("down", "--timeout", "10")
                if self.containers():
                    raise RuntimeError("Test containers remain after cleanup")
        finally:
            self.log.close()


def summarize(result):
    out = {}
    for phase in ("small_only", "with_whale"):
        samples = result[phase]["small"]
        if not samples["requests"]:
            raise RuntimeError("No small-tenant requests were measured")
        out[phase] = {key: samples[key] for key in ("requests", "p95_ms", "p99_ms", "errors")}
        out[phase]["error_rate_pct"] = round(100 * samples["errors"] / samples["requests"], 3)
    if not result["with_whale"]["whale"]["requests"]:
        raise RuntimeError("No whale report workload was measured")
    out["whale"] = result["with_whale"]["whale"]
    out["degradation_pct"] = {
        metric: round(100 * (out["with_whale"][metric] / out["small_only"][metric] - 1), 2)
        if out["small_only"][metric] else None for metric in ("p95_ms", "p99_ms")}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--performance", action="store_true")
    parser.add_argument("--duration", type=int, default=60, help="seconds per load-test phase")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--small-workers", type=int, default=10)
    parser.add_argument("--whale-workers", type=int, default=40)
    args = parser.parse_args()
    if min(args.duration, args.repeats, args.small_workers, args.whale_workers) < 1:
        parser.error("Durations and worker counts must be positive")
    directory = ROOT / "artifacts" / "worker-tests" / uuid4().hex[:12]
    directory.mkdir(parents=True)
    report = {"status": "running", "settings": vars(args), "trials": [],
              "limits": "Application budget: 2 CPUs/1 GiB total in both layouts; DBs: 2 CPUs/1 GiB each",
              "claims": "Synthetic test only; no 1200-user peak, <10% target, or 99.9% SLA is proven."}
    path = directory / "report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lab = None
    try:
        print("Running unit and HTTP isolation tests on fresh disposable databases", flush=True)
        lab = Lab("split", directory / "functional")
        lab.start(small_orders=3, whale_orders=200)
        lab.client("-m", "unittest", "discover", "-s", "worker_tests", "-p", "test_*.py", "-v")
        lab.client("-m", "worker_tests.http_checks", "suite")
        lab.dc("stop", "api_whale")
        lab.client("-m", "worker_tests.http_checks", "whale-stopped")
        lab.dc("start", "api_whale")
        lab.dc("stop", "db_whale")
        lab.client("-m", "worker_tests.http_checks", "whale-db-stopped")
        lab.close()
        lab = None
        report["functional_tests"] = "passed"
        if args.performance:
            for repeat in range(1, args.repeats + 1):
                for mode in (("shared", "split") if repeat % 2 else ("split", "shared")):
                    print(f"Measuring {mode}, trial {repeat}; fresh identical fixtures", flush=True)
                    label = f"{mode}-{repeat}"
                    lab = Lab(mode, directory / label)
                    lab.start(small_orders=300, whale_orders=31500)
                    lab.client("-m", "worker_tests.http_checks", "warmup")
                    # Run the existing load test unchanged, with a repeatable initial RNG seed.
                    lab.client("-c", "import random,runpy; random.seed(2026); runpy.run_path('loadtest/loadtest.py', run_name='__main__')",
                               "--base-url", "http://proxy:8000", "--duration", str(args.duration),
                               "--small-workers", str(args.small_workers), "--whale-workers", str(args.whale_workers),
                               "--small-tenants", "300", "--label", label)
                    result = json.loads((lab.directory / f"{label}.json").read_text(encoding="utf-8"))
                    report["trials"].append({"mode": mode, "repeat": repeat, **summarize(result)})
                    lab.close()
                    lab = None
                    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["status"] = "completed"
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = str(exc) or "Interrupted"
        report["traceback"] = traceback.format_exc()
        print(f"FAILED: {report['error']}", file=sys.stderr)
    finally:
        if lab is not None:
            try:
                lab.close()
            except Exception as exc:
                report["status"] = "failed"
                report["cleanup_error"] = str(exc)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence: {path}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
