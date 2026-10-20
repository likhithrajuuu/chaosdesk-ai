# Operating ChaosDesk

The service targets one internal support organization on one host with a local
SQLite volume. It has authenticated job admission, durable results, bounded
workers, idempotency, retry limits, rate/call budgets, and health checks. The
original one-ticket CLI remains available. Incident, engineering, and billing
actions are recommendations only.

This implementation is deployment infrastructure, not evidence of production AI
quality. Live-model accuracy/adversarial evaluation, a representative held-out
dataset, your deployment's load targets, backup restoration, TLS, and operational
alerts must be verified before accepting real customer traffic. The current
confidence values are uncalibrated; urgent or uncertain results require review.

## Local service

Install the tested dependency snapshot:

```bash
source .venv/bin/activate
python -m pip install -r requirements.lock
cp .env.service.example .env.service
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Put the generated token in `CHAOSDESK_API_TOKEN` in `.env.service`. The example
selects development/mock mode so no API calls are made. Load the same environment
in two terminals:

```bash
set -a
source .env.service
set +a
```

In the first terminal run `python -m chaosdesk.api`. In the second run
`python -m chaosdesk.worker`. The service and worker intentionally do not load
the older `.env` file automatically. They fail startup on invalid configuration.
Local binding defaults to `127.0.0.1:8000`; `CHAOSDESK_HOST` and `CHAOSDESK_PORT`
override it. The container explicitly binds its internal port on all interfaces;
Compose publishes that port on the host loopback interface only.

For production, set `CHAOSDESK_ENVIRONMENT=production`, `CHAOSDESK_MODE=ai`, and
`OPENAI_API_KEY` in your secret-management environment. Production rejects mock
mode and enabled LangSmith/LangChain prompt tracing. Configure a supported model
through `CHAOSDESK_MODEL`; use a tested model snapshot when reproducibility is
required. Changing the model requires re-evaluation.

## API

All `/v1` endpoints require `Authorization: Bearer <service-token>`. This shared
token authorizes access to all jobs for one organization. This is not a
multi-tenant identity or customer-facing authorization system. Place the service
behind your internal access gateway; rotate the token by restarting API instances
with a new value. Health endpoints expose only availability without authentication.

Submit a ticket:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/jobs \
  -H "Authorization: Bearer $CHAOSDESK_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: support-ticket-123-v1' \
  --data-binary @examples/ticket.json
```

A new submission returns 202, an opaque `job_id`, and a `Location` header. Poll
that location with the same authorization header to obtain its state and final
decision. Replaying the same idempotency key and normalized ticket returns the
existing job (200). Reusing a key for a different ticket returns 409.

States are `queued`, `running`, `completed`, and `failed`. A `completed` job can
still require human review. A `failed` job has no decision and must be investigated
by an operator; it must never be treated as resolved customer work. Responses
include attempts, model/mode, pipeline version, and the knowledge-base revision
used for a completed worker result.

Other endpoints:

| Endpoint | Meaning |
|---|---|
| `GET /health/live` | HTTP process responds |
| `GET /health/ready` | Database accessible, matching workers recently heartbeating, no expired leases, queue and daily AI budget available |
| `GET /v1/metrics` | Authenticated JSON queue counts, live worker processes, expired leases, oldest queued age, and reserved model attempts today |
| `GET /v1/jobs/{job_id}` | Authenticated job status and decision |

Errors use stable codes without input/provider details. Missing/invalid auth is
401, malformed tickets are 422, invalid idempotency keys are 400, unsupported
content types are 415, conflicting idempotency keys are 409, oversized requests
are 413, admission-rate exhaustion is 429, and queue/storage exhaustion is 503.
Transient admission failures include `Retry-After`. Duplicate JSON keys are
rejected. Responses have `Cache-Control: no-store` and generated request IDs.

## Capacity, attempts, and spend controls

