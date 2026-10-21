"""Real SQL outbox tests; quotes are deterministic fixtures, not live prices."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from playmatch.database import Database
from playmatch.main import create_app
from playmatch.models import (
    GameRecord,
    NotificationRecord,
    PriceAlertEvent,
    UserRecord,
    WatchRecord,
)
from playmatch.notifications import (
    CONSENT_VERSION,
    MAX_ATTEMPTS,
    NotificationOutbox,
    configure_alert,
    invalidate_alert,
    lock_alerts,
    record_observation,
)
from playmatch.run_queue import database_now, now_iso
from playmatch.schemas import AlertInput
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import OperationalError


@pytest.fixture
def alerts(settings, catalog):
    url = os.getenv("ALERT_TEST_DATABASE_URL", settings.database_url)
    # External database must be dedicated. Only this fixture's own user data is removed.
    if os.getenv("ALERT_TEST_DATABASE_URL"):
        assert os.getenv("PLAYMATCH_ALLOW_DISPOSABLE_DB") == "yes"
        assert url.rsplit("/", 1)[-1].endswith("_test")
    db = Database(url)
    db.migrate()
    user = str(uuid4())
    with db.sessions() as s, s.begin():
        for game in catalog:
            if s.get(GameRecord, game["id"]) is None:
                s.add(GameRecord(id=game["id"], title=game["title"], payload=game))
        s.add(
            UserRecord(
                id=user,
                email=f"{user}@example.test",
                password_hash="test-only",
                created_at=now_iso(),
            )
        )
        s.flush()
        s.add(
            WatchRecord(
                id=str(uuid4()),
                user_id=user,
                game_id="space",
                target_minor=1000,
                currency="USD",
                country="US",
                created_at=now_iso(),
            )
        )
    yield db, user, catalog[1]
    with db.sessions() as s, s.begin():
        for model in (NotificationRecord, PriceAlertEvent, WatchRecord):
            s.execute(delete(model).where(model.user_id == user))
        s.execute(delete(UserRecord).where(UserRecord.id == user))
    db.close()


def configure(alerts, enabled=True, version=0):
    db, user, _ = alerts
    with db.sessions() as s, s.begin():
        w = configure_alert(
            s,
            user,
            "space",
            AlertInput(
                enabled=enabled,
                expected_version=version,
                consent_version=CONSENT_VERSION if enabled else None,
            ),
        )
        return w.alert_version, w.alert_state


def quote(price=999, **changes):
    offer = {
        "id": "fixture-offer",
        "provider": "cheapshark",
        "store": "Fixture Store",
        "price_minor": price,
        "regular_price_minor": 1999,
        "currency": "USD",
        "country": "US",
        "url": "https://www.cheapshark.com/redirect?dealID=fixture-only",
        "fetched_at": now_iso(),
        "source_updated_at": None,
        "edition": "unknown",
        "activation_status": "unknown",
        "eligible_for_best": False,
    }
    offer.update(changes)
    return {
        "supported": True,
        "country": "US",
        "currency": "USD",
        "status": "partial",
        "offers": [offer],
        "best_offer_id": None,
        "fetched_at": now_iso(),
        "notices": ["fixture only"],
    }


def observe(alerts, result=None, *, started=None):
    db, user, game = alerts
    with db.sessions() as s, s.begin():
        started = database_now(s) if started is None else started
        return record_observation(s, user, game, result or quote(), started)


def count(db, model):
    with db.sessions() as s:
        return s.scalar(select(func.count()).select_from(model))


def state(alerts):
    db, user, _ = alerts
    with db.sessions() as s:
        w = s.scalar(select(WatchRecord).where(WatchRecord.user_id == user))
        return w.alert_state, w.alert_version


def test_explicit_consent_default_off_and_no_auto_network(alerts, monkeypatch):
    monkeypatch.setattr("httpx.Client.request", lambda *_a, **_k: pytest.fail("unexpected network"))
    assert observe(alerts) == "disabled"
    assert not NotificationOutbox(alerts[0]).process_one()
    with pytest.raises(ValueError):
        AlertInput(enabled=True, expected_version=0)
    assert configure(alerts) == (1, "armed")
    assert count(alerts[0], PriceAlertEvent) == 0


def test_one_shot_idempotent_delivery_and_receipt(alerts):
    db = alerts[0]
    configure(alerts)
    assert observe(alerts) == "queued"
    assert observe(alerts) == "already_triggered"
    outbox = NotificationOutbox(db)
    claim = outbox.claim()
    assert outbox.deliver(claim)
    assert not outbox.deliver(claim)
    assert configure(alerts) == (1, "notified")  # exact lost-response replay never rearms
    assert observe(alerts) == "already_triggered"
    assert count(db, NotificationRecord) == count(db, PriceAlertEvent) == 1
    assert state(alerts) == ("notified", 1)


@pytest.mark.parametrize("price", [0, 999, 1000])
def test_inclusive_integer_price_threshold(alerts, price):
    configure(alerts)
    assert observe(alerts, quote(price)) == "queued"


def test_above_target_then_below(alerts):
    configure(alerts)
    assert observe(alerts, quote(1001)) == "above_target"
    assert state(alerts)[0] == "armed"
    assert observe(alerts, quote(999)) == "queued"


@pytest.mark.parametrize(
    "field,value",
    [
        ("price_minor", True),
        ("price_minor", 1.5),
        ("price_minor", -1),
        ("price_minor", "9.99"),
        ("price_minor", 1000001),
        ("currency", "CNY"),
        ("country", "CN"),
        ("provider", "unknown"),
        ("activation_status", "unsupported"),
        ("url", "javascript:alert(1)"),
        ("url", "https://evil.invalid/redirect?dealID=x"),
        ("url", "https://www.cheapshark.com/redirect?dealID=x&dealID=y"),
        ("url", "https://www.cheapshark.com/redirect?dealID=x&token=secret"),
        ("fetched_at", "2020-01-01T00:00:00Z"),
        ("fetched_at", "2099-01-01T00:00:00Z"),
        ("fetched_at", "2026-01-01T00:00:00"),
        ("source_updated_at", "2099-01-01T00:00:00Z"),
    ],
)
def test_invalid_or_stale_offer_never_notifies(alerts, field, value):
    configure(alerts)
    assert observe(alerts, quote(**{field: value})) == "no_qualifying_offers"
    assert count(alerts[0], PriceAlertEvent) == 0


@pytest.mark.parametrize(
    "code,expected", [("unavailable", "unavailable"), ("unsupported", "unavailable")]
)
def test_provider_failure_keeps_armed(alerts, code, expected):
    configure(alerts)
    result = quote()
    result["status"] = code
    assert observe(alerts, result) == expected
    assert state(alerts)[0] == "armed"


def test_unsupported_envelope_is_not_currency_conversion(alerts):
    configure(alerts)
    result = quote()
    result["country"] = "CN"
    assert observe(alerts, result) == "unsupported"


def test_request_began_before_consent_or_after_max_age(alerts):
    db = alerts[0]
    configure(alerts)
    with db.sessions() as s:
        now = database_now(s)
    assert observe(alerts, started=now - 1000) == "stale"
    assert observe(alerts, started=now + 1000) == "stale"


def test_concurrent_observation_and_workers_exactly_one_inbox(alerts):
    configure(alerts)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: observe(alerts), range(12)))
    assert results.count("queued") == 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        deliveries = list(
            pool.map(lambda _: NotificationOutbox(alerts[0]).process_one(), range(12))
        )
    assert sum(deliveries) == count(alerts[0], NotificationRecord) == 1


def test_two_real_worker_processes_deliver_after_database_reopen(alerts):
    db = alerts[0]
    configure(alerts)
    observe(alerts)
    env = {
        **os.environ,
        "DATABASE_URL": db.engine.url.render_as_string(hide_password=False),
        "RUN_EXECUTION_MODE": "external",
        "LLM_API_KEY": "",
        "LLM_MODEL": "",
    }
    workers = [
        subprocess.Popen(
            [sys.executable, "-m", "playmatch.worker", "--once"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    for worker in workers:
        try:
            worker.communicate(timeout=25)
            assert worker.returncode == 0
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()
    assert count(db, NotificationRecord) == 1


def test_reclaimed_lease_rejects_old_owner(alerts):
    db = alerts[0]
    configure(alerts)
    observe(alerts)
    outbox = NotificationOutbox(db)
    first = outbox.claim()
    with db.sessions() as s, s.begin():
        s.get(PriceAlertEvent, first.event_id).lease_until = 0
    second = outbox.claim()
    assert first.token != second.token
    assert not outbox.deliver(first)
    assert outbox.deliver(second)
    assert count(db, NotificationRecord) == 1


def test_close_while_claimed_suppresses_and_rearm_is_explicit(alerts):
    db = alerts[0]
    configure(alerts)
    observe(alerts)
    outbox = NotificationOutbox(db)
    claim = outbox.claim()
    assert configure(alerts, False, 1) == (2, "off")
    assert not outbox.deliver(claim)
    assert count(db, NotificationRecord) == 0
    configure(alerts, True, 2)
    observe(alerts)
    assert outbox.process_one()
    assert count(db, NotificationRecord) == 1


def test_cancel_delivery_race_has_consistent_terminal_result(alerts):
    db = alerts[0]
    configure(alerts)
    observe(alerts)
    outbox = NotificationOutbox(db)
    claim = outbox.claim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(outbox.deliver, claim)
        b = pool.submit(configure, alerts, False, 1)
        delivered, _ = a.result(), b.result()
    assert count(db, NotificationRecord) == int(delivered)
    assert state(alerts)[0] == "off"


def test_changed_target_revokes_queued_snapshot(alerts):
    db, user, _ = alerts
    configure(alerts)
    observe(alerts)
    with db.sessions() as s, s.begin():
        lock_alerts(s)
        w = s.scalar(select(WatchRecord).where(WatchRecord.user_id == user))
        invalidate_alert(s, w)
        w.target_minor = 900
    assert not NotificationOutbox(db).process_one()
    assert count(db, NotificationRecord) == 0


def test_expired_event_and_bad_snapshot_never_deliver(alerts):
    db = alerts[0]
    configure(alerts)
    observe(alerts)
    outbox = NotificationOutbox(db)
    claim = outbox.claim()
    with db.sessions() as s, s.begin():
        e = s.get(PriceAlertEvent, claim.event_id)
        e.payload = {**e.payload, "offer_url": "javascript:x"}
    assert not outbox.deliver(claim)
    assert state(alerts)[0] == "failed"
    configure(alerts, True, 1)
    observe(alerts)
    with db.sessions() as s, s.begin():
        s.execute(
            update(PriceAlertEvent).where(PriceAlertEvent.status == "pending").values(expires_at=0)
        )
    assert not outbox.process_one()
    assert count(db, NotificationRecord) == 0


def test_transient_commit_failure_retry_exhaustion(alerts, monkeypatch):
    db = alerts[0]
    configure(alerts)
    observe(alerts)
    outbox = NotificationOutbox(db)

    def fail(_):
        raise OperationalError("synthetic commit", {}, Exception("not logged"))

    monkeypatch.setattr(outbox, "deliver", fail)
    for _ in range(MAX_ATTEMPTS):
        assert not outbox.process_one()
        with db.sessions() as s, s.begin():
            s.execute(
                update(PriceAlertEvent)
                .where(PriceAlertEvent.status == "pending")
                .values(available_at=0)
            )
    assert state(alerts)[0] == "failed"
    assert count(db, NotificationRecord) == 0


def test_deleted_notification_does_not_redeliver(alerts):
    db = alerts[0]
    configure(alerts)
    observe(alerts)
    outbox = NotificationOutbox(db)
    assert outbox.process_one()
    with db.sessions() as s, s.begin():
        s.execute(delete(NotificationRecord))
    assert not outbox.process_one() and observe(alerts) == "already_triggered"


def test_stale_command_rejected(alerts):
    configure(alerts)
    configure(alerts, False, 1)
    with pytest.raises(HTTPException) as error:
        configure(alerts)
    assert error.value.status_code == 409


def test_regional_and_null_target_gate(alerts):
    db, user, _ = alerts
    for currency, country, target in (("CNY", "CN", 1000), ("USD", "US", None)):
        with db.sessions() as s, s.begin():
            w = s.scalar(select(WatchRecord).where(WatchRecord.user_id == user))
            w.currency, w.country, w.target_minor = currency, country, target
        with pytest.raises(HTTPException) as error:
            configure(alerts)
        assert error.value.status_code == 422


def register(client, email=None):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email or f"{uuid4()}@example.test", "password": "Test-password-54321"},
    ).json()


def test_api_optin_check_inbox_privacy_and_delete(settings, catalog, monkeypatch):
    settings.run_execution_mode = "external"

    async def offers(*_args, **_kwargs):
        return quote()

    monkeypatch.setattr("playmatch.main.CheapSharkClient.get_offers", offers)
    with TestClient(create_app(settings, catalog=catalog)) as client:
        assert client.get("/api/v1/notifications").status_code == 401
        register(client)
        body = {"game_id": "space", "target_minor": 1000, "currency": "USD", "country": "US"}
        saved = client.post("/api/v1/watchlist", json=body).json()
        assert saved["alert"]["state"] == "off"
        assert (
            client.put(
                "/api/v1/watchlist/space/alert", json={"enabled": True, "expected_version": 0}
            ).status_code
            == 422
        )
        command = {"enabled": True, "expected_version": 0, "consent_version": CONSENT_VERSION}
        assert client.put("/api/v1/watchlist/space/alert", json=command).json()["state"] == "armed"
        assert client.get("/api/v1/games/space/offers").status_code == 200
        assert (
            count(client.app.state.database, PriceAlertEvent) == 0
        )  # Passive GET has no write effect.
        check = client.post("/api/v1/watchlist/space/check")
        assert check.status_code == 200 and check.json()["alert_evaluation"] == "queued"
        assert check.headers["cache-control"] == "no-store"
        assert client.post("/api/v1/watchlist/space/check").status_code == 409
        assert NotificationOutbox(client.app.state.database).process_one()
        listing = client.get("/api/v1/notifications").json()
        assert listing["total"] == listing["unread"] == 1
        notice = listing["items"][0]
        assert notice["payload"]["purchase_eligibility"] == "not_verified"
        path = "/api/v1/notifications/" + notice["id"]
        first = client.post(path + "/read").json()
        assert client.post(path + "/read").json()["read_at"] == first["read_at"]
        assert client.get("/api/v1/notifications").json()["unread"] == 0
        owner_cookies = dict(client.cookies)
        client.post("/api/v1/auth/logout")
        register(client)
        assert client.get("/api/v1/notifications").json()["total"] == 0
        assert client.post(path + "/read").status_code == 404
        assert client.delete(path).status_code == 204
        with client.app.state.database.sessions() as s:
            assert s.get(NotificationRecord, notice["id"]) is not None
        assert client.put("/api/v1/watchlist/space/alert", json=command).status_code == 404
        assert (
            client.put(
                "/api/v1/watchlist/space/alert",
                json=command,
                headers={"Origin": "https://evil.invalid"},
            ).status_code
            == 403
        )
        assert client.get("/api/v1/notifications?limit=1000").status_code == 422
        assert owner_cookies  # no tokens in notification responses


def test_api_target_edit_silences_old_rule_and_same_save_preserves(settings, catalog):
    with TestClient(create_app(settings, catalog=catalog)) as client:
        register(client)
        body = {"game_id": "space", "target_minor": 1000, "currency": "USD", "country": "US"}
        client.post("/api/v1/watchlist", json=body)
        client.put(
            "/api/v1/watchlist/space/alert",
            json={"enabled": True, "expected_version": 0, "consent_version": CONSENT_VERSION},
        )
        assert client.post("/api/v1/watchlist", json=body).json()["alert"]["state"] == "armed"
        assert (
            client.post("/api/v1/watchlist", json={**body, "target_minor": 900}).json()["alert"][
                "state"
            ]
            == "off"
        )


def test_api_inflight_revoke_prevents_old_price_event(settings, catalog, monkeypatch):
    settings.run_execution_mode = "external"
    with TestClient(create_app(settings, catalog=catalog)) as client:
        user = register(client)
        client.post(
            "/api/v1/watchlist",
            json={"game_id": "space", "target_minor": 1000, "currency": "USD", "country": "US"},
        )
        client.put(
            "/api/v1/watchlist/space/alert",
            json={"enabled": True, "expected_version": 0, "consent_version": CONSENT_VERSION},
        )

        async def slow_price(*_args, **_kwargs):
            with client.app.state.database.sessions() as s, s.begin():
                configure_alert(
                    s, user["id"], "space", AlertInput(enabled=False, expected_version=1)
                )
            return quote()

        monkeypatch.setattr("playmatch.main.CheapSharkClient.get_offers", slow_price)
        assert client.post("/api/v1/watchlist/space/check").json()["alert_evaluation"] == "stale"
        assert count(client.app.state.database, PriceAlertEvent) == 0


def test_actual_commit_failure_rolls_back_inbox_and_delivered_state(alerts):
    from sqlalchemy import event as sql_event

    db = alerts[0]
    configure(alerts)
    observe(alerts)
    outbox = NotificationOutbox(db)
    claim = outbox.claim()
    failed = False

    def break_commit(session):
        nonlocal failed
        if any(isinstance(row, NotificationRecord) for row in session.new):
            failed = True
            raise OperationalError("synthetic commit failure", {}, Exception("test only"))

    sql_event.listen(db.sessions, "before_commit", break_commit)
    try:
        with pytest.raises(OperationalError):
            outbox.deliver(claim)
    finally:
        sql_event.remove(db.sessions, "before_commit", break_commit)
    assert failed and count(db, NotificationRecord) == 0
    assert state(alerts)[0] == "pending"
    with db.sessions() as s:
        assert s.get(PriceAlertEvent, claim.event_id).status == "sending"
    outbox.retry(claim)
    with db.sessions() as s, s.begin():
        s.get(PriceAlertEvent, claim.event_id).available_at = 0
    assert outbox.process_one() and count(db, NotificationRecord) == 1


def test_observations_never_trigger_another_users_rule(alerts):
    db, user, game = alerts
    configure(alerts)
    with db.sessions() as s, s.begin():
        assert record_observation(s, "not-the-owner", game, quote(), database_now(s)) == "disabled"
    assert state(alerts)[0] == "armed" and count(db, PriceAlertEvent) == 0


def test_api_logout_during_price_request_does_not_write(settings, catalog, monkeypatch):
    from playmatch.models import SessionRecord

    settings.run_execution_mode = "external"
    with TestClient(create_app(settings, catalog=catalog)) as client:
        user = register(client)
        client.post("/api/v1/watchlist", json={"game_id": "space", "target_minor": 1000})
        client.put(
            "/api/v1/watchlist/space/alert",
            json={"enabled": True, "expected_version": 0, "consent_version": CONSENT_VERSION},
        )

        async def logout_before_price(*_args, **_kwargs):
            with client.app.state.database.sessions() as s, s.begin():
                s.execute(delete(SessionRecord).where(SessionRecord.user_id == user["id"]))
            return quote()

        monkeypatch.setattr("playmatch.main.CheapSharkClient.get_offers", logout_before_price)
        assert client.post("/api/v1/watchlist/space/check").status_code == 401
        assert count(client.app.state.database, PriceAlertEvent) == 0


def test_removing_watch_suppresses_pending_but_keeps_delivered_history(
    settings, catalog, monkeypatch
):
    settings.run_execution_mode = "external"

    async def offers(*_a, **_k):
        return quote()

    monkeypatch.setattr("playmatch.main.CheapSharkClient.get_offers", offers)
    with TestClient(create_app(settings, catalog=catalog)) as client:
        register(client)
        outbox = NotificationOutbox(client.app.state.database)
        for delivered in (False, True):
            client.post("/api/v1/watchlist", json={"game_id": "space", "target_minor": 1000})
            client.put(
                "/api/v1/watchlist/space/alert",
                json={"enabled": True, "expected_version": 0, "consent_version": CONSENT_VERSION},
            )
            client.post("/api/v1/watchlist/space/check")
            if delivered:
                assert outbox.process_one()
            assert client.delete("/api/v1/watchlist/space").status_code == 204
            assert not outbox.process_one()
            assert client.get("/api/v1/notifications").json()["total"] == int(delivered)


def test_inbox_keyset_order_unread_and_page_boundaries(settings, catalog, monkeypatch):
    settings.run_execution_mode = "external"

    async def offers(*_a, **_k):
        return quote()

    monkeypatch.setattr("playmatch.main.CheapSharkClient.get_offers", offers)
    with TestClient(create_app(settings, catalog=catalog)) as client:
        register(client)
        client.post("/api/v1/watchlist", json={"game_id": "space", "target_minor": 1000})
        for version in range(3):
            client.put(
                "/api/v1/watchlist/space/alert",
                json={
                    "enabled": True,
                    "expected_version": version,
                    "consent_version": CONSENT_VERSION,
                },
            )
            client.post("/api/v1/watchlist/space/check")
            assert NotificationOutbox(client.app.state.database).process_one()
        pages = [client.get(f"/api/v1/notifications?limit=1&offset={n}").json() for n in range(4)]
        assert len({p["items"][0]["id"] for p in pages[:3]}) == 3
        assert pages[3]["items"] == [] and pages[0]["total"] == pages[0]["unread"] == 3
