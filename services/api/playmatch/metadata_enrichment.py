"""Leased, budgeted automatic enrichment from discovered Steam identities.

One batch per database, no network transaction, source fencing, and atomic
metadata + lexical-index writes. It does not scrape Steam or call a language model.
"""

from dataclasses import dataclass
from secrets import token_hex

from sqlalchemy import case, func, select, update

from playmatch.catalog_import import digest
from playmatch.metadata_provider import lookup_batch
from playmatch.models import (
    CatalogLock,
    CatalogSource,
    EnrichmentJob,
    EnrichmentState,
    GameRecord,
    SteamDirectoryApp,
)
from playmatch.providers.cheapshark import ProviderRequestError
from playmatch.run_queue import database_now

LEASE_SECONDS = 90
REQUEST_RESERVATION = 6  # Upper bound incl. existing IGDB token/retry path, not actual calls.
RETRY_SECONDS = 86400


class EnrichmentLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class EnrichmentClaim:
    token: str
    identities: tuple[tuple[int, str], ...]


def source_hash(app) -> str:
    # Price-only signals/last_seen do not demand a new gameplay summary.
    return digest([app.appid, app.name, app.source_modified])


def lock_state(session):
    if (
        session.execute(
            update(EnrichmentState)
            .where(EnrichmentState.id == 1)
            .values(scan_cursor=EnrichmentState.scan_cursor)
        ).rowcount
        != 1
    ):
        raise RuntimeError("Metadata migration is required")
    return session.get(EnrichmentState, 1)


def lock_catalog(session):
    session.execute(
        update(CatalogLock).where(CatalogLock.id == 1).values(revision=CatalogLock.revision)
    )


