"""Isolation checks run without Docker, before integration containers are started."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import db_backup as tool
from tests.run_backup_recovery import LABEL, check_config, check_containers


PROJECT = "erp-backup-test-0123456789ab"


def safe_config():
    service = {
        "image": "postgres:16", "network_mode": "none", "labels": {LABEL: "true"},
        "tmpfs": ["/var/lib/postgresql/data:size=268435456"],
    }
    return {"name": PROJECT, "services": {name: copy.deepcopy(service) for name in tool.SERVICES}}


def safe_container():
    return {"Config": {"Labels": {LABEL: "true", "com.docker.compose.project": PROJECT,
                                  "com.docker.compose.service": "db"}},
            "HostConfig": {"NetworkMode": "none"},
            "Mounts": [{"Type": "tmpfs", "Destination": "/var/lib/postgresql/data"}]}


class EnvironmentTests(unittest.TestCase):
    def test_test_commands_cannot_select_the_application_compose_file(self):
        command = tool.compose_command(PROJECT)
        self.assertEqual(command[command.index("--project-name") + 1], PROJECT)
        self.assertEqual(Path(command[command.index("-f") + 1]).name, "compose.backup-test.yml")
        self.assertIn("--env-file", command)

    def test_non_test_project_names_are_rejected_before_any_command(self):
        for name in ("erp-spike", "main", "", "erp-backup-test-main", "erp-backup-test-123;whoami"):
            with self.subTest(name=name), patch.object(tool, "run_in") as run:
                self.assertEqual(tool.main(["--test-project", name, "backup"]), 1)
                run.assert_not_called()
                self.assertIsNone(tool.TEST_PROJECT)

    def test_run_in_passes_the_explicit_test_project_to_every_database_command(self):
        with patch.object(tool, "TEST_PROJECT", PROJECT), patch.object(tool.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stderr = b""
            tool.run_in("db_whale", "psql", "-d", "erp")
        command = run.call_args.args[0]
        self.assertIn(PROJECT, command)
        self.assertFalse(any(Path(item).name == "docker-compose.yml" for item in command))

    def test_only_isolated_services_are_accepted(self):
        check_config(safe_config(), PROJECT)
        for field, value in (("volumes", ["team-data:/var/lib/postgresql/data"]),
                             ("ports", ["5432:5432"]), ("network_mode", "host"),
                             ("container_name", "team-db"), ("tmpfs", [])):
            config = safe_config()
            config["services"]["db"][field] = value
            with self.subTest(field=field), self.assertRaises(tool.BackupError):
                check_config(config, PROJECT)
        config = safe_config()
        config["volumes"] = {"team-data": {"external": True}}
        with self.assertRaises(tool.BackupError):
            check_config(config, PROJECT)

    def test_cleanup_refuses_foreign_containers_or_persistent_mounts(self):
        check_containers([safe_container()], PROJECT)
        for kind in ("foreign_project", "missing_label", "persistent_mount", "host_network"):
            container = safe_container()
            if kind == "foreign_project":
                container["Config"]["Labels"]["com.docker.compose.project"] = "erp-spike"
            elif kind == "missing_label":
                del container["Config"]["Labels"][LABEL]
            elif kind == "persistent_mount":
                container["Mounts"] = [{"Type": "volume", "Name": "team-data"}]
            else:
                container["HostConfig"]["NetworkMode"] = "host"
            with self.subTest(kind=kind), self.assertRaises(tool.BackupError):
                check_containers([container], PROJECT)

    def test_devcontainer_startup_does_not_touch_databases(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"))
        for hook in ("initializeCommand", "onCreateCommand", "updateContentCommand", "postCreateCommand",
                     "postStartCommand", "postAttachCommand"):
            command = str(config.get(hook, ""))
            self.assertNotIn("app.seed", command)
            self.assertNotIn("docker compose", command)


if __name__ == "__main__":
    unittest.main()
