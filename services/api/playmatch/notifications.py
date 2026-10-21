"""One-shot, explicitly consented notifications from the user's own price queries.

No price polling, external email, webhooks, or user-supplied quote ingestion. The
outbox delivers only to our SQL inbox, so notification + delivery state are atomic.
All writers lock before reading mutable watch/event state, including on SQLite.
"""

import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime
from secrets import token_hex
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from playmatch.models import AlertLock, NotificationRecord, PriceAlertEvent, WatchRecord
from playmatch.run_queue import database_now, now_iso
from playmatch.schemas import AlertInput, NotificationPayload

CONSENT_VERSION = "observed-usd-inapp-v1"
MAX_ATTEMPTS = 5
LEASE_SECONDS = 30
QUOTE_MAX_AGE = 120
EVENT_MAX_AGE = 600
logger = logging.getLogger(__name__)


def lock_alerts(session):
    count = session.execute(
        update(AlertLock).where(AlertLock.id == 1).values(revision=AlertLock.revision)
    )
    if count.rowcount != 1:
        raise RuntimeError("Notification migration required")


def alert_payload(watch: WatchRecord) -> dict:
    return {
        "state": watch.alert_state,
        "version": watch.alert_version,
        "consent_version": watch.alert_consent,
        "mode": "own_query_observation",
        "automatic_price_polling": False,
        "external_email": False,
    }


def invalidate_alert(session, watch: WatchRecord):
    """Call under the alert lock when target/region changes or watch is deleted."""
    session.execute(
        update(PriceAlertEvent)
        .where(
            PriceAlertEvent.watch_id == watch.id,
            PriceAlertEvent.status.in_(["pending", "sending"]),
        )
        .values(
            status="suppressed",
            failure_code="subscription_changed",
            lease_token=None,
            lease_until=None,
        )
    )
    watch.alert_version += 1
    watch.alert_state, watch.alert_consent = "off", None
    watch.alert_armed_at, watch.alert_command = None, None
    watch.monitoring_enabled = False  # Scheduled price monitoring is still not implemented.


def configure_alert(session, user_id: str, game_id: str, body: AlertInput) -> WatchRecord:
    lock_alerts(session)
    watch = session.scalar(
        select(WatchRecord)
        .where(
            WatchRecord.user_id == user_id,
            WatchRecord.game_id == game_id,
        )
        .execution_options(populate_existing=True)
    )
    if watch is None:
        raise HTTPException(404, "请先收藏这款游戏并设置目标价")
    command = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
    # Lost-response replay returns current state without rearming a delivered rule.
    if watch.alert_command == command and watch.alert_version == body.expected_version + 1:
        return watch
    if watch.alert_version != body.expected_version:
        raise HTTPException(409, "提醒设置已变化，请刷新收藏后重试")
    if body.enabled and (
        watch.country != "US" or watch.currency != "USD" or watch.target_minor is None
    ):
        raise HTTPException(422, "查价提醒仅支持已设置 USD 目标价的美国地区收藏")
    invalidate_alert(session, watch)
    watch.alert_command = command
    if body.enabled:
        watch.alert_state, watch.alert_consent = "armed", CONSENT_VERSION
        watch.alert_armed_at = database_now(session)
    return watch


def timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed.timestamp()


