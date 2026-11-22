"""Back up both ERP databases and restore only into newly created test databases.

Uses Python's standard library and the PostgreSQL tools in the existing containers.
Run from the host or Codespaces terminal, not from inside the API container.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("db", "db_whale")
SOURCE_DATABASE = "erp"
TEST_PROJECT = None


def compose_command(test_project=None):
    """An explicit test project always uses the separate disposable Compose file."""
    if test_project is None:
        return ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]
    if not re.fullmatch(r"erp-backup-test-[0-9a-f]{12}", test_project):
        raise BackupError("Invalid disposable test project name; refusing to use another project.")
    return ["docker", "compose", "--project-name", test_project,
            "--env-file", str(ROOT / "tests" / "backup-test.env"),
            "-f", str(ROOT / "tests" / "compose.backup-test.yml")]

# Counts and ordered row fingerprints detect missing rows and changed values.
# This is read-only, including when the requested database is the working "erp".
VERIFY_SQL = """
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';
SELECT json_build_object(
    'tenants', (SELECT json_build_object(
        'rows', count(*),
        'fingerprint', md5(coalesce(string_agg(md5(row_to_json(t)::text), '' ORDER BY id), ''))
    ) FROM public.tenants t),
    'orders', (SELECT json_build_object(
        'rows', count(*),
        'fingerprint', md5(coalesce(string_agg(md5(row_to_json(t)::text), '' ORDER BY id), ''))
    ) FROM public.orders t),
    'order_lines', (SELECT json_build_object(
        'rows', count(*),
        'fingerprint', md5(coalesce(string_agg(md5(row_to_json(t)::text), '' ORDER BY id), ''))
    ) FROM public.order_lines t),
    'indexes', (SELECT json_agg(indexdef ORDER BY tablename, indexname)
        FROM pg_indexes WHERE schemaname = 'public'
        AND tablename IN ('tenants', 'orders', 'order_lines')),
    'orders_sequence', (SELECT json_build_object('last_value', last_value, 'is_called', is_called)
        FROM public.orders_id_seq),
    'order_lines_sequence', (SELECT json_build_object('last_value', last_value, 'is_called', is_called)
        FROM public.order_lines_id_seq)
);
COMMIT;
"""


class BackupError(Exception):
    pass


def run_in(service, *command, stdin=None, stdout=None):
    """Run a command in the selected Compose service.

    PostgreSQL archives are staged as temporary files to avoid unreliable
    stdin forwarding through Docker exec.
    """
    prefix = compose_command(TEST_PROJECT)
    remote_archive = None

    if stdin is not None and command[0] == "pg_restore":
        source = getattr(stdin, "name", None)
        if not source:
            raise BackupError("Archive must be backed by a real file.")

        remote_archive = "/tmp/erp_backup_" + uuid4().hex + ".dump"
        copy_result = subprocess.run(
            [*prefix, "cp", str(Path(source).resolve()),
             f"{service}:{remote_archive}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if copy_result.returncode:
            raise BackupError(
                "Could not stage backup archive: " +
                copy_result.stderr.decode("utf-8", errors="replace")
            )

        command = (*command, remote_archive)
        stdin = None

    try:
        result = subprocess.run(
            [*prefix, "exec", "-T", service, *command],
            cwd=ROOT,
            stdin=subprocess.DEVNULL if stdin is None else stdin,
            stdout=subprocess.PIPE if stdout is None else stdout,
            stderr=subprocess.PIPE,
        )
        details = result.stderr.decode("utf-8", errors="replace").strip()
        if result.returncode:
            raise BackupError(
                f"{service}: {command[0]} failed "
                f"(exit {result.returncode}).\\n{details}"
            )
        if details:
            print(details, file=sys.stderr)
        return result.stdout
    finally:
        if remote_archive is not None:
            cleanup = subprocess.run(
                [*prefix, "exec", "-T", service, "rm", "-f",
                 remote_archive],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if cleanup.returncode:
                print("WARNING: Temporary archive cleanup failed.",
                      file=sys.stderr)


def unique_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz") + "_" + uuid4().hex[:8]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_archive(service, path):
    with path.open("rb") as archive:
        if archive.read(5) != b"PGDMP":
            raise BackupError(f"Not a PostgreSQL custom-format archive: {path}")
        archive.seek(0)
        run_in(service, "pg_restore", "--list", stdin=archive)


def backup():
    directory = ROOT / "backups" / unique_id()
    directory.mkdir(parents=True)  # Never reuse an existing backup directory.
    print(f"Creating backup in {directory}", file=sys.stderr)
    manifest = {"version": 1, "source_database": SOURCE_DATABASE, "archives": {}}
    for service in SERVICES:
        print(f"Backing up {service}/{SOURCE_DATABASE}...", file=sys.stderr)
        partial = directory / f"{service}.dump.partial"
        with partial.open("xb") as output:
            run_in(service, "pg_dump", "--no-password", "-U", "erp",
                   "-d", SOURCE_DATABASE, "--format=custom", stdout=output)
        check_archive(service, partial)
        archive = directory / f"{service}.dump"
        partial.rename(archive)
        manifest["archives"][service] = {
            "file": archive.name,
            "size_bytes": archive.stat().st_size,
            "sha256": sha256(archive),
        }

    # A backup is complete only after BOTH dumps and archive checks succeed.
    with (directory / "manifest.json").open("x", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2)
        output.write("\n")
    print(f"Backup complete: {directory}")
    return directory


def validate_backup(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or manifest.get("source_database") != SOURCE_DATABASE
            or not isinstance(manifest.get("archives"), dict)
            or set(manifest["archives"]) != set(SERVICES)):
        raise BackupError("Invalid or incomplete backup manifest; both databases are required.")
    archives = {}
    for service in SERVICES:
        entry = manifest["archives"][service]
        filename = f"{service}.dump"
        if not isinstance(entry, dict) or entry.get("file") != filename:
            raise BackupError(f"Invalid archive entry for {service}.")
        path = directory / filename
        if path.stat().st_size != entry.get("size_bytes") or sha256(path) != entry.get("sha256"):
            raise BackupError(f"Backup checksum/size mismatch: {path}. Nothing has been restored.")
        archives[service] = path
    return archives


def collect_evidence(database):
    evidence = {}
    for service in SERVICES:
        raw = run_in(service, "psql", "-X", "--no-password", "-U", "erp",
                     "--dbname", database, "-qAt", "-v", "ON_ERROR_STOP=1",
                     "--command", VERIFY_SQL)
        evidence[service] = json.loads(raw)
        if all(evidence[service][table]["rows"] == 0
               for table in ("tenants", "orders", "order_lines")):
            print(f"NOTE: {service}/{database} has no ERP rows; this tests schema recovery only.",
                  file=sys.stderr)
    return evidence


def create_test_database(service, database):
    # There is deliberately no user-supplied restore destination or overwrite option.
    if not re.fullmatch(r"erp_restore_[0-9]{8}t[0-9]{6}z_[0-9a-f]{8}", database):
        raise BackupError("Refusing to create a database outside the generated test namespace.")
    run_in(service, "createdb", "--no-password", "-U", "erp",
           "--maintenance-db=postgres", "--template=template0", database)
    # createdb fails if the name exists. The caller must not restore after that failure.


def restore(directory):
    archives = validate_backup(directory)
    # Validate BOTH archives before creating any databases.
    for service, archive in archives.items():
        check_archive(service, archive)
    database = "erp_restore_" + unique_id()
    for service in SERVICES:
        print(f"Creating and restoring {service}/{database}...", file=sys.stderr)
        create_test_database(service, database)
        with archives[service].open("rb") as source:
            run_in(service, "pg_restore", "--no-password", "-U", "erp",
                   "--dbname", database, "--exit-on-error", "--single-transaction",
                   "--no-owner", "--no-privileges", stdin=source)
    evidence = collect_evidence(database)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    print(f"Restore complete. Test database on BOTH servers: {database}")
    test_option = f"--test-project {TEST_PROJECT} " if TEST_PROJECT is not None else ""
    print(f'Compare saved evidence: python scripts/db_backup.py {test_option}verify {database} '
          f'--expected "{directory / "before.json"}"')
    return database


def verify(database, expected=None):
    # Only database names, never a connection string that could redirect the command.
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database):
        raise BackupError("Use a plain PostgreSQL database name.")
    previous = None if expected is None else json.loads(expected.read_text(encoding="utf-8"))
    evidence = collect_evidence(database)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    if expected is not None:
        if evidence != previous:
            raise BackupError("Verification mismatch: counts, data, indexes, or sequences differ. "
                              "Check that the evidence and backup were made while writes were paused.")
        print("Verification passed: both databases match the saved evidence.", file=sys.stderr)


def main(argv=None):
    global TEST_PROJECT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-project", help="Use an isolated erp-backup-test-<12 hex digits> project.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup", help="Back up erp from db and db_whale into backups/.")
    restore_parser = commands.add_parser("restore", help="Restore into new test databases only.")
    restore_parser.add_argument("directory", type=Path, help="A complete timestamped backup directory.")
    verify_parser = commands.add_parser("verify", help="Read-only counts and data fingerprints for both servers.")
    verify_parser.add_argument("database", help="erp or a generated erp_restore_... name.")
    verify_parser.add_argument("--expected", type=Path, help="JSON saved by an earlier verify command.")
    args = parser.parse_args(argv)
    previous_project = TEST_PROJECT
    try:
        compose_command(args.test_project)  # Validate before creating files or running Docker.
        TEST_PROJECT = args.test_project
        if args.command == "backup":
            backup()
        elif args.command == "restore":
            restore(args.directory.resolve())
        else:
            verify(args.database, args.expected)
    except (BackupError, OSError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        print("No automatic cleanup is performed. Incomplete backups and any newly created "
              "test databases are kept for inspection. Restore never targets the working erp databases.",
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; incomplete backups and test databases are kept for inspection.", file=sys.stderr)
        return 130
    finally:
        TEST_PROJECT = previous_project
    return 0


if __name__ == "__main__":
    sys.exit(main())