| Variable | Default | Purpose |
|---|---:|---|
| `CHAOSDESK_WORKERS` | 4 | In-flight triage calls per worker process, maximum 32 |
| `CHAOSDESK_MAX_PENDING` | 1000 | Maximum queued plus running jobs |
| `CHAOSDESK_MAX_STORED_JOBS` | 100000 | Maximum retained jobs, including terminal jobs |
| `CHAOSDESK_SUBMISSIONS_PER_MINUTE` | 120 | New admissions per fixed minute; idempotent replays do not consume another admission |
| `CHAOSDESK_MODEL_REQUESTS_PER_MINUTE` | 60 | Smoothly paced AI job attempts across workers sharing the DB |
| `CHAOSDESK_MODEL_REQUESTS_PER_DAY` | 10000 | AI attempt budget per UTC day |
| `CHAOSDESK_MODEL_TIMEOUT` | 30 | SDK request timeout in seconds |
| `CHAOSDESK_MODEL_OUTPUT_TOKENS` | 1500 | Maximum model output tokens |
| `CHAOSDESK_MAX_ATTEMPTS` | 3 | Maximum claims per job, including interrupted claims |
| `CHAOSDESK_LEASE_SECONDS` | 180 | Claim lifetime, validated against model timeout |
| `CHAOSDESK_MAX_BODY_BYTES` | 65536 | HTTP body bound, including chunked bodies |
| `CHAOSDESK_RETENTION_DAYS` | 7 | Age of terminal jobs eligible for deletion |

All worker processes must use the same model, mode, capacity, and rate settings.
The DB serializes admission, claims, and AI start reservations. Attempt budgets
are conservative: a crash after reservation can consume a reserved attempt
without making a model request. Request limits, bounded input sizes, and output
token caps reduce exposure but are not dollar-denominated spend guarantees.
Set provider project budgets/alerts separately and tune RPM against your actual
token-per-minute quota. Other applications using the same provider account are
outside this limiter.

Connection/timeouts, HTTP 408/409/429, and provider 5xx failures are retried with
persisted exponential backoff and jitter. Numeric `Retry-After` seconds are
honored up to 300 seconds. Insufficient-quota and other 4xx errors are terminal.
Retries also pause AI starts across workers sharing the DB. The SDK's own retries
are disabled, preventing nested retry amplification. Invalid model output yields
the existing conservative, review-required fallback rather than unlimited retries.
Knowledge-base failures never create invented evidence.

Database claim leases and unique claim tokens fence completion and failure writes.
After a process dies, expired jobs can be claimed again; the last exhausted claim
becomes failed. Processing is at least once: a provider call can be repeated after
a crash or expired lease. Stored completion is fenced, but external calls are not
exactly once. No customer communication or destructive external action is performed.

SDK timeouts are request-level limits, not a universal kill switch for Python
threads. If an operation hangs beyond its lease, results from the expired claim
are discarded, readiness fails while expired leases exist, and an operator or
supervisor should restart affected workers. Graceful shutdown stops new claims,
withdraws the process heartbeat, and waits up to 45 seconds. Remaining leases
are recovered after expiration. Do not rely on readiness alone to detect every
provider outage; monitor failures, queue age, and completion rates.

## Knowledge-base rollout

Workers load an immutable, tokenized article snapshot at startup and record its
SHA-256 revision on completed jobs. No per-ticket disk scan is needed. Deploy KB
changes through a reviewed image and restart workers. New articles still need the
scope rules described in the README. Syntax errors, missing IDs, duplicate IDs,
or an empty/missing KB stop worker startup. Drain pending jobs before changing
model/mode or pipeline policy versions; workers only claim jobs matching their
configured model/mode. Schema version 1 rejects newer unsupported DB versions;
future schema changes need explicit migrations and rollback testing.

## Container deployment

```bash
docker compose --env-file .env.service up --build -d
```

