"""Real database/state-machine tests. No external provider calls."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playmatch.database import Database
from playmatch.main import create_app
from playmatch.models import GameRecord, RunEventRecord, RunRecord
from playmatch.run_queue import (
    CHECKPOINT_VERSION,
    LeaseLost,
    RunQueue,
    database_now,
    now_iso,
)
from playmatch.runs import context_hash, execute_claim, restore_state, snapshot_state
from playmatch.security import digest
from playmatch.workflow import build_graph, rule_intent
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def durable(settings, catalog):
    url = os.getenv("RUN_TEST_DATABASE_URL", settings.database_url)
    config = settings.model_copy(update={"database_url": url, "run_execution_mode": "external"})
    db = Database(url)
    db.migrate()
    # This env variable must refer to a fresh disposable test database. Never drop
    # application tables; tests use only their own unique run ids.
    with db.sessions() as session, session.begin():
        for game in catalog:
            if session.get(GameRecord, game["id"]) is None:
                session.add(GameRecord(id=game["id"], title=game["title"], payload=game))
    yield db, config, RunQueue(db, config)
    db.close()


def add_run(db, *, deadline=90, status="queued", **overrides):
    ident = str(uuid4())
    with db.sessions() as session, session.begin():
        record = RunRecord(
            id=ident,
            user_id=None,
            capability_hash=digest("test-token"),
            idempotency_scope=ident,
            idempotency_key_hash=ident,
            payload_hash=ident,
            query="太空探索",
            request_payload={"query": "太空探索", "country": "CN", "budget_minor": None},
            status=status,
            mode="rules",
            intent={},
            results=[],
            warnings=[],
            agent_state={},
            created_at=now_iso(),
            deadline_at=database_now(session) + deadline,
            **overrides,
        )
        session.add(record)
    return ident


def expire(db, ident):
    with db.sessions() as session, session.begin():
        row = session.get(RunRecord, ident)
        row.lease_expires_at = database_now(session) - 1
        row.available_at = 0


def events(db, ident):
    with db.sessions() as session:
        return [
            (row.sequence, row.event, row.payload)
            for row in session.scalars(
                select(RunEventRecord)
                .where(RunEventRecord.run_id == ident)
                .order_by(RunEventRecord.sequence)
            )
        ]


def result(db, ident):
    with db.sessions() as session:
        row = session.get(RunRecord, ident)
        return {
            "status": row.status,
            "attempts": row.attempt_count,
            "results": row.results,
            "state": row.agent_state,
            "warnings": row.warnings,
        }


def cleanup(db, *idents):
    with db.sessions() as session, session.begin():
        session.execute(delete(RunRecord).where(RunRecord.id.in_(idents)))


def test_parallel_claim_only_one_owner(durable):
    db, _, queue = durable
    ident = add_run(db)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: queue.claim(), range(8)))
        winners = [c for c in claims if c and c.run_id == ident]
        assert len(winners) == 1 and winners[0].attempt == 1
        assert queue.renew(winners[0])
    finally:
        cleanup(db, ident)


def test_old_attempt_cannot_event_checkpoint_finish_fail_or_requeue(durable):
    db, settings, queue = durable
    ident = add_run(db)
    try:
        old = queue.claim()
        expire(db, ident)
        new = queue.claim()
        assert old.token != new.token and new.attempt == 2
        with pytest.raises(LeaseLost):
            queue.event(old, "late", "late", {})
        with pytest.raises(LeaseLost):
            queue.checkpoint(old, {})
        with pytest.raises(LeaseLost):
            queue.complete(old, {})
        assert not queue.renew(old)
        queue.fail(old)
        queue.abandon(old)
        assert queue.is_owned(new)
        execute_claim(db, settings, new)
        assert result(db, ident)["status"] == "completed"
        assert sum(name == "done" for _, name, _ in events(db, ident)) == 1
    finally:
        cleanup(db, ident)


def test_cancel_fences_active_task_and_is_idempotent(durable):
    db, _, queue = durable
    ident = add_run(db)
    try:
        claim = queue.claim()
        for _ in range(2):
            with db.sessions() as session, session.begin():
                assert queue.cancel(session, ident).status == "cancelled"
        assert not queue.renew(claim)
        with pytest.raises(LeaseLost):
            queue.checkpoint(claim, {})
        queue.fail(claim)
        queue.abandon(claim)
        assert result(db, ident)["status"] == "cancelled"
        assert sum(name == "done" for _, name, _ in events(db, ident)) == 1
        assert queue.claim() is None
    finally:
        cleanup(db, ident)


def test_expired_deadline_and_retry_limit_stop_recovery(durable):
    db, settings, queue = durable
    expired = add_run(db, deadline=-1)
    limited = add_run(db, attempt_count=settings.run_max_attempts)
    try:
        assert queue.claim() is None
        assert result(db, expired)["status"] == result(db, limited)["status"] == "failed"
        assert len(events(db, expired)) == 3
        assert len(events(db, limited)) == 3
    finally:
        cleanup(db, expired, limited)


def test_abandon_requeues_with_backoff_and_preserves_safe_checkpoint(durable):
    db, _, queue = durable
    ident = add_run(db)
    try:
        claim = queue.claim()
        queue.checkpoint(claim, {"version": CHECKPOINT_VERSION, "stage": "understand"})
        queue.abandon(claim)
        assert result(db, ident)["status"] == "queued"
        assert result(db, ident)["state"]["stage"] == "understand"
        assert queue.claim() is None
        expire(db, ident)
        assert queue.claim().attempt == 2
    finally:
        cleanup(db, ident)


def test_event_sequence_serialized_and_terminal_only_once(durable):
    db, _, queue = durable
    ident = add_run(db)
    try:
        claim = queue.claim()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: queue.event(claim, "probe", f"probe-{i}", {}), range(12)))
        queue.fail(claim)
        queue.fail(claim)
        rows = events(db, ident)
        assert [seq for seq, _, _ in rows] == list(range(1, len(rows) + 1))
        assert sum(name == "done" for _, name, _ in rows) == 1
    finally:
        cleanup(db, ident)


def initial_state(catalog):
    req = {"query": "太空探索", "country": "CN", "budget_minor": None}
    return {
        "request": req,
        "catalog": sorted(catalog, key=lambda g: g["id"]),
        "feedback": [],
        "intent": rule_intent(req),
        "mode": "rules",
        "warnings": [],
    }


@pytest.mark.parametrize(
    "change", ["checksum", "context", "version", "stage", "missing", "catalog", "model"]
)
def test_invalid_or_stale_snapshot_restarts_without_truncation(settings, catalog, change):
    state = initial_state(catalog)
    context = context_hash(state, settings)
    snapshot = snapshot_state(state, "understand", context)
    if change in {"checksum", "context", "version", "stage"}:
        snapshot[change] = "changed"
    elif change == "missing":
        snapshot["state"].pop("intent")
    elif change == "catalog":
        state["catalog"] = deepcopy(state["catalog"])
        state["catalog"][0]["description"] += "new"
        context = context_hash(state, settings)
    else:
        settings.llm_model = "different"
        context = context_hash(state, settings)
    restored, stage = restore_state(state, snapshot, context)
    assert stage is None and restored == state


def test_oversize_checkpoint_retains_previous_boundary(settings, catalog, monkeypatch):
    import playmatch.runs as runs

    state = initial_state(catalog)
    monkeypatch.setattr(runs, "MAX_CHECKPOINT_BYTES", 10)
    assert snapshot_state(state, "understand", context_hash(state, settings)) is None


def test_resume_skips_committed_intent_model(durable, catalog, monkeypatch):
    db, settings, queue = durable
    ident = add_run(db)
    try:
        old = queue.claim()
        state = initial_state(catalog)
        queue.checkpoint(old, snapshot_state(state, "understand", context_hash(state, settings)))
        expire(db, ident)
        new = queue.claim()
        monkeypatch.setattr(
            "playmatch.workflow.rule_intent", lambda _: pytest.fail("committed intent repeated")
        )
        execute_claim(db, settings, new)
        assert result(db, ident)["status"] == "completed"
        assert result(db, ident)["attempts"] == 2
        assert all("test-token" not in json.dumps(payload) for _, _, payload in events(db, ident))
    finally:
        cleanup(db, ident)


def test_resume_after_filter_rechecks_budget(settings, catalog, monkeypatch):
    config = settings.model_copy(update={"retrieval_mode": "bm25"})
    state = initial_state(catalog)
    first = build_graph(config, lambda *_: None).invoke(state)
    state.update(first)
    state["intent"]["budget_minor"] = 1000
    state["intent"]["country"] = "US"
    calls = []

    async def unavailable(candidates, country):
        calls.append(country)
        return [{"offers": []} for _ in candidates]

    monkeypatch.setattr("playmatch.workflow._prices_for", unavailable)
    output = build_graph(config, lambda *_: None, resume_after="filter").invoke(state)
    assert calls == ["US"]
    assert any("预算尚未核验" in s for row in output["results"] for s in row["tradeoffs"])


def test_cancel_api_authorization_csrf_and_replay(settings, catalog):
    settings.run_execution_mode = "external"
    with TestClient(create_app(settings, catalog=catalog)) as client:
        response = client.post(
            "/api/v1/runs", json={"query": "太空探索"}, headers={"Idempotency-Key": str(uuid4())}
        )
        ref = response.json()
        url = f"/api/v1/runs/{ref['id']}/cancel"
        headers = {"X-Run-Token": ref["access_token"]}
        assert client.post(url).status_code == 404
        assert (
            client.post(url, headers={**headers, "Origin": "https://evil.invalid"}).status_code
            == 403
        )
        cancelled = client.post(url, headers=headers).json()
        assert cancelled["status"] == "cancelled" and cancelled["attempt_count"] == 0
        assert client.post(url, headers=headers).json() == cancelled
        stream = client.get(f"/api/v1/runs/{ref['id']}/events", headers=headers).text
        assert stream.count("event: done") == 1 and '"cancelled"' in stream


def test_capacity_shared_across_api_instances(settings, catalog):
    settings.run_execution_mode = "external"
    settings.max_pending_runs = 1
    with (
        TestClient(create_app(settings, catalog=catalog)) as a,
        TestClient(create_app(settings, catalog=catalog)) as b,
    ):
        assert (
            a.post(
                "/api/v1/runs", json={"query": "太空"}, headers={"Idempotency-Key": str(uuid4())}
            ).status_code
            == 202
        )
        assert (
            b.post(
                "/api/v1/runs", json={"query": "经营"}, headers={"Idempotency-Key": str(uuid4())}
            ).status_code
            == 503
        )


def worker_env(settings):
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": settings.database_url,
            "PYTHONPATH": str(ROOT / "api"),
            "LLM_API_KEY": "",
            "LLM_MODEL": "",
            "RETRIEVAL_MODE": "bm25",
            "RUN_EXECUTION_MODE": "external",
            "RUN_LEASE_SECONDS": str(settings.run_lease_seconds),
        }
    )
    # ROOT is services, use exact source path regardless of test cwd.
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return env


def test_independent_supervised_worker_finishes_sql_job(durable):
    db, settings, _ = durable
    ident = add_run(db)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "playmatch.worker", "--once"],
            env=worker_env(settings),
            capture_output=True,
            timeout=20,
        )
        assert done.returncode == 0, done.stderr.decode()
        assert result(db, ident)["status"] == "completed"
        assert sum(name == "done" for _, name, _ in events(db, ident)) == 1
    finally:
        cleanup(db, ident)


def test_killed_process_recovered_from_committed_stage(durable, tmp_path):
    db, settings, queue = durable
    ident = add_run(db)
    marker = tmp_path / "safe-stage"
    script = tmp_path / "crash_probe.py"
    script.write_text(
        """
