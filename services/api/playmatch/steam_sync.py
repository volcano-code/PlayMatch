"""Durable page-level full/delta Steam discovery with conservative reconciliation.

Network runs outside the transaction. A page and its cursor commit together.
An expired owner cannot commit. Watermark advances only at confirmed cycle EOF.
Discovery records never enter the recommendation corpus without metadata.
"""

import unicodedata
from dataclasses import dataclass
from secrets import token_hex
from uuid import uuid4

from sqlalchemy import String, case, cast, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from playmatch.models import GameRecord, SteamDirectoryApp, SteamSyncReceipt, SteamSyncState
from playmatch.providers.steam_catalog import SteamCatalogClient, SteamCatalogError, SteamPage
from playmatch.run_queue import database_now

LEASE_SECONDS = 120
OVERLAP_SECONDS = 300


@dataclass(frozen=True)
class SyncClaim:
    token: str
    cycle_id: str
    cursor: int
    since: int
    limit: int


class SyncLeaseLost(RuntimeError):
    pass


def normalize_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def lock_state(session):
    if (
        session.execute(
            update(SteamSyncState)
            .where(SteamSyncState.id == 1)
            .values(requests=SteamSyncState.requests)
        ).rowcount
        != 1
    ):
        raise RuntimeError("Steam directory migration is required")
    return session.get(SteamSyncState, 1)