The Compose services run as UID 10001, drop Linux capabilities, use a read-only
root filesystem, have memory/CPU bounds, and share the named `jobs` volume.
The volume must be writable by UID 10001. The API port binds to localhost only.
Keep it behind a TLS reverse proxy/access gateway. Configure the proxy's body
limit, a short request-body timeout, connection limits, and abuse rate limits;
Uvicorn has a 100-connection concurrency bound but is not a slow-client defense
by itself. The default container health checks report liveness/worker health;
configure your traffic router to use `/health/ready` for admission routing.
Docker Compose does not restart a container merely because it is unhealthy;
provide an alerting/supervision policy in your deployment platform.

SQLite WAL requires a local filesystem. Do not mount this database on NFS or
share it across hosts. This deployment has a single-host failure domain and is
not highly available. For multi-host availability, replace the store with a
transactional shared database/queue and preserve idempotency and fencing tests.
The queue is an interface boundary where that change can be made.

## Data handling, backups, and recovery

The database contains raw support tickets and response drafts. Use an encrypted
local volume, restricted host access, and encrypted backups. Database files are
created with restrictive permissions. Do not place database files, secrets, or
reports containing customer material into source control or container layers.
Logs contain generated IDs and event metadata; raw prompts, bodies, provider
exceptions, paths, and authorization headers are omitted by application logging.
Keep SDK debug logging and external prompt tracing disabled.

Terminal jobs are purged hourly by the worker process after the retention window.
Queued/running work is preserved. Idempotency ends when its terminal job is
purged. You can run `python -m chaosdesk.worker --purge` with the same environment
to purge explicitly. Deletion is logical: old contents may remain in WAL files,
free pages, and backups; storage/backup lifecycle controls must enforce your
actual retention requirements. Retained failed jobs require an operator review
process. To retry a terminal job after fixing its cause, submit the same ticket
under a new, deliberate idempotency key; replaying the old key returns its old state.

Use SQLite's backup API for a consistent backup, not a blind copy of the main
database while WAL is active:

```bash
python - <<'PY'
import sqlite3
from contextlib import closing
from chaosdesk.settings import Settings
settings = Settings.from_env()
with closing(sqlite3.connect(str(settings.database))) as source:
    with closing(sqlite3.connect('chaosdesk-backup.db')) as target:
        source.backup(target)
PY
```

Run backups under `umask 077` and move them to encrypted managed storage. Test
restoration in a staging directory: stop API and workers, restore a backup with
correct ownership, run `PRAGMA integrity_check`, restart workers and API, and
verify polling/completion for restored jobs. Restored running jobs will recover
after their leases expire. A rollback can lose newer admissions or repeat
provider calls; reconcile ticket IDs against the upstream ticket system.

## Verification and release

```bash
python -m pip check
python -m pytest -q
python scripts/smoke_service.py
python -m chaosdesk.harness all --output reports/harness.json
python -m chaosdesk.loadtest --tickets 100 --workers 4 --latency-ms 50 --rate-limit-every 10 --output reports/load.json
```

The load test uses the actual HTTP routes, queue, worker pool, and retry handling
with a fake model. It checks bounded concurrency and terminal failures. It cannot
predict actual provider throughput. Use `--max-p95-ms` to enforce a measured
deployment target. CI runs tests, harnesses, a simulated load scenario, and a
container build; runtime application and test dependencies are pinned in
`requirements.lock`. Recreate and reverify that snapshot after upgrades.

For a release, also run `python -m chaosdesk.evaluate --mode ai` against the
intended model/account (paid calls), add held-out/adversarial examples, set
model-quality and throughput acceptance gates, test a worker crash/recovery,
and exercise a backup restore in the actual environment. Configure alerts for
failed jobs, old queued jobs, expired leases, missing worker heartbeats,
readiness failures, provider rate limits, and budget exhaustion. No service or
infrastructure has been published by this repository change.

Implementation references: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
[SQLite transactions](https://www.sqlite.org/lang_transaction.html), and
[OpenAI rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits).
