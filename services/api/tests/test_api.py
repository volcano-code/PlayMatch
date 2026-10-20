import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playmatch.main import create_app
from playmatch.models import RunEventRecord, RunRecord, SessionRecord, UserRecord
from playmatch.runs import now_iso
from playmatch.security import digest
from sqlalchemy import select


def register(client, email="player@example.com"):
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "correct-test-passphrase"}
    )
    assert response.status_code == 201, response.text
    return response


def start_run(client, query="经营放松", **kwargs):
    response = client.post(
        "/api/v1/runs",
        json={"query": query, "country": "US", **kwargs},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    headers = {"X-Run-Token": payload["access_token"]} if payload["access_token"] else {}
    return payload, headers


def wait_run(client, created, headers):
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{created['id']}", headers=headers)
        assert response.status_code == 200, response.text
        if response.json()["status"] in {"completed", "partial", "failed"}:
            return response.json()
        time.sleep(0.02)
    pytest.fail("run did not finish")


def test_health_catalog_and_honest_capabilities(client):
    assert client.get("/health").json()["catalog_count"] == 4
    result = client.get("/api/v1/games?q=经营").json()
    assert [game["id"] for game in result["items"]] == ["farm"]
    caps = client.get("/api/v1/capabilities").json()
    assert caps["agent_mode"] == "rules"
    assert caps["notification_monitoring"] == "not_enabled"
    assert client.get("/api/v1/games/missing").status_code == 404


def test_auth_hashes_credentials_and_cookie_logout(client):
    response = register(client)
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "password" not in response.json()
    with client.app.state.database.sessions() as db:
        user = db.scalar(select(UserRecord))
        assert user.password_hash.startswith("$argon2id$")
        stored = db.scalar(select(SessionRecord))
        assert stored.token_hash == digest(client.cookies["playmatch_session"])
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    wrong = client.post(
        "/api/v1/auth/login",
        json={"email": "player@example.com", "password": "wrong-long-password"},
    )
    assert wrong.status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "player@example.com", "password": "correct-test-passphrase"},
        ).status_code
        == 200
    )


def test_csrf_cross_origin_rejected(client):
    register(client)
    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200


def test_anon_run_capability_and_sse_replay(client):
    created, headers = start_run(client)
    run = wait_run(client, created, headers)
    assert run["status"] == "completed", run
    assert run["mode"] == "rules"
    assert run["results"][0]["game"]["id"] == "farm"
    assert client.get(f"/api/v1/runs/{created['id']}").status_code == 404
    assert (
        client.get(f"/api/v1/runs/{created['id']}", headers={"X-Run-Token": "bad"}).status_code
        == 404
    )
    stream = client.get(f"/api/v1/runs/{created['id']}/events", headers=headers)
    assert "event: result" in stream.text and "event: done" in stream.text
    ids = [int(line[4:]) for line in stream.text.splitlines() if line.startswith("id: ")]
    assert ids == sorted(set(ids))
    replay = client.get(
        f"/api/v1/runs/{created['id']}/events", headers={**headers, "Last-Event-ID": str(ids[-2])}
    )
    assert "event: done" in replay.text and "event: progress" not in replay.text
    assert f"id: {ids[-2]}\n" not in replay.text
    with client.app.state.database.sessions() as db:
        record = db.get(RunRecord, created["id"])
        assert record.capability_hash == digest(created["access_token"])
        assert created["access_token"] not in json.dumps(record.agent_state)


@pytest.mark.asyncio
async def test_sse_releases_auth_connection_before_streaming(client):
    from starlette.requests import Request

    created, headers = start_run(client)
    wait_run(client, created, headers)
    route = next(
        route
        for route in client.app.routes
        if getattr(route, "path", "") == "/api/v1/runs/{run_id}/events"
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/runs/{created['id']}/events",
            "headers": [(b"x-run-token", headers["X-Run-Token"].encode())],
            "app": client.app,
        }
    )
    database = client.app.state.database
    before = database.engine.pool.checkedout()
    with database.sessions() as auth_session:
        # Demonstrate that a real query holds a pool connection before the route.
        assert auth_session.get(RunRecord, created["id"]) is not None
        assert database.engine.pool.checkedout() == before + 1
        response = await route.endpoint(
            created["id"], request, last_event_id=None, session=auth_session
        )
        # The body has not been consumed; an SSE client could keep it open.
        assert response.media_type == "text/event-stream"
        assert not auth_session.in_transaction()
        assert database.engine.pool.checkedout() == before


def test_idempotency_reuses_run_and_rejects_changed_payload(client):
    key = str(uuid4())
    first = client.post(
        "/api/v1/runs", json={"query": "太空探索"}, headers={"Idempotency-Key": key}
    )
    again = client.post(
        "/api/v1/runs", json={"query": "太空探索"}, headers={"Idempotency-Key": key}
    )
    assert first.json()["id"] == again.json()["id"]
    assert first.json()["access_token"] == again.json()["access_token"]
    assert (
        client.post(
            "/api/v1/runs", json={"query": "经营"}, headers={"Idempotency-Key": key}
        ).status_code
        == 409
    )
    assert client.post("/api/v1/runs", json={"query": "经营"}).status_code == 422


