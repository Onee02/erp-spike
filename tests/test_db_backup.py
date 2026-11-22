"""Safety/error-path tests. Docker calls are mocked; no databases are accessed."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import db_backup as tool


RUN_ID = "20261122t120000z_01234567"
TEST_DATABASE = "erp_restore_" + RUN_ID
ARCHIVE_BYTES = b"PGDMP\x00\xff\r\narchive bytes"


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        root_patch = patch.object(tool, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.output = io.StringIO()
        self.enter_context(contextlib.redirect_stdout(self.output))
        self.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def enter_context(self, manager):
        manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)

    def dump_command(self, service, *command, stdin=None, stdout=None):
        if command[0] == "pg_dump":
            assert stdout is not None
            stdout.write(ARCHIVE_BYTES)
        return b"archive contents\n"

    def make_backup(self):
        with patch.object(tool, "run_in", side_effect=self.dump_command):
            return tool.backup()

    def test_backup_keeps_binary_bytes_and_checksums_for_both_databases(self):
        directory = self.make_backup()
        archives = tool.validate_backup(directory)
        self.assertEqual(set(archives), set(tool.SERVICES))
        for path in archives.values():
            self.assertEqual(path.read_bytes(), ARCHIVE_BYTES)

    def test_generated_database_name_is_safe_and_accepted_by_verification(self):
        database = "erp_restore_" + tool.unique_id()
        with patch.object(tool, "run_in") as run:
            tool.create_test_database("db", database)
            run.assert_called_once()
        with patch.object(tool, "collect_evidence", return_value={}):
            self.assertEqual(tool.main(["verify", database]), 0)

    def test_failed_second_dump_has_no_completion_manifest_or_success_message(self):
        def fail_second(service, *command, **kwargs):
            if service == "db_whale" and command[0] == "pg_dump":
                raise tool.BackupError("disk full")
            return self.dump_command(service, *command, **kwargs)

        with patch.object(tool, "run_in", side_effect=fail_second):
            self.assertEqual(tool.main(["backup"]), 1)
        self.assertEqual(list(self.root.rglob("manifest.json")), [])
        self.assertNotIn("Backup complete", self.output.getvalue())

    def test_missing_docker_returns_failure(self):
        with patch.object(tool.subprocess, "run", side_effect=FileNotFoundError("docker unavailable")):
            self.assertEqual(tool.main(["backup"]), 1)
        self.assertNotIn("Backup complete", self.output.getvalue())

    def test_corrupt_or_missing_second_archive_prevents_all_database_commands(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                directory = self.make_backup()
                archive = directory / "db_whale.dump"
                if missing:
                    archive.unlink()
                else:
                    archive.write_bytes(ARCHIVE_BYTES[:-1] + b"X")
                with patch.object(tool, "run_in") as run:
                    self.assertEqual(tool.main(["restore", str(directory)]), 1)
                    run.assert_not_called()

    def test_incomplete_or_wrong_manifest_is_rejected_before_database_access(self):
        directory = self.make_backup()
        manifest = directory / "manifest.json"
        for value in ("", "[]", '{"version": 1}', '{"source_database": "another"}'):
            with self.subTest(value=value):
                manifest.write_text(value, encoding="utf-8")
                with patch.object(tool, "run_in") as run:
                    self.assertEqual(tool.main(["restore", str(directory)]), 1)
                    run.assert_not_called()

    def test_invalid_archive_is_rejected_before_creating_any_database(self):
        directory = self.make_backup()

        def fail_listing(service, *command, **kwargs):
            if service == "db_whale":
                raise tool.BackupError("invalid archive contents")
            return b"contents"

        with patch.object(tool, "run_in", side_effect=fail_listing) as run:
            self.assertEqual(tool.main(["restore", str(directory)]), 1)
        self.assertTrue(all(call.args[1] == "pg_restore" and "--list" in call.args
                            for call in run.call_args_list))

    def test_restore_targets_new_databases_and_never_uses_clean_or_create(self):
        directory = self.make_backup()
        with patch.object(tool, "unique_id", return_value=RUN_ID), \
                patch.object(tool, "run_in", return_value=b"") as run, \
                patch.object(tool, "collect_evidence", return_value={}):
            self.assertEqual(tool.restore(directory), TEST_DATABASE)
        creates = [call for call in run.call_args_list if call.args[1] == "createdb"]
        restores = [call for call in run.call_args_list if "--dbname" in call.args]
        self.assertEqual([call.args[0] for call in creates], list(tool.SERVICES))
        self.assertEqual(len(restores), 2)
        for call in restores:
            args = call.args
            self.assertEqual(args[args.index("--dbname") + 1], TEST_DATABASE)
            self.assertIn("--single-transaction", args)
            self.assertIn("--exit-on-error", args)
            self.assertNotIn("--clean", args)
            self.assertNotIn("--create", args)

    def test_existing_test_database_failure_stops_restore(self):
        directory = self.make_backup()

        def already_exists(service, *command, **kwargs):
            if command[0] == "createdb":
                raise tool.BackupError("database already exists")
            return b"contents"

        with patch.object(tool, "run_in", side_effect=already_exists) as run:
            self.assertEqual(tool.main(["restore", str(directory)]), 1)
        self.assertFalse(any("--dbname" in call.args for call in run.call_args_list))
        self.assertNotIn("Restore complete", self.output.getvalue())

    def test_restore_error_never_reports_success(self):
        directory = self.make_backup()

        def fail_restore(service, *command, **kwargs):
            if command[0] == "pg_restore" and "--dbname" in command:
                raise tool.BackupError("restore failed")
            return b"contents"

        with patch.object(tool, "run_in", side_effect=fail_restore):
            self.assertEqual(tool.main(["restore", str(directory)]), 1)
        self.assertNotIn("Restore complete", self.output.getvalue())

    def test_working_database_cannot_be_used_as_restore_destination(self):
        with patch.object(tool, "run_in") as run:
            for name in ("erp", "postgres", "erp_restore_erp", "erp_restore_../../erp"):
                with self.subTest(name=name), self.assertRaises(tool.BackupError):
                    tool.create_test_database("db", name)
            run.assert_not_called()

    def test_verification_detects_changed_data(self):
        expected = self.root / "before.json"
        original = {service: {"orders": {"rows": 3, "fingerprint": "original"}}
                    for service in tool.SERVICES}
        expected.write_text(json.dumps(original), encoding="utf-8")
        with patch.object(tool, "collect_evidence", return_value=original):
            self.assertEqual(tool.main(["verify", TEST_DATABASE, "--expected", str(expected)]), 0)
        changed = json.loads(json.dumps(original))
        changed["db_whale"]["orders"]["fingerprint"] = "changed"
        with patch.object(tool, "collect_evidence", return_value=changed):
            self.assertEqual(tool.main(["verify", TEST_DATABASE, "--expected", str(expected)]), 1)

    def test_verification_uses_read_only_sql_on_both_servers(self):
        rows = {table: {"rows": 1} for table in ("tenants", "orders", "order_lines")}
        with patch.object(tool, "run_in", return_value=json.dumps(rows).encode()) as run:
            tool.verify("erp")
        self.assertEqual([call.args[0] for call in run.call_args_list], list(tool.SERVICES))
        for call in run.call_args_list:
            self.assertEqual(call.args[1], "psql")
            self.assertIn("READ ONLY", call.args[-1])
            self.assertIn("ON_ERROR_STOP=1", call.args)

    def test_docker_nonzero_exit_is_propagated(self):
        result = subprocess.CompletedProcess([], 3, stdout=b"", stderr=b"permission denied")
        with patch.object(tool.subprocess, "run", return_value=result), \
                self.assertRaisesRegex(tool.BackupError, "permission denied"):
            tool.run_in("db", "pg_dump")


if __name__ == "__main__":
    unittest.main()