def qualifying_offer(result: dict, started_at: float, now: float) -> dict | None:
    """Only server-adapter results enter here; this is deliberately not an API."""
    eligible = []
    for raw in result.get("offers", [])[:60]:
        try:
            if (
                not isinstance(raw, dict)
                or raw.get("provider") != "cheapshark"
                or raw.get("country") != "US"
                or raw.get("currency") != "USD"
                or raw.get("activation_status") == "unsupported"
                or type(raw.get("price_minor")) is not int
                or not 0 <= raw["price_minor"] <= 1_000_000
            ):
                continue
            observed = timestamp(raw["fetched_at"])
            if not started_at - 1 <= observed <= now + 5 or now - observed > QUOTE_MAX_AGE:
                continue
            if raw.get("source_updated_at") is not None:
                source_time = timestamp(raw["source_updated_at"])
                if source_time > now + 5:
                    continue
            # Reuse the strict schema for links, money and source fields.
            NotificationPayload.model_validate(
                {
                    "game_id": "validation",
                    "game_title": "validation",
                    "target_minor": 0,
                    "price_minor": raw["price_minor"],
                    "store": raw["store"],
                    "offer_url": raw["url"],
                    "observed_at": raw["fetched_at"],
                    "source_updated_at": raw.get("source_updated_at"),
                }
            )
            eligible.append(raw)
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return min(eligible, key=lambda row: (row["price_minor"], row["url"])) if eligible else None


def record_observation(session, user_id: str, game: dict, result: dict, started_at: float) -> str:
    """Commit this transaction before telling the caller an event was queued."""
    lock_alerts(session)
    now = database_now(session)
    watch = session.scalar(
        select(WatchRecord)
        .where(
            WatchRecord.user_id == user_id,
            WatchRecord.game_id == game["id"],
        )
        .execution_options(populate_existing=True)
    )
    if watch is None or watch.alert_state == "off":
        return "disabled"
    if watch.alert_state != "armed":
        return "already_triggered"
    if (
        watch.alert_consent != CONSENT_VERSION
        or watch.alert_armed_at is None
        or not math.isfinite(started_at)
        or started_at < watch.alert_armed_at
        or started_at > now + 1
        or now - started_at > QUOTE_MAX_AGE
    ):
        return "stale"
    if (
        watch.currency != "USD"
        or watch.country != "US"
        or result.get("country") != "US"
        or result.get("currency") != "USD"
        or result.get("supported") is not True
    ):
        return "unsupported"
    if result.get("status") not in {"ok", "partial"}:
        return "unavailable"
    offer = qualifying_offer(result, started_at, now)
    if offer is None:
        return "no_qualifying_offers"
    if watch.target_minor is None or offer["price_minor"] > watch.target_minor:
        return "above_target"
    payload = NotificationPayload(
        game_id=game["id"],
        game_title=(game["title_zh"] or game["title"])[:300],
        price_minor=offer["price_minor"],
        target_minor=watch.target_minor,
        store=offer["store"],
        offer_url=offer["url"],
        observed_at=offer["fetched_at"],
        source_updated_at=offer.get("source_updated_at"),
    ).model_dump()
    session.add(
        PriceAlertEvent(
            id=str(uuid4()),
            user_id=user_id,
            watch_id=watch.id,
            alert_version=watch.alert_version,
            payload=payload,
            status="pending",
            created_at=now_iso(),
            expires_at=started_at + EVENT_MAX_AGE,
            available_at=now,
            attempts=0,
        )
    )
    watch.alert_state = "pending"
    session.flush()
    return "queued"


@dataclass(frozen=True)
class DeliveryClaim:
    event_id: str
    token: str


