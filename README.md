# Mini-ERP: the whale customer

This is a small demo of a cloud business system (an "ERP") used in the exercise
*Refinement of the Whale*. You do not need to read or understand any code to use it.

## The business situation

You run a cloud ERP for small businesses. Every customer (called a *tenant*) has 5 to 20
users who enter orders and look at reports.

- **300 small customers** pay about €99 a month each.
- All of them share **one system and one database**. This keeps costs low.
- A distributor with **1,200 users** wants to join. They would pay **€6,000 a month**
  on a 12-month contract. We call this big customer **the whale**.
- They require **99.9% availability** (a promise that the system works almost all the time)
  and **separation of their data** from everyone else's.

The catch: the system was never built for a customer this big. In a test it had to handle
about 8 times today's peak load, and the database grew by 35%. Worst of all, the whale's
**month-end reports** slowed the test system down for everybody else.

Sales needs a go / no-go decision within a week. The question for the exercise:
**can we take on the whale without hurting our 300 small customers, and what would it cost?**

## What the application shows

The application has two pages.

**Customer** (the main page). This is what one customer sees: their latest orders, a form
for a new order, and a button that creates the monthly report. The label *System speed* in
the top bar shows how long the last action took (green is fast, red is slow).

**Operations.** A control panel for setting up the situation:

- **Remove whale / Add whale** takes the big customer out of the system or puts them back.
- **Simulate month-end** makes the whale's finance team run their monthly reports all at
  once, as they would on the last day of the month.

### Try it yourself

1. Open the **Customer** page and click around as a small customer. Everything is fast.
2. Open **Operations** and click **Simulate month-end**.
3. Go back to the **Customer** page and try again. The system is now slow, and after a few
   seconds the page stops working. This is what the small customers would experience.
4. On **Operations**, click **Remove whale**. Now the small customer's page works again,
   even though the simulation is still running.
5. Click **Add whale** (it takes about 15 seconds) and **Stop simulation** to restore everything.

This is the problem you are asked to solve.

## How to run it

The easiest way is in your web browser. You do not need to install anything.

1. Log in to GitHub and open this repository.
2. Choose your own branch from the branch list at the top left of the repository page.
3. Click the green **Code** button, open the **Codespaces** tab and choose
   **Create codespace on** your branch.
4. Wait for the Python requirements to install. Startup does not start database
   containers or seed data. For backup testing, use the disposable procedure below.
5. To run the application itself, start the existing environment deliberately using
   the commands below. Then use the **Ports** tab to open port **8000**.

Notes:

- A Codespace uses your free monthly hours. When you finish, stop it
  (github.com/codespaces, then **Stop codespace**). It also stops by itself after 30
  minutes without activity.
- Pages: the main page is the address of port 8000, and **Operations** is the same address
  followed by `/ops`.

### Running on your own computer (optional)

If you have Docker Desktop installed and running, open a terminal in the project folder and run:

```bash
docker compose up -d --build
```

Only for a new, disposable demonstration database: the following command **truncates
existing data**. Do not run it on your team's working databases or for backup tests.

```bash
docker compose run --rm api python -m app.seed
```

Then open <http://localhost:8000> in your browser. To switch it off:

```bash
docker compose down
```

## Backup and recovery

Use `scripts/db_backup.py` from your computer or the Codespaces terminal. It needs
Python 3.9+ and Docker Compose; no extra Python packages are required. PostgreSQL
tools run inside the existing `postgres:16` containers.

The tool backs up the `erp` database in **both** services, `db` and `db_whale`.
Each archive contains the database schema, data, indexes, constraints, and sequence
values. It does not back up server-wide roles, Docker volumes, or server settings.
The existing `erp` role is used for the recovery exercise.

Restoration creates a new database named `erp_restore_<timestamp>_<random-id>` on
each existing server. These are separate databases, but share server resources
with the working databases. The application keeps using `erp`. The tool does not
change tenant routing, volumes, or application configuration, and never drops,
truncates, or restores over an existing database. Test databases remain available
for inspection; they are not automatically deleted.

