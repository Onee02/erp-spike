# Handoff: execute the disposable Backup & Recovery tests

The user wants actual test execution and evidence for a university ERP assignment.
The local Windows environment used to prepare these files had neither a usable
Python interpreter nor Docker. Static checks are not proof that the real tests pass.

Work in this uploaded `erp-backup-test` directory inside a fresh Blank Codespace.
Check `python --version`, `docker version`, and `docker compose version`, then run:

```bash
python tests/run_backup_recovery.py
```

The runner executes unit tests and a real PostgreSQL backup/restore/verification
round trip. It creates its own unique Docker project, uses only synthetic data in
temporary storage, and cleans up only containers verified to belong to that run.
Read the generated report and command log under `backups/test-runs/` before
reporting the result. Fix implementation issues if tests fail and rerun the
procedure; do not report success until it has actually finished successfully.

Do not run the normal `docker-compose.yml`, the seed script, blanket Docker cleanup
commands, or commands against any team's working databases. Do not weaken the test
project, storage, or container ownership checks to get a pass. Do not implement
application workers, change normal tenant routing/volumes, commit, or push.

If a failure leaves test containers running, the generated report identifies the
project. Inspect its project and safety labels before any cleanup, and use only
that exact project with `tests/compose.backup-test.yml`. Never use a generic
`docker compose down` or `docker system prune`.

Report the commands actually executed, their results, and the report/log paths.
The upload has no Git history or remote; keep any fixes as files so they can be
copied back to the original local checkout without overwriting teammates' work.