import time
from pathlib import Path
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.run_queue import RunQueue
from playmatch.runs import execute_claim
config = Settings()
db = Database(config.database_url)
queue = RunQueue(db, config)
claim = queue.claim()
original = RunQueue.checkpoint
def checkpoint(self, current, snapshot):
    original(self, current, snapshot)
    if snapshot["stage"] == "understand":
        Path(MARKER).write_text("checkpoint-committed")
        time.sleep(60)
RunQueue.checkpoint = checkpoint
execute_claim(db, config, claim)
""".replace("MARKER", repr(str(marker)))
    )
    process = subprocess.Popen(
        [sys.executable, str(script)],
        env=worker_env(settings),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        until = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < until and process.poll() is None:
            time.sleep(0.05)
        assert marker.exists()
        process.kill()
        process.wait(timeout=5)
        assert process.returncode != 0
        expire(db, ident)  # Explicit test-controlled expiry, not a claimed real wait.
        recovered = subprocess.run(
            [sys.executable, "-m", "playmatch.worker", "--once"],
            env=worker_env(settings),
            capture_output=True,
            timeout=20,
        )
        assert recovered.returncode == 0, recovered.stderr.decode()
        assert result(db, ident)["status"] == "completed" and result(db, ident)["attempts"] == 2
        messages = [payload.get("message", "") for _, _, payload in events(db, ident)]
        assert any("已恢复 understand" in message for message in messages)
        assert messages.count("已解析主题与硬条件") == 1
        assert sum(name == "done" for _, name, _ in events(db, ident)) == 1
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stderr.close()
        cleanup(db, ident)


def test_legacy_running_row_without_lease_is_recovered(durable):
    db, settings, queue = durable
    ident = add_run(db, status="running")
    try:
        claim = queue.claim()
        assert claim.run_id == ident
        execute_claim(db, settings, claim)
        assert result(db, ident)["status"] == "completed"
    finally:
        cleanup(db, ident)


def test_two_independent_supervisors_do_not_duplicate_completion(durable):
    db, settings, _ = durable
    ident = add_run(db)
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "playmatch.worker", "--once"],
            env=worker_env(settings),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            _, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr.decode()
        assert result(db, ident)["status"] == "completed"
        assert result(db, ident)["attempts"] == 1
        assert sum(name == "done" for _, name, _ in events(db, ident)) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stderr.close()
        cleanup(db, ident)


def test_supervisor_terminates_real_stalled_provider_process(durable):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from playmatch.worker import supervise

    requested = threading.Event()

    class SlowProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            requested.set()
            time.sleep(6)  # Synthetic provider never produces a model response.
            self.close_connection = True

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    db, settings, queue = durable
    config = settings.model_copy(
        update={
            "llm_base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "llm_api_key": "synthetic-key",
            "llm_model": "test",
        }
    )
    ident = add_run(db, deadline=3)
    try:
        claim = queue.claim()
        started = time.monotonic()
        supervise(db, config, claim, threading.Event())
        assert requested.is_set(), "The isolated process never reached the stalled HTTP fixture"
        assert time.monotonic() - started < 6
        assert result(db, ident)["status"] == "failed"
        assert any("超时" in text for text in result(db, ident)["warnings"])
        assert sum(name == "done" for _, name, _ in events(db, ident)) == 1
    finally:
        server.shutdown()
        server.server_close()
        cleanup(db, ident)


@pytest.mark.parametrize("iteration", range(3))
def test_cancel_finish_race_has_one_terminal_result(durable, iteration):
    import threading

    db, _, queue = durable
    ident = add_run(db)
    barrier = threading.Barrier(2)
    claim = queue.claim()

    def cancel():
        barrier.wait(timeout=2)
        with db.sessions() as session, session.begin():
            queue.cancel(session, ident)

    def complete():
        barrier.wait(timeout=2)
        try:
            queue.complete(claim, {"intent": {}, "results": [], "warnings": [], "mode": "rules"})
        except LeaseLost:
            pass

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(cancel), pool.submit(complete)]
            for task in tasks:
                task.result(timeout=5)
        rows = events(db, ident)
        assert sum(name == "done" for _, name, _ in rows) == 1
        assert sum(name == "result" for _, name, _ in rows) == 1
        assert result(db, ident)["status"] in {"cancelled", "partial"}
    finally:
        cleanup(db, ident)