### Automated testing in a disposable Codespace (recommended)

Run the full procedure with one command from the project root:

```bash
python tests/run_backup_recovery.py
```

Python 3.9+, Docker, and Docker Compose v2 with `up --wait` are required. No Python
packages or PostgreSQL installation on the host are needed. The runner performs:

1. All Python unit tests, including tests of the environment's isolation rules.
2. Creation of two fresh PostgreSQL 16 containers in a unique
   `erp-backup-test-<random-id>` Docker project.
3. Schema creation and a tiny synthetic dataset in each new database. It never
   runs `app.seed` or loads a backup from your team's working databases.
4. Real backup creation for both databases, then new synthetic records added
   after the backup to demonstrate recovery of the earlier snapshot.
5. Restoration into new test databases and comparison of rows, fingerprints,
   indexes, and sequences against the saved pre-backup evidence.
6. Checks that source databases remain unchanged by restore and that new orders
   and order lines can be inserted into the restored databases.
7. Rejection of a deliberately damaged copy of a synthetic archive, without
   creating any databases for that rejected restore.
8. Removal of only this run's containers, after checking their ownership labels.

The separate `tests/compose.backup-test.yml` has no published ports, persistent
volumes, host-directory mounts, or network access. Database files live in tmpfs
(temporary memory storage) and disappear with the containers. The runner passes
the test project explicitly to every database command, ignores the team's `.env`,
and never starts or stops the normal application Compose project. It refuses
cleanup if a container's project or safety labels do not match.

Results are saved in `backups/test-runs/<project>/report.json` and `commands.log`.
Archives and evidence are kept under `backups/` for inspection; they contain only
synthetic test data. A final `PASS` is printed only after the tests and cleanup
actually finish. A failure returns a nonzero exit code and records the reason.
If the process is forcibly killed, a report may remain `running`; this is not a pass.

#### Opening the unpublished changes without committing or pushing

Local, uncommitted files are not automatically copied into a GitHub Codespace.
Creating a Codespace from the old remote branch would still execute its old
automatic seed command. Use a **fresh Blank template Codespace** instead:

