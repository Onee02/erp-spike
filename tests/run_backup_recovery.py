"""Run unit tests and a real backup/restore round trip on disposable PostgreSQL.

    python tests/run_backup_recovery.py

Never uses the application's Compose file or the existing seed script. All Docker
operations use a fresh project name and the two isolated tmpfs database containers.
"""
import json
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import db_backup as tool


LABEL = "org.erp-spike.backup-test"


def check_config(config, project):
    """Fail closed if the resolved Compose file could share working resources."""
    if config.get("name") != project or set(config.get("services", {})) != set(tool.SERVICES):
        raise tool.BackupError("Unexpected test project or services in Compose configuration.")
    if any(config.get(key) for key in ("volumes", "networks", "secrets", "configs")):
        raise tool.BackupError("The test stack must not define persistent/shared resources.")
    for service in config["services"].values():
        if (service.get("image") != "postgres:16" or service.get("network_mode") != "none"
                or service.get("labels", {}).get(LABEL) != "true"
                or service.get("tmpfs") != ["/var/lib/postgresql/data:size=268435456"]
                or any(service.get(key) for key in
                       ("volumes", "ports", "container_name", "build", "extends", "secrets", "configs"))):
            raise tool.BackupError("Refusing a test service without the required resource isolation.")


def check_containers(containers, project):
    """Only containers bearing this run's project and safety labels may be used/removed."""
    for container in containers:
        labels = container.get("Config", {}).get("Labels", {})
        host = container.get("HostConfig", {})
        if (labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") not in tool.SERVICES
                or labels.get(LABEL) != "true"
                or host.get("NetworkMode") != "none"
                or any(mount.get("Type") in ("volume", "bind") for mount in container.get("Mounts", []))):
            raise tool.BackupError("Container ownership/isolation check failed; refusing access or cleanup.")


def fixture_sql(service):
    tenant = 1 if service == "db_whale" else 2
    name = "Whale test fixture" if tenant == 1 else "Small test fixture"
    plan = "enterprise" if tenant == 1 else "small"
    return f"""
INSERT INTO tenants (id, name, plan) VALUES ({tenant}, '{name}', '{plan}');
INSERT INTO orders (tenant_id, order_date, status, total) VALUES
    ({tenant}, DATE '2026-01-15', 'paid', 19.98),
    ({tenant}, DATE '2026-02-16', 'new', 12.50);
INSERT INTO order_lines (tenant_id, order_id, product, qty, unit_price) VALUES
    ({tenant}, 1, 'BACKUP-FIXTURE-{service}', 2, 9.99),
    ({tenant}, 2, 'SECOND-FIXTURE-{service}', 1, 12.50);
"""


def extra_order_sql(service):
    tenant = 1 if service == "db_whale" else 2
    return f"""
WITH added AS (
    INSERT INTO orders (tenant_id, order_date, status, total)
    VALUES ({tenant}, DATE '2026-03-01', 'after-backup', 29.97) RETURNING id
)
INSERT INTO order_lines (tenant_id, order_id, product, qty, unit_price)
SELECT {tenant}, id, 'AFTER-BACKUP', 3, 9.99 FROM added;
"""