def test_user_ownership_watchlist_feedback_and_owned_filter(client):
    register(client)
    created, headers = start_run(client)
    wait_run(client, created, headers)
    body = {"game_id": "farm", "target_minor": 1500, "currency": "USD", "country": "US"}
    assert client.post("/api/v1/watchlist", json=body).json()["monitoring_status"] == "not_enabled"
    assert client.post("/api/v1/watchlist", json=body).status_code == 200
    assert len(client.get("/api/v1/watchlist").json()["items"]) == 1
    assert (
        client.post("/api/v1/feedback", json={"game_id": "farm", "kind": "owned"}).status_code
        == 201
    )
    owned_run, owned_headers = start_run(client)
    assert wait_run(client, owned_run, owned_headers)["results"] == []
    included_run, included_headers = start_run(client, exclude_owned=False)
    assert wait_run(client, included_run, included_headers)["results"][0]["game"]["id"] == "farm"
    client.post("/api/v1/auth/logout")
    register(client, "another@example.com")
    assert client.get(f"/api/v1/runs/{created['id']}").status_code == 404
    assert client.get("/api/v1/watchlist").json()["items"] == []
    assert client.get("/api/v1/feedback").json()["items"] == []


def test_budget_region_uncertainty_and_invalid_currency(client):
    created, headers = start_run(client, country="CN", budget_minor=10000)
    result = wait_run(client, created, headers)
    assert result["status"] == "partial"
    assert any("预算尚未核验" in item for item in result["results"][0]["tradeoffs"])
    response = client.get("/api/v1/games/farm/offers?country=CN")
    assert response.status_code == 200 and response.json()["supported"] is False
    assert response.json()["offers"] == []
    assert (
        client.post(
            "/api/v1/watchlist", json={"game_id": "farm", "country": "CN", "currency": "USD"}
        ).status_code
        == 422
    )


def test_provider_unavailable_remains_explicit(client, monkeypatch):
    async def unavailable(self, app_id, country="US"):
        return {
            "supported": True,
            "country": country,
            "currency": "USD",
            "offers": [],
            "best_offer_id": None,
            "fetched_at": None,
            "status": "unavailable",
            "notices": ["fixture timeout"],
        }

    monkeypatch.setattr("playmatch.providers.cheapshark.CheapSharkClient.get_offers", unavailable)
    assert client.get("/api/v1/games/farm/offers").json()["status"] == "unavailable"
    created, headers = start_run(client, budget_minor=1000)
    assert wait_run(client, created, headers)["status"] == "partial"


def test_service_restart_marks_interrupted_run_failed(settings, catalog):
    with TestClient(create_app(settings, catalog=catalog)) as client:
        with client.app.state.database.sessions() as db:
            record = RunRecord(
                id=str(uuid4()),
                user_id=None,
                capability_hash=digest("fixture-token"),
                idempotency_scope="fixture",
                idempotency_key_hash=digest("fixture"),
                payload_hash=digest("fixture"),
                query="经营",
                request_payload={"query": "经营"},
                status="running",
                mode="rules",
                intent={},
                results=[],
                warnings=[],
                agent_state={"last_stage": "retrieve"},
                created_at=now_iso(),
                completed_at=None,
            )
            db.add(record)
            db.commit()
            run_id = record.id
    with TestClient(create_app(settings, catalog=catalog)) as client:
        result = client.get(
            f"/api/v1/runs/{run_id}", headers={"X-Run-Token": "fixture-token"}
        ).json()
        assert result["status"] == "failed"
        assert any("中断" in item for item in result["warnings"])
        with client.app.state.database.sessions() as db:
            assert [
                row.event
                for row in db.scalars(
                    select(RunEventRecord)
                    .where(RunEventRecord.run_id == run_id)
                    .order_by(RunEventRecord.sequence)
                )
            ] == ["error", "done"]


def test_task_queue_has_a_real_bound(settings, catalog, monkeypatch):
    import threading

    hold = threading.Event()
    started = threading.Event()

    def blocked(_self, _run_id):
        started.set()
        assert hold.wait(timeout=5)

    settings.max_pending_runs = 1
    monkeypatch.setattr("playmatch.runs.RunExecutor.execute", blocked)
    with TestClient(create_app(settings, catalog=catalog)) as client:
        try:
            assert (
                client.post(
                    "/api/v1/runs",
                    json={"query": "经营"},
                    headers={"Idempotency-Key": str(uuid4())},
                ).status_code
                == 202
            )
            assert started.wait(timeout=2)
            full = client.post(
                "/api/v1/runs", json={"query": "太空"}, headers={"Idempotency-Key": str(uuid4())}
            )
            assert full.status_code == 503
            assert full.headers["retry-after"] == "10"
        finally:
            hold.set()


def test_run_timeout_records_terminal_event(client, monkeypatch):
    class TimeoutGraph:
        def invoke(self, *_args, **_kwargs):
            raise TimeoutError("fixture deadline")

    monkeypatch.setattr("playmatch.runs.build_graph", lambda *_args: TimeoutGraph())
    created, headers = start_run(client)
    result = wait_run(client, created, headers)
    assert result["status"] == "failed"
    assert result["results"] == []
    assert any("超时" in warning for warning in result["warnings"])
    events = client.get(f"/api/v1/runs/{created['id']}/events", headers=headers).text
    assert "event: error" in events and "event: done" in events