class MetadataEnricher:
    def __init__(self, database, settings, client):
        self.database, self.settings, self.client = database, settings, client

    def claim(self) -> EnrichmentClaim | None:
        if not self.settings.metadata_enrichment_enabled or not self.settings.metadata_configured:
            return None
        with self.database.sessions() as session, session.begin():
            state = lock_state(session)
            now = database_now(session)
            state.heartbeat_at = now
            if state.lease_until > now:
                return None
            # Recover a batch whose owner died. Do not reset accumulated attempts.
            if state.lease_token:
                session.execute(
                    update(EnrichmentJob)
                    .where(EnrichmentJob.lease_token == state.lease_token)
                    .values(
                        status="pending",
                        lease_token=None,
                        available_at=case(
                            (EnrichmentJob.attempts >= 5, now + RETRY_SECONDS), else_=now
                        ),
                    )
                )
                state.lease_token = None
            if state.scan_due <= now:
                apps = list(
                    session.scalars(
                        select(SteamDirectoryApp)
                        .where(SteamDirectoryApp.appid > state.scan_cursor)
                        .order_by(SteamDirectoryApp.appid)
                        .limit(200)
                    )
                )
                for app in apps:
                    key = source_hash(app)
                    job = session.get(EnrichmentJob, app.appid)
                    if job is None:
                        session.add(
                            EnrichmentJob(
                                appid=app.appid, source_hash=key, status="pending", available_at=now
                            )
                        )
                    elif job.source_hash != key:
                        job.source_hash, job.status, job.available_at = key, "pending", now
                        job.attempts, job.last_error = 0, None
                        lock_catalog(session)
                        old_metadata = session.get(GameRecord, f"steam-{app.appid}")
                        if old_metadata and old_metadata.metadata_origin == "igdb_auto":
                            old_metadata.recommendation_blocked = True
                state.scan_cursor = apps[-1].appid if apps else 0
                state.scan_due = now + (1 if apps else 600)
                session.flush()
            if state.next_request_at > now:
                return None
            day = int(now // 86400)
            if day != state.request_day:
                state.request_day, state.reserved_requests = day, 0
            if (
                state.reserved_requests + REQUEST_RESERVATION
                > self.settings.metadata_daily_requests
            ):
                state.next_request_at, state.last_error = (
                    (day + 1) * 86400,
                    "daily_budget_exhausted",
                )
                return None
            # Skip max-attempt cooldown until due; a new window can retry, never hot-loop.
            jobs = list(
                session.scalars(
                    select(EnrichmentJob)
                    .where(EnrichmentJob.available_at <= now)
                    .order_by(EnrichmentJob.available_at, EnrichmentJob.appid)
                    .limit(self.settings.metadata_batch_size)
                )
            )
            if not jobs:
                return None
            state.lease_token, state.lease_until = token_hex(24), now + LEASE_SECONDS
            state.reserved_requests += REQUEST_RESERVATION
            state.next_request_at = now + 2
            identities = []
            for job in jobs:
                app = session.get(SteamDirectoryApp, job.appid)
                job.source_hash = source_hash(app)
                job.lease_token, job.status = state.lease_token, "processing"
                job.attempts = 1 if job.attempts >= 5 else job.attempts + 1
                identities.append((job.appid, job.source_hash))
            return EnrichmentClaim(state.lease_token, tuple(identities))

    @staticmethod
    def _owned(state, claim, now):
        if state.lease_token != claim.token or state.lease_until <= now:
            raise EnrichmentLeaseLost("Metadata owner expired")

    def apply(self, claim: EnrichmentClaim, results: dict[int, dict]) -> None:
        if set(results) != {i for i, _ in claim.identities}:
            raise ValueError("Incomplete batch response")
        with self.database.sessions() as session, session.begin():
            state = lock_state(session)
            now = database_now(session)
            self._owned(state, claim, now)
            lock_catalog(session)
            succeeded = False
            for appid, expected in claim.identities:
                job = session.get(EnrichmentJob, appid)
                if job.lease_token != claim.token or job.status != "processing":
                    raise EnrichmentLeaseLost("Metadata job superseded")
                app = session.scalar(
                    select(SteamDirectoryApp)
                    .where(SteamDirectoryApp.appid == appid)
                    .with_for_update()
                )
                job.lease_token = None
                record = session.get(GameRecord, f"steam-{appid}")
                if source_hash(app) != expected:
                    job.source_hash, job.status, job.available_at = source_hash(app), "pending", now
                    job.last_error = "discovery_changed"
                    if record and record.metadata_origin == "igdb_auto":
                        record.recommendation_blocked = True
                    continue
                value = results[appid]
                error = value.get("error")
                if error:
                    job.status = "unmatched" if error == "unmatched" else "rejected"
                    job.last_error = error[:48]
                    job.available_at = now + RETRY_SECONDS
                    if record and record.metadata_origin == "igdb_auto":
                        record.recommendation_blocked = True
                    continue
                # Never overwrite curated/operator-imported records with automated data.
                if record and (
                    record.metadata_origin != "igdb_auto"
                    or record.payload.get("metadata_status") == "curated"
                ):
                    job.status, job.last_error, job.last_success = "preserved", None, now
                    job.available_at = now + self.settings.metadata_refresh_seconds
                    succeeded = True
                    continue
                provider_id = value["provider_id"]
                source = session.get(CatalogSource, provider_id)
                owner = session.scalar(
                    select(CatalogSource).where(CatalogSource.game_id == f"steam-{appid}")
                )
                if (source and source.game_id != f"steam-{appid}") or (
                    owner and owner.provider_id != provider_id
                ):
                    job.status, job.last_error, job.available_at = (
                        "rejected",
                        "identity_conflict",
                        now + RETRY_SECONDS,
                    )
                    if record:
                        record.recommendation_blocked = True
                    continue
                if job.provider_updated is not None and (
                    value["updated_at"] < job.provider_updated
                    or (
                        value["updated_at"] == job.provider_updated
                        and value["hash"] != job.provider_hash
                    )
                ):
                    job.status, job.last_error, job.available_at = (
                        "rejected",
                        "stale_provider_data",
                        now + RETRY_SECONDS,
                    )
                    continue
                from playmatch.schemas import Game

                payload = Game.model_validate(value["payload"]).model_dump()
                if (
                    payload["steam_app_id"] != appid
                    or payload["id"] != f"steam-{appid}"
                    or payload["metadata_status"] != "provider"
                ):
                    raise ValueError("Unsafe normalized identity")
                if record is None:
                    record = GameRecord(
                        id=payload["id"],
                        title=payload["title"],
                        payload=payload,
                        metadata_origin="igdb_auto",
                    )
                    session.add(record)
                else:
                    record.title, record.payload = payload["title"], payload
                record.metadata_valid_until = now + self.settings.metadata_refresh_seconds
                record.recommendation_blocked = False
                session.flush()  # Includes transactional postings via ORM hook.
                if source is None:
                    session.add(
                        CatalogSource(
                            provider_id=provider_id,
                            game_id=record.id,
                            observed_at=value["observed_at"],
                            content_hash=value["hash"],
                        )
                    )
                else:
                    source.observed_at, source.content_hash = value["observed_at"], value["hash"]
                job.provider_updated, job.provider_hash = value["updated_at"], value["hash"]
                job.last_success, job.status, job.last_error, job.attempts = now, "ready", None, 0
                job.available_at = now + self.settings.metadata_refresh_seconds
                succeeded = True
            if succeeded:
                state.last_success = now
            state.lease_token, state.lease_until, state.last_error = None, 0, None

    def fail(self, claim: EnrichmentClaim, code: str) -> None:
        # Map arbitrary upstream values to a fixed public vocabulary, never response text.
        allowed = {
            "rate_limited",
            "authentication_failed",
            "authentication_unavailable",
            "not_configured",
            "identity_conflict",
            "ambiguous_response_limit",
            "invalid_response",
            "network_unavailable",
            "timeout",
            "invalid_json",
            "response_too_large",
        }
        code = code if code in allowed else "provider_unavailable"
        with self.database.sessions() as session, session.begin():
            state = lock_state(session)
            now = database_now(session)
            self._owned(state, claim, now)
            delay = 3600 if code.startswith("authentication") or code == "rate_limited" else 30
            for appid, _ in claim.identities:
                job = session.get(EnrichmentJob, appid)
                if job.lease_token != claim.token:
                    raise EnrichmentLeaseLost("Metadata job superseded")
                job.status = "backoff"
                job.available_at = now + (
                    RETRY_SECONDS if job.attempts >= 5 else max(delay, 30 * 2 ** (job.attempts - 1))
                )
                job.last_error, job.lease_token = code, None
            state.next_request_at = now + delay
            state.lease_token, state.lease_until, state.last_error = None, 0, code

    async def tick(self) -> bool:
        claim = self.claim()
        if claim is None:
            return False
        try:
            results = await lookup_batch(self.client, [i for i, _ in claim.identities])
            self.apply(claim, results)
        except EnrichmentLeaseLost:
            return True
        except TimeoutError:
            self.fail(claim, "timeout")
        except ProviderRequestError as error:
            self.fail(claim, str(error))
        except (ValueError, TypeError, KeyError):
            self.fail(claim, "invalid_response")
        # SQL failures leave the lease/cursor intact for a different attempt.
        return True


def enrichment_status(session, settings) -> dict:
    state = session.get(EnrichmentState, 1)
    now = database_now(session)
    counts = dict(
        session.execute(
            select(EnrichmentJob.status, func.count()).group_by(EnrichmentJob.status)
        ).all()
    )
    fresh = (
        GameRecord.recommendation_blocked.is_(False),
        (GameRecord.metadata_valid_until.is_(None) | (GameRecord.metadata_valid_until > now)),
    )
    eligible = session.scalar(select(func.count()).select_from(GameRecord).where(*fresh))
    auto = session.scalar(
        select(func.count())
        .select_from(GameRecord)
        .where(GameRecord.metadata_origin == "igdb_auto", *fresh)
    )
    recent = state.heartbeat_at is not None and now - state.heartbeat_at < LEASE_SECONDS * 2
    status = (
        "disabled"
        if not settings.metadata_enrichment_enabled
        else "not_configured"
        if not settings.metadata_configured
        else "worker_not_running"
        if not recent
        else "backoff"
        if state.last_error
        else "processing"
        if state.lease_token and state.lease_until > now
        else "ready"
        if state.last_success
        else "never_enriched"
    )
    return {
        "status": status,
        "enabled": settings.metadata_enrichment_enabled,
        "configured": settings.metadata_configured,
        "worker_recent": recent,
        "jobs_by_state": counts,
        "eligible_metadata": eligible,
        "automatic_metadata": auto,
        "last_success_at": state.last_success,
        "last_error": state.last_error,
        "next_request_at": state.next_request_at,
        "reserved_requests_today": state.reserved_requests
        if state.request_day == int(now // 86400)
        else 0,
        "request_budget": settings.metadata_daily_requests,
        "refresh_seconds": settings.metadata_refresh_seconds,
        "retrieval_candidate_limit": settings.retrieval_candidate_limit,
        "notes": [
            "启用不等于已取得真实上游资料；未匹配或有歧义的 AppID 不自动生成简介。",
            "保留 IGDB 原文；中文主题为固定词表映射，不冒充完整中文翻译。",
            "跨平台语言记录保持未知；不从缺失字段推断不支持或无恐怖。",
            "大目录先以 SQL 词项索引召回有限候选，再执行 BM25/已有硬约束，不是全库向量召回。",
        ],
    }
