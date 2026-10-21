# v0.4: durable recommendation tasks and independent workers

## What changed

The recommendation row **is the queue item**. POST `/api/v1/runs` writes the task
and its initial event in one database transaction; there is no post-commit in-memory
dispatch that can be lost. Idempotency and the pending-task bound are checked under
a short admission lock shared by API instances. A returned 202 means persisted,
not completed. API restart no longer marks all unfinished work as failed.

Local quickstart defaults to `RUN_EXECUTION_MODE=embedded`. Embedded threads consume
the same SQL queue and are intended for development. Docker Compose uses `external`:
API accepts requests, and a separate `worker` service executes them. Each external
worker supervises one spawned subprocess at a time; multiple worker instances can
share PostgreSQL. No Redis/Celery broker or native LangGraph checkpointer is claimed.

```sh
git pull --ff-only origin main
docker compose up --build -d --wait
docker compose logs -f api worker
# More external consumers (size according to measured upstream/database limits):
docker compose up -d --scale worker=2
```

For a local independent worker, start the API with `RUN_EXECUTION_MODE=external`,
then run `python -m playmatch.worker` in the same configured environment. The API
must finish migrations first. `--once` processes at most one currently claimable
job; no job is a successful no-op. It is not a daemon. Do not run different code
versions against one queue. Stop pre-v0.4 API/worker processes before migration 0004;
old code does not participate in fencing. Keep database volumes and backups.

## Ownership, retries and cancellation

Claims carry a fresh random ownership token and increasing attempt number. Lease
and deadline calculations use the database clock, not independent machine clocks.
Every heartbeat, progress event, checkpoint and final write validates ownership.
Event allocation holds the run row lock, preventing competing `max(sequence)+1`
writes. Terminal state, final result and `done` event commit together. A cancelled
or replaced attempt cannot overwrite its successor or publish another completion.

The default lease is 15 seconds, renewed every 5 seconds. A crashed process is
eligible for recovery after expiry; at most 3 attempts, under one total 90-second
deadline (including queue wait for new requests). Known supervisor exits use bounded
backoff before retry. Ordinary graph exceptions fail explicitly rather than retrying
an arbitrary operation forever. Provider/model errors continue to use the existing
truthful partial-result fallback. Transient database failure leaves the lease for
recovery and never turns volatile memory into a successful result.

`POST /api/v1/runs/{id}/cancel` uses the same session or anonymous X-Run-Token authorization
as reads, plus the existing CSRF checks. It is idempotent. A terminal completed run
stays completed if completion won the race; cancellation does not erase a result.
Cancelling active work immediately fences writes. The external supervisor terminates
its child when cancellation, lost ownership or the total deadline is observed.
This cannot undo an HTTP call already accepted by an external provider.

Only short claim/admission transactions use a singleton writer lock, portable to
SQLite and PostgreSQL. The graph and provider calls run outside queue transactions.
This is a bounded queue (default 24 outstanding tasks), **not** a throughput claim or
an unbounded multi-tenant distributed platform. A future broker can be a wake-up
transport; database admission and fencing must remain authoritative.

## Safe stage checkpoints

The runner consumes LangGraph `stream_mode=updates` at node boundaries and stores
application-level snapshots after `understand`, `retrieve`, and `filter`. Each binds
a workflow version, the entire catalog/feedback/request context, model/retrieval
configuration identity and a checksum. Freshly loaded context must match before
restoring; otherwise the task restarts its graph with an explicit progress message.
Checksums detect accidental corruption, not a malicious database operator.

Only already committed stages are skipped. A crash after a provider accepted a call
but before the stage committed can repeat that call. Thus execution is **at least
once for uncommitted read-only stages**, with one fenced terminal commit, not external
exactly-once. The attempt bound limits this replay but is not a monetary billing cap.
No purchase, payment, email or other new irreversible tool was added.

Budget output is deliberately **not** a resumable checkpoint. Recovery after filter
rechecks prices; it never promotes an interrupted quote into current purchase evidence.
Explain and terminal publication are one final transaction. A checkpoint exceeding
8 MiB is skipped without truncation, retaining the preceding safe boundary. Large
catalog performance and serialization costs remain unbenchmarked.

## Browser behavior

Stopping the progress stream is different from cancelling the server task. The UI
provides both. A per-tab `sessionStorage` record holds only the opaque run ID,
anonymous capability when required, owner ID and timestamp (one-hour restore window).
It stores no query or model credentials, and is cleared on account change/logout.
Refresh offers “继续查看原任务”; reconnect uses GET/SSE, never another recommendation
POST. Invalid, expired and cross-account records are rejected. If storage is unavailable,
the current session still works but refresh recovery is unavailable.

An anonymous capability in sessionStorage remains accessible to same-origin JavaScript;
this is not protection against XSS or a compromised browser. Tokens are never placed
in URLs, server logs, fixtures of real users, public status endpoints or source snapshots.
The existing server-side capability/session checks remain the authorization boundary.

## Operational and validation limits

External supervisors enforce a wall-clock child deadline while alive. Embedded threads
cannot forcibly kill blocked Python or OS calls. Killing an entire container terminates
its worker and children; killing only the supervisor with SIGKILL may leave an orphan
child until it exits or the container is stopped. Fencing still rejects late writes,
but this is not a complete process-reaper/production orchestration solution.

Worker liveness is a container-local heartbeat file with a 30-second freshness check.
It means the loop recently reached the database, not that every model/provider is
reachable or that a recommendation has completed. CI additionally submits a real HTTP
recommendation in external mode and requires the independent worker to finish it.
Worker absence can leave queued work until a worker returns; the API does not report
that as completed. Rate limits remain per API process, while queue capacity is shared.

Tests include concurrent claims, stale-owner writes, cancel/finish races, retry limits,
checksums/context invalidation, process-kill recovery, two competing supervisor processes
and a real loopback HTTP provider that stalls until the supervisor terminates the child.
Process-kill recovery explicitly manipulates lease expiry in the test; it does not
claim a real 15-second wait. The timeout test uses an actual elapsed deadline.

```sh
uv run pytest -q
# Only a dedicated empty disposable test database; never production:
RUN_TEST_DATABASE_URL=postgresql+psycopg://... uv run pytest services/api/tests/test_durable_runs.py -q
npm --prefix apps/web test
npm --prefix tests/e2e test
docker compose exec -T api python scripts/smoke_worker.py
```

References consulted for API semantics: [SQLAlchemy transaction scopes](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html),
[LangGraph streaming updates](https://docs.langchain.com/oss/python/langgraph/streaming).
This implementation uses the project's existing locked dependencies; no framework
upgrade, paid API call or live catalog import is required for queue validation.