def main():
    project = "erp-backup-test-" + uuid4().hex[:12]
    compose = tool.compose_command(project)
    directory = ROOT / "backups" / "test-runs" / project
    directory.mkdir(parents=True)
    report = {"status": "running", "project": project, "steps": [],
              "started_at": datetime.now(timezone.utc).isoformat()}
    report_path = directory / "report.json"
    reserved = False
    checks_passed = False

    def save_report():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save_report()
    with (directory / "commands.log").open("w", encoding="utf-8") as log:
        def run(command, input_bytes=None, expected_failure=False):
            log.write("COMMAND: " + " ".join(map(str, command)) + "\n")
            log.flush()
            result = subprocess.run(command, cwd=ROOT, input=input_bytes, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            output = result.stdout.decode("utf-8", errors="replace")
            errors = result.stderr.decode("utf-8", errors="replace")
            log.write(output + errors + f"\nEXIT: {result.returncode}\n")
            log.flush()
            if expected_failure:
                if result.returncode == 0:
                    raise tool.BackupError("A negative test unexpectedly succeeded.")
            elif result.returncode:
                raise tool.BackupError(f"Command failed ({result.returncode}): {output}\n{errors}")
            return output

        def step(name):
            print(name, flush=True)
            report["steps"].append(name)
            save_report()

        def cli(*args, expected_failure=False):
            return run([sys.executable, str(ROOT / "scripts" / "db_backup.py"),
                        "--test-project", project, *map(str, args)], expected_failure=expected_failure)

        def sql(service, database, text):
            return run([*compose, "exec", "-T", service, "psql", "-X", "--no-password",
                        "-U", "erp", "--dbname", database, "-qAt", "-v", "ON_ERROR_STOP=1",
                        "--single-transaction", "--file", "-"], input_bytes=text.encode("utf-8"))

        def containers():
            ids = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).split()
            return [] if not ids else json.loads(run(["docker", "inspect", *ids]))

        def databases():
            return {service: sql(service, "postgres", "SELECT datname FROM pg_database ORDER BY datname;")
                    for service in tool.SERVICES}

        try:
            step("Running Python unit tests")
            run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
            step("Validating disposable Compose configuration")
            config = json.loads(run([*compose, "config", "--format", "json"]))
            check_config(config, project)
            if containers():
                raise tool.BackupError("Generated project name already exists; no containers will be changed.")
            reserved = True
            step("Starting two isolated PostgreSQL containers")
            run([*compose, "up", "-d", "--wait", "--wait-timeout", "120"])
            owned = containers()
            check_containers(owned, project)
            if len(owned) != 2:
                raise tool.BackupError("Expected exactly two disposable database containers.")

            step("Creating the schema and small synthetic fixtures (no seed script)")
            schema = (ROOT / "db" / "init.sql").read_text(encoding="utf-8")
            for service in tool.SERVICES:
                sql(service, "erp", schema + "\n" + fixture_sql(service))
            before = json.loads(cli("verify", "erp"))
            for service in tool.SERVICES:
                if [before[service][table]["rows"] for table in ("tenants", "orders", "order_lines")] != [1, 2, 2]:
                    raise tool.BackupError("Fixture row counts do not match the expected nonempty dataset.")

            step("Creating real backups of both fixture databases")
            output = cli("backup")
            backup_dir = Path(next(line.removeprefix("Backup complete: ") for line in output.splitlines()
                                   if line.startswith("Backup complete: ")))
            expected = backup_dir / "before.json"
            expected.write_text(json.dumps(before, indent=2) + "\n", encoding="utf-8")
            report["backup_directory"] = str(backup_dir)

            step("Adding post-backup records only to the disposable source databases")
            for service in tool.SERVICES:
                sql(service, "erp", extra_order_sql(service))
            live = json.loads(cli("verify", "erp"))
            if live == before:
                raise tool.BackupError("The post-backup fixture change was not detected.")

            step("Restoring both archives into new test databases")
            output = cli("restore", backup_dir)
            restored = next(line.split(": ", 1)[1] for line in output.splitlines()
                            if line.startswith("Restore complete. Test database on BOTH servers: "))
            report["restored_database"] = restored
            step("Comparing recovered rows, fingerprints, indexes, and sequences")
            cli("verify", restored, "--expected", expected)
            if json.loads(cli("verify", "erp")) != live:
                raise tool.BackupError("Restoration changed the disposable source databases.")
            step("Checking new inserts and restored ID sequences")
            for service in tool.SERVICES:
                sql(service, restored, extra_order_sql(service))
            inserted = json.loads(cli("verify", restored))
            for service in tool.SERVICES:
                if (inserted[service]["orders"]["rows"] != 3
                        or inserted[service]["order_lines"]["rows"] != 3):
                    raise tool.BackupError("Inserting into a restored database failed.")
            if inserted != live:
                raise tool.BackupError("Restored inserts do not match the expected rows and sequences.")

            step("Checking that a damaged archive is rejected before creating a database")
            # This negative test copies only this run's synthetic archives.
            corrupt = directory / "corrupt-copy"
            shutil.copytree(backup_dir, corrupt)
            with (corrupt / "db_whale.dump").open("ab") as archive:
                archive.write(b"deliberate-test-corruption")
            names_before = databases()
            rejected = cli("restore", corrupt, expected_failure=True)
            if "Restore complete" in rejected or databases() != names_before:
                raise tool.BackupError("A corrupt archive created a database or reported success.")
            checks_passed = True
        except (Exception, KeyboardInterrupt) as exc:
            report["status"] = "failed"
            report["error"] = str(exc) or "Interrupted"
            log.write(traceback.format_exc())
            print(f"TEST RUN FAILED: {report['error']}", file=sys.stderr)
        finally:
            if reserved:
                try:
                    check_containers(containers(), project)
                    step("Removing only this run's disposable containers")
                    run([*compose, "down", "--timeout", "10"])
                    if containers():
                        raise tool.BackupError("Disposable containers remain after cleanup.")
                except (Exception, KeyboardInterrupt) as exc:
                    report["status"] = "failed"
                    report["cleanup_error"] = str(exc)
                    print(f"Cleanup failed or was refused: {exc}", file=sys.stderr)
            if checks_passed and report["status"] != "failed":
                report["status"] = "passed"
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            save_report()
    print(f"Report: {report_path}")
    if report["status"] != "passed":
        return 1
    print("PASS: unit tests and the real backup/restore/verification tests completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