class SteamSynchronizer:
    def __init__(self, database, settings, client=None):
        self.database, self.settings = database, settings
        self.client = client or SteamCatalogClient(settings.steam_web_api_key.get_secret_value())

    def claim(self) -> SyncClaim | None:
        if (
            not self.settings.steam_sync_enabled
            or not self.settings.steam_web_api_key.get_secret_value().strip()
        ):
            return None
        with self.database.sessions() as session, session.begin():
            state = lock_state(session)
            now = database_now(session)
            state.heartbeat_at = now
            if state.lease_until > now or state.next_due > now:
                return None
            day = int(now // 86400)
            if state.request_day != day:
                state.request_day, state.daily_requests = day, 0
            if state.daily_requests >= self.settings.steam_sync_daily_requests:
                state.next_due = (day + 1) * 86400
                state.last_error = "daily_budget_exhausted"
                return None
            if not state.cycle_id:
                full = (
                    state.last_full_success is None
                    or now - state.last_full_success >= self.settings.steam_full_sync_seconds
                )
                state.mode = "full" if full else "incremental"
                if full:
                    state.generation += 1
                state.cycle_id, state.cursor = str(uuid4()), 0
                state.started_at = now
                state.since = 0 if full else max(0, state.watermark - OVERLAP_SECONDS)
                state.pages, state.rows_seen = 0, 0
            state.lease_token, state.lease_until = token_hex(24), now + LEASE_SECONDS
            state.requests += 1
            state.daily_requests += 1
            return SyncClaim(
                state.lease_token,
                state.cycle_id,
                state.cursor,
                state.since,
                self.settings.steam_sync_page_size,
            )

    @staticmethod
    def _owned(state, claim, now):
        if (
            state.lease_token != claim.token
            or state.lease_until <= now
            or state.cycle_id != claim.cycle_id
            or state.cursor != claim.cursor
        ):
            raise SyncLeaseLost("Steam sync lease expired or superseded")

    def apply(self, claim: SyncClaim, page: SteamPage) -> None:
        with self.database.sessions() as session, session.begin():
            state = lock_state(session)
            now = database_now(session)
            self._owned(state, claim, now)
            if state.mode == "full" and state.rows_seen == 0 and not page.apps:
                raise SteamCatalogError("empty_full_snapshot", 3600)
            # Bind payload/cursor/EOF again to prevent a custom client bypassing validation.
            from playmatch.providers.steam_catalog import parse_page

            checked = parse_page(
                {
                    "response": {
                        "apps": page.apps,
                        "last_appid": page.cursor,
                        "have_more_results": page.more,
                    }
                },
                claim.cursor,
                claim.limit,
            )
            if checked.digest != page.digest:
                raise SteamCatalogError("page_integrity_failed")
            insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
            # Bounded multi-row UPSERT (below SQLite bind limit) independent of catalog size.
            for offset in range(0, len(page.apps), 80):
                batch = [
                    {
                        "appid": app["appid"],
                        "name": app["name"],
                        "name_normalized": normalize_name(app["name"])[:512],
                        "source_modified": app["last_modified"],
                        "price_change_number": app["price_change_number"],
                        "first_seen": now,
                        "last_seen": now,
                        "full_generation": state.generation if state.mode == "full" else 0,
                    }
                    for app in page.apps[offset : offset + 80]
                ]
                statement = insert(SteamDirectoryApp).values(batch)
                changes = {
                    key: getattr(statement.excluded, key)
                    for key in (
                        "name",
                        "name_normalized",
                        "source_modified",
                        "price_change_number",
                        "last_seen",
                    )
                }
                fresh = or_(
                    SteamDirectoryApp.source_modified.is_(None),
                    statement.excluded.source_modified >= SteamDirectoryApp.source_modified,
                )
                for field in ("name", "name_normalized", "source_modified", "price_change_number"):
                    changes[field] = case(
                        (
                            fresh,
                            func.coalesce(
                                getattr(statement.excluded, field),
                                getattr(SteamDirectoryApp, field),
                            ),
                        ),
                        else_=getattr(SteamDirectoryApp, field),
                    )
                if state.mode == "full":
                    changes["full_generation"] = state.generation
                session.execute(
                    statement.on_conflict_do_update(index_elements=["appid"], set_=changes)
                )
            state.cursor = page.cursor
            state.pages += 1
            state.rows_seen += len(page.apps)
            state.last_page_hash = page.digest
            state.failures, state.last_error = 0, None
            state.lease_token, state.lease_until = None, 0
            state.next_due = now + 1  # Shared rate gate, including resumed/multi-process calls.
            if not page.more:
                state.watermark = max(state.watermark, int(state.started_at))
                state.last_success = now
                if state.mode == "full":
                    state.last_full_success = now
                    state.completed_generation = state.generation
                session.add(
                    SteamSyncReceipt(
                        id=state.cycle_id,
                        mode=state.mode,
                        started_at=state.started_at,
                        completed_at=now,
                        since=state.since,
                        watermark=state.watermark,
                        pages=state.pages,
                        rows_seen=state.rows_seen,
                        final_cursor=state.cursor,
                        final_page_hash=page.digest,
                    )
                )
                # Keep bounded operational receipts; app identities and favorites are not deleted.
                old = (
                    select(SteamSyncReceipt.id)
                    .order_by(SteamSyncReceipt.completed_at.desc(), SteamSyncReceipt.id)
                    .offset(199)
                )
                session.execute(delete(SteamSyncReceipt).where(SteamSyncReceipt.id.in_(old)))
                state.cycle_id = None
                state.next_due = now + self.settings.steam_sync_interval_seconds

    def fail(self, claim: SyncClaim, error: SteamCatalogError) -> None:
        with self.database.sessions() as session, session.begin():
            state = lock_state(session)
            now = database_now(session)
            self._owned(state, claim, now)
            state.failures += 1
            state.last_error = error.code
            delay = max(error.retry_after, min(3600, 30 * 2 ** min(state.failures - 1, 7)))
            state.next_due = now + delay
            state.lease_token, state.lease_until = None, 0
            # Crucially: retain cursor, since, cycle start and prior successful watermark.

    def tick(self) -> str:
        claim = self.claim()
        if claim is None:
            return "idle"
        try:
            page = self.client.page(claim.cursor, claim.since, claim.limit)
            self.apply(claim, page)
            return "page_committed" if page.more else "cycle_completed"
        except SyncLeaseLost:
            return "lease_lost"
        except SteamCatalogError as error:
            try:
                self.fail(claim, error)
            except SyncLeaseLost:
                return "lease_lost"
            return error.code


def sync_status(session, settings) -> dict:
    state = session.get(SteamSyncState, 1)
    now = database_now(session)
    configured = bool(settings.steam_web_api_key.get_secret_value().strip())
    recent = state.heartbeat_at is not None and now - state.heartbeat_at < 60
    join = GameRecord.id == literal("steam-") + cast(SteamDirectoryApp.appid, String)
    total = session.scalar(select(func.count()).select_from(SteamDirectoryApp))
    linked = session.scalar(
        select(func.count()).select_from(SteamDirectoryApp).join(GameRecord, join)
    )
    missing = 0
    if state.completed_generation:
        missing = session.scalar(
            select(func.count())
            .select_from(SteamDirectoryApp)
            .where(
                SteamDirectoryApp.full_generation < state.completed_generation,
                SteamDirectoryApp.last_seen < state.last_full_success,
            )
        )
    if not settings.steam_sync_enabled:
        status = "disabled"
    elif not configured:
        status = "not_configured"
    elif not recent:
        status = "worker_not_running"
    elif state.last_error:
        status = "backoff"
    elif state.cycle_id:
        status = "syncing"
    elif state.last_success is None:
        status = "never_synced"
    elif now - state.last_success > settings.steam_sync_interval_seconds * 2:
        status = "stale"
    else:
        status = "up_to_date"
    return {
        "status": status,
        "enabled": settings.steam_sync_enabled,
        "configured": configured,
        "worker_recent": recent,
        "source": "steam_istoreservice_games",
        "discovered_games": total,
        "with_recommendation_metadata": linked,
        "metadata_pending": total - linked,
        "not_seen_in_last_full": missing,
        "last_success_at": state.last_success,
        "last_full_success_at": state.last_full_success,
        "cycle_mode": state.mode,
        "cycle_in_progress": state.cycle_id is not None,
        "pages_committed": state.pages,
        "rows_seen_in_cycle": state.rows_seen,
        "cursor": state.cursor,
        "watermark": state.watermark,
        "next_due_at": state.next_due,
        "last_error": state.last_error,
        "requests_today": state.daily_requests,
        "request_budget": settings.steam_sync_daily_requests,
        "poll_interval_seconds": settings.steam_sync_interval_seconds,
        "automatic_metadata_enrichment": False,
        "automatic_price_polling": False,
        "notes": [
            "已发现名称不等于具备主题推荐资料；不编造简介、语言或实时价格。",
            "up_to_date 仅表示近期完成接口遍历，不保证上游无延迟或全库绝无遗漏。",
            "未在最近全量遍历出现的记录保留，不自动认定下架或删除收藏。",
            "当前按需推荐仍使用已有资料集；自动资料补全和大规模向量检索尚未交付。",
        ],
    }


def directory_page(session, *, q="", after_appid=0, limit=24) -> dict:
    query = normalize_name(q)
    if len(query) > 512:
        return {"items": [], "next_appid": None, "search_mode": "name_prefix_or_exact_appid"}
    clause = SteamDirectoryApp.appid > after_appid
    statement = (
        select(SteamDirectoryApp, GameRecord.id)
        .outerjoin(
            GameRecord, GameRecord.id == literal("steam-") + cast(SteamDirectoryApp.appid, String)
        )
        .where(clause)
    )
    if query:
        if query.isascii() and query.isdecimal():
            appid = int(query)
            if not 1 <= appid <= 4294967295:
                return {
                    "items": [],
                    "next_appid": None,
                    "search_mode": "name_prefix_or_exact_appid",
                }
            statement = statement.where(SteamDirectoryApp.appid == appid)
        else:
            statement = statement.where(
                SteamDirectoryApp.name_normalized.startswith(query, autoescape=True)
            )
            if session.bind.dialect.name == "sqlite":
                statement = statement.where(
                    SteamDirectoryApp.name_normalized >= query,
                    SteamDirectoryApp.name_normalized < query + "\U0010ffff",
                )
    records = list(session.execute(statement.order_by(SteamDirectoryApp.appid).limit(limit + 1)))
    more = len(records) > limit
    records = records[:limit]
    return {
        "items": [
            {
                "appid": app.appid,
                "name": app.name,
                "store_url": f"https://store.steampowered.com/app/{app.appid}/",
                "source_modified": app.source_modified,
                "observed_at": app.last_seen,
                "metadata_status": "available" if game_id else "pending",
                "game_id": game_id,
            }
            for app, game_id in records
        ],
        "next_appid": records[-1][0].appid if more else None,
        "search_mode": "name_prefix_or_exact_appid",
    }