1. Go to [GitHub Codespaces](https://github.com/codespaces), find **Blank** under
   the quick-start templates, and choose **Use this template**. Do not reuse a
   team Codespace. No repository needs to be published.
2. Upload the prepared `artifacts/erp-backup-test-bundle.zip` from this local
   checkout into the Codespace's Explorer. It contains the current source and
   tests, with no `.git` directory, backups, or database data.
3. In the Codespace terminal, in the directory containing the uploaded archive:

   ```bash
   unzip -n erp-backup-test-bundle.zip
   cd erp-backup-test
   ```

4. Run the automated command above, or start Codex in this directory and ask it
   to follow `tests/CODESPACES.md`. Opening a Codespace does not move a local
   Codex session into it; the test-running session must run inside the Codespace.

The updated local `.devcontainer/devcontainer.json` only installs Python
requirements. It has no database startup or seed hook. The Blank template's
existing Python/Docker environment is sufficient; no rebuild is needed for the
uploaded test bundle.

### Manual backup and recovery of an existing environment

Run these commands from the repository root after copying the changed files into
your existing Codespace. These manual commands intentionally back up your existing
databases; use the automated disposable procedure above for development tests.
**Do not run the seed command or remove/add whale data for this exercise.**

1. Check Python and the existing database services:

   ```bash
   python --version
   docker compose version
   docker compose ps -a
   ```

   If the existing database containers are stopped, start them without recreating
   them, then wait until both are healthy:

   ```bash
   docker compose start db db_whale
   docker compose ps
   ```

   If the containers do not exist, use your team's normal environment setup first.
   Do not recreate the working databases just to test recovery.

2. Stop the month-end simulation using **Stop simulation**, stop any load test,
   and leave the customer page idle. Ask teammates to pause writes until step 3
   finishes. Save read-only evidence of the current contents:

   ```bash
   mkdir -p backups
   python scripts/db_backup.py verify erp > backups/before.json
   ```

   Check that the command succeeds before continuing. Evidence includes row
   counts and a fingerprint of every row in `tenants`, `orders`, and `order_lines`,
   index definitions, and order/line sequence values. Fingerprints detect changed
   values even when row counts stay the same. Empty databases are reported as
   empty; recovering an empty database demonstrates schema recovery only.

3. Create the backup:

   ```bash
   python scripts/db_backup.py backup
   ```

   Success prints the new directory, for example:

   ```text
   backups/20261122t120000z_a1b2c3d4/
     db.dump
     db_whale.dump
     manifest.json
   ```

   Each run uses a new directory. A manifest is written only after both dumps
   and archive-list checks succeed; it records their sizes and SHA-256 checksums.
   The whole `backups/` directory is ignored by Git. Save the evidence with this
   particular backup (replace the example path with the one actually printed):

   ```bash
   cp backups/before.json backups/20261122t120000z_a1b2c3d4/before.json
   ```

   Normal writes can now resume. `pg_dump` itself supports concurrent writes, but
   the separate evidence capture must happen while writes are paused for an exact
   later comparison. Each database has its own consistent snapshot; the two dumps
   are not one simultaneous snapshot across servers.

4. Restore that backup into new test databases:

   ```bash
   python scripts/db_backup.py restore backups/20261122t120000z_a1b2c3d4
   ```

   Replace the path above with your backup path. Both archives are checked before
   any test database is created. Restoration uses `pg_restore` with
   `--exit-on-error` and `--single-transaction`. An existing target name causes an
   error; it is never reused. The command checks that the restored ERP tables can
   be read and prints a new test database name shared by the two servers, such as
   `erp_restore_20261122t121000z_e5f6a7b8`.

5. Compare the recovered data with the saved evidence. Replace both example names:

   ```bash
   python scripts/db_backup.py verify erp_restore_20261122t121000z_e5f6a7b8 --expected backups/20261122t120000z_a1b2c3d4/before.json
   ```

   Expect `Verification passed: both databases match the saved evidence.`
   Any mismatch or failed command returns a nonzero exit code. The comparison is
   against the saved evidence, so later changes in the working databases are fine.
   You can also view recovered orders without changing them:

   ```bash
   docker compose exec -T db psql -X -U erp -d erp_restore_20261122t121000z_e5f6a7b8 -v ON_ERROR_STOP=1 -c "SELECT id, tenant_id, order_date, total FROM orders ORDER BY id DESC LIMIT 5;"
   docker compose exec -T db_whale psql -X -U erp -d erp_restore_20261122t121000z_e5f6a7b8 -v ON_ERROR_STOP=1 -c "SELECT id, tenant_id, order_date, total FROM orders ORDER BY id DESC LIMIT 5;"
   ```

For your teacher, show the two archive files, the new database names, the matching
verification output, and some recovered orders. There is no need to delete any
working data to demonstrate recovery.

### Failures, limitations, and local checks

- A missing Docker command, stopped database, failed dump, damaged archive, failed
  restore, or failed verification produces `FAILED` and a nonzero exit code.
  Do not continue to the next step after a failure.
- Failed backup directories may contain `.partial` files or lack a valid manifest.
  They are not complete backups. Rerun the backup to create a fresh directory.
- Restore is atomic within each target database, not across both servers. If the
  second restore fails, the first test database may already be restored. No cleanup
  runs automatically, and no overall success is reported. A retry uses new names.
- Restore only archives you created and trust. Checksums detect accidental changes;
  they do not establish who created an archive.
- Copies in `backups/` protect against database mistakes, but remain on the same
  computer/Codespace. Copy completed backups to separate storage to protect against
  losing that environment. This assignment implementation does not schedule backups
  or provide recovery to an arbitrary point between backups.

Run the safety/error-handling tests without Docker:

```bash
python -m unittest discover -s tests -v
python -m py_compile scripts/db_backup.py tests/test_db_backup.py
git diff --check
```

The unit tests mock Docker calls. They check corrupt/incomplete backups, command
failures, binary archive handling, safe restore destinations, environment isolation,
and verification mismatches. They do not replace the real round trip performed by
`python tests/run_backup_recovery.py`.

PostgreSQL references: [pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html)
and [pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html).

## Separate application workers

The original scenario above describes the shared application's bottleneck. The
normal Compose setup now gives the whale its own application container as well as
its existing database:

```text
Browser / load test -> Nginx :8000
                       |-- small tenants 2..301 -> api       -> db
                       |-- whale tenant 1      -> api_whale -> db_whale
```

`api` and `api_whale` each run one Uvicorn process, have their own request threads
and connection pools (maximum 10 connections each), and are limited to 1 CPU and
512 MiB each. Only Nginx publishes the application port, 8000. The existing
PostgreSQL services, host ports, initialization mounts, and resource settings are
unchanged. CPU limits cap consumption; containers still share the host's physical
CPU, memory, and disk. This is not protection against a whole-host outage.

### Routing and existing features

| Public path | Application worker |
| --- | --- |
| `/t/1/...` | Whale |
| Other `/t/...` paths | Small; invalid tenant IDs are rejected |
| `/admin/whale` and its actions | Whale |
| `/admin/simulation` and its actions | Whale |
| `/admin/metrics/whale` and `/admin/metrics/whale/reset` | Whale |
| `/admin/metrics/small` and `/admin/metrics/small/reset` | Small |
| `/`, `/ops`, `/static/...`, `/api/tenants`, `/health` | Small |

The application checks tenant ownership before processing tenant requests and
again when choosing a database connection. Wrong-group access returns HTTP 403,
including direct requests inside the Docker network. IDs outside 1..301 return
404. `TENANT_GROUP=small` is the default; `whale` owns tenant 1. The explicit `all`
mode exists only for the controlled shared-process benchmark. These restrictions
are workload protection; the demo still has no customer authentication.

The customer dropdown reads small-customer names from `db` and includes a fixed
Whale Distribution entry. It does not query `db_whale`, so the small-customer page
can load while the whale is unavailable. That entry is directory information,
not proof that whale business data exists. No existing tenant data is moved or
seeded by this change. In particular, the old seed script still populates only
its `DATABASE_URL`; do not run it to prepare working databases for this change.

The month-end simulator starts only on the whale worker and sends HTTP requests
to that same container's `127.0.0.1:8000`. Its clients, reports, and database waits
therefore consume whale application capacity. Start/stop/status run without
waiting for a synchronous request thread. Stop prevents new requests; the UI
shows "stopping" until current requests finish. Simulation controls and metrics
stay in one process, which is why each container uses exactly one Uvicorn worker.

The Operations page displays small and whale metrics separately. `/admin/metrics`
retains its original shape with additional worker identification and returns the
small process through the proxy. Use the explicit group paths to inspect/reset
both groups. Metrics are rolling samples, not lifetime counters; percentiles from
different workers are not averaged. Every handled response includes
`X-ERP-Worker` and `X-ERP-Instance` for routing tests. `/health` checks process
liveness, not database availability.

The original whale add/remove buttons remain available on the whale worker.
They still modify whale data when deliberately invoked. Automated worker tests
do not invoke these destructive actions; they check routing with GET requests
and check rejection of POST requests sent to the wrong worker.

### Safe automated worker tests

With the current files present in a Docker-capable environment, run from the
repository root:

```bash
python worker_tests/run.py
```

Only Python 3.10+ and Docker Compose v2 with `up --wait` are needed on the host.
Application dependencies are installed inside the test image. This uses the
standalone `worker_tests/compose.yml`, a fresh `erp-worker-test-<random-id>` project,
an internal network, and temporary-memory database storage. There are no published
test ports or working database mounts. It never runs the normal Compose project
or `app.seed`. All test data is generated by the deterministic, INSERT-only
`worker_tests/fixture.py` in empty disposable databases.

The procedure runs Python unit tests in a test client, checks `nginx -t`, performs
real proxy/direct HTTP tests for reads, writes, reports, tenant selection, metrics,
and simulation, checks distinct application processes and actual resource limits,
and verifies that small-customer operations still work after stopping the test
whale API and then its database. Cleanup checks project/safety labels and removes
only that run's disposable containers. Test results and logs remain in the ignored
`artifacts/worker-tests/<run>/` directory. No success is recorded until commands
actually finish. A failure returns nonzero and records the reason.

Python unit tests can also run without Docker if Python and the application's
requirements are installed:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s worker_tests -p 'test_*.py' -v
```

Optional static checks using Node (also available without Python/Docker):

```bash
node --test worker_tests/test_proxy_config.mjs
node --test worker_tests/test_client_ui.mjs
node --check app/static/client.js
node --check app/static/ops.js
git diff --check
```

The Node tests check routing rules in the template; they do not execute Nginx.
The backup scripts, tests, and disposable backup infrastructure are unchanged.
Their tests can still be run separately with `python -m unittest discover -s tests -v`.
The earlier backup upload ZIP is a historical snapshot; copy the current files,
including `nginx/` and `worker_tests/`, when moving this implementation to Codespaces.

### Controlled performance comparison

```bash
python worker_tests/run.py --performance --duration 60 --repeats 3 --small-workers 10 --whale-workers 40
```

This first runs the functional suite, then runs three pairs of measurements with
the existing `loadtest/loadtest.py` unchanged. The two layouts are:

| Layout | Application resources | Databases |
| --- | --- | --- |
| Shared baseline | One process, 2 CPUs / 1 GiB; tenant DSN routing enabled | Two separate databases |
| Separate workers | Two processes, each 1 CPU / 512 MiB | The same two-database arrangement |

Every trial gets fresh fixtures: 300 small tenants with 300 orders each, and the
whale with 31,500 orders; every order has four deterministic lines. SQL and indexes
are identical. Both layouts use the same proxy, database limits, and load-generator
limits. The run order alternates, reports are warmed up, and the load generator's
initial random seed is fixed. Scheduling and timings still vary between runs.
The UI simulation is off during performance measurements; the HTTP smoke test
uses four simulator clients, while the benchmark uses the requested concurrency.

The report records each trial's small-tenant p95/p99, error counts and error rates
for the small-only and with-whale phases, and their percentage degradation. It
also retains whale results, raw load-test JSON, resolved configuration, image IDs,
container/process IDs, and resource limits. Report per-trial measurements; do not
average p95 values and call the result a combined percentile. The client load-test
measurements include network/queueing time and differ from the Operations page's
server-side rolling samples.

This is a repeatable synthetic concurrency test. It does not prove a 1,200-user
peak, less than 10% degradation, or a 99.9% availability SLA. No performance result
should be claimed until the actual benchmark has run. Existing `results/baseline.json`
is not overwritten or used as this experiment's baseline.

### Applying the application configuration to an existing environment

First verify that both existing database containers are running and contain the
intended tenant data. After completing disposable tests, the application-only
update is:

```bash
docker compose up -d --build --no-deps api api_whale
docker compose up -d --no-deps proxy
```

These commands recreate only the application containers and start the proxy;
they do not recreate database containers or change database volumes. There is a
brief application interruption while port 8000 moves from the old API to Nginx.
Do not use a volume cleanup, a seed command, or a whole-stack reset for this task.