class NotificationOutbox:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _watch(session, event):
        watch = session.get(WatchRecord, event.watch_id)
        if (
            watch is not None
            and watch.user_id == event.user_id
            and watch.alert_version == event.alert_version
            and watch.alert_state == "pending"
            and watch.alert_consent == CONSENT_VERSION
        ):
            return watch
        return None

    def claim(self) -> DeliveryClaim | None:
        with self.database.sessions() as session, session.begin():
            lock_alerts(session)
            now = database_now(session)
            event = session.scalar(
                select(PriceAlertEvent)
                .where(
                    or_(
                        and_(
                            PriceAlertEvent.status == "pending", PriceAlertEvent.available_at <= now
                        ),
                        and_(
                            PriceAlertEvent.status == "sending", PriceAlertEvent.lease_until <= now
                        ),
                    )
                )
                .order_by(PriceAlertEvent.created_at, PriceAlertEvent.id)
                .limit(1)
            )
            if event is None:
                return None
            watch = self._watch(session, event)
            if watch is None:
                event.status, event.failure_code = "suppressed", "subscription_changed"
                return None
            if event.expires_at <= now or event.attempts >= MAX_ATTEMPTS:
                event.status = watch.alert_state = "failed"
                event.failure_code = (
                    "quote_expired" if event.expires_at <= now else "attempts_exhausted"
                )
                return None
            event.status, event.lease_token = "sending", token_hex(24)
            event.lease_until, event.attempts = now + LEASE_SECONDS, event.attempts + 1
            return DeliveryClaim(event.id, event.lease_token)

    def deliver(self, claim: DeliveryClaim) -> bool:
        with self.database.sessions() as session, session.begin():
            lock_alerts(session)
            now = database_now(session)
            event = session.get(PriceAlertEvent, claim.event_id)
            if (
                event is None
                or event.status != "sending"
                or event.lease_token != claim.token
                or event.lease_until is None
                or event.lease_until <= now
            ):
                return False
            watch = self._watch(session, event)
            if watch is None:
                event.status, event.failure_code = "suppressed", "subscription_changed"
                return False
            if event.expires_at <= now:
                event.status = watch.alert_state = "failed"
                event.failure_code = "quote_expired"
                return False
            try:
                payload = NotificationPayload.model_validate(event.payload)
                if (
                    payload.game_id != watch.game_id
                    or payload.target_minor != watch.target_minor
                    or payload.price_minor > payload.target_minor
                    or watch.currency != "USD"
                    or watch.country != "US"
                ):
                    raise ValueError("Mismatched delivery snapshot")
            except (ValidationError, ValueError, TypeError):
                event.status = watch.alert_state = "failed"
                event.failure_code = "invalid_snapshot"
                return False
            session.add(
                NotificationRecord(
                    id=str(uuid4()),
                    user_id=event.user_id,
                    event_id=event.id,
                    payload=payload.model_dump(),
                    created_at=now_iso(),
                    read_at=None,
                )
            )
            # The unique event_id is a second line of defense; all three changes
            # commit together or none do. No external delivery is claimed.
            event.status, event.failure_code = "delivered", None
            event.lease_token, event.lease_until = None, None
            watch.alert_state = "notified"
            return True

    def retry(self, claim: DeliveryClaim):
        with self.database.sessions() as session, session.begin():
            lock_alerts(session)
            event = session.get(PriceAlertEvent, claim.event_id)
            if event is None or event.status != "sending" or event.lease_token != claim.token:
                return
            now = database_now(session)
            watch = self._watch(session, event)
            if watch is None:
                event.status, event.failure_code = "suppressed", "subscription_changed"
            elif event.attempts >= MAX_ATTEMPTS or event.expires_at <= now:
                event.status = watch.alert_state = "failed"
                event.failure_code = (
                    "attempts_exhausted" if event.attempts >= MAX_ATTEMPTS else "quote_expired"
                )
            else:
                event.status, event.failure_code = "pending", "database_write_failed"
                event.available_at = now + min(2**event.attempts, 60)
            event.lease_token, event.lease_until = None, None

    def process_one(self) -> bool:
        claim = self.claim()
        if claim is None:
            return False
        try:
            return self.deliver(claim)
        except SQLAlchemyError:
            logger.warning("notification_delivery_write_failed")
            self.retry(claim)  # If DB is down, lease reclamation handles recovery.
            return False


class NotificationConsumer:
    """Embedded development consumer. External mode uses the independent worker."""

    def __init__(self, database):
        self.outbox = NotificationOutbox(database)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="notification-consumer")
        self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            try:
                self.outbox.process_one()
            except SQLAlchemyError:
                logger.warning("notification_database_unavailable")
            self.stop.wait(0.5)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
