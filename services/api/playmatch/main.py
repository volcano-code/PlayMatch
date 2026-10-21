import asyncio
import hmac
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from playmatch.catalog_seed import catalog_coverage, load_catalog
from playmatch.config import Settings, get_settings
from playmatch.database import Database
from playmatch.models import (
    CatalogLock,
    FeedbackRecord,
    GameRecord,
    NotificationRecord,
    RunEventRecord,
    RunRecord,
    SessionRecord,
    UserRecord,
    WatchRecord,
)
from playmatch.notifications import (
    CONSENT_VERSION,
    NotificationConsumer,
    alert_payload,
    configure_alert,
    invalidate_alert,
    lock_alerts,
    record_observation,
)
from playmatch.providers.cheapshark import CheapSharkClient
from playmatch.run_queue import RunQueue, database_now, lock_queue
from playmatch.runs import (
    TERMINAL,
    RunExecutor,
    append_event,
    now_iso,
    run_payload,
)
from playmatch.schemas import (
    AlertInput,
    AlertState,
    CatalogCoverage,
    Credentials,
    FeedbackInput,
    Game,
    GameList,
    IndexStatus,
    NotificationItem,
    NotificationList,
    OfferResponse,
    Run,
    RunCreated,
    RunInput,
    User,
    WatchInput,
    WatchItem,
)
from playmatch.security import (
    DUMMY_HASH,
    SESSION_COOKIE,
    VISITOR_COOKIE,
    RateLimiter,
    capability,
    digest,
    password_hasher,
    validate_origin,
    verify_password,
)
from playmatch.semantic_index import index_status


def get_db(request: Request):
    with request.app.state.database.sessions() as session:
        yield session


def optional_user(request: Request, session: Session) -> UserRecord | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    stored = session.get(SessionRecord, digest(token))
    if not stored or stored.expires_at < time.time():
        return None
    return session.get(UserRecord, stored.user_id)


def require_user(request: Request, session: Session) -> UserRecord:
    user = optional_user(request, session)
    if not user:
        raise HTTPException(401, "请先登录")
    return user


def authorize_run(run_id: str, request: Request, session: Session) -> RunRecord:
    record = session.get(RunRecord, run_id)
    if not record:
        raise HTTPException(404, "任务不存在")
    user = optional_user(request, session)
    if record.user_id:
        if not user or user.id != record.user_id:
            raise HTTPException(404, "任务不存在")
    else:
        supplied = request.headers.get("X-Run-Token", "")
        if (
            not supplied
            or not record.capability_hash
            or not hmac.compare_digest(digest(supplied), record.capability_hash)
        ):
            raise HTTPException(404, "任务不存在")
    return record


def game_or_404(session: Session, game_id: str) -> GameRecord:
    game = session.get(GameRecord, game_id)
    if not game:
        raise HTTPException(404, "游戏不存在")
    return game


def set_session(response: Response, session: Session, user_id: str, settings: Settings) -> None:
    token = secrets.token_urlsafe(32)
    session.add(
        SessionRecord(
            token_hash=digest(token),
            user_id=user_id,
            expires_at=time.time() + settings.session_days * 86400,
        )
    )
    session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_days * 86400,
        path="/",
    )


def watch_payload(row: WatchRecord, session: Session) -> dict:
    return {
        "game": game_or_404(session, row.game_id).payload,
        "target_minor": row.target_minor,
        "currency": row.currency,
        "country": row.country,
        "monitoring_status": "not_enabled",
        "alert": alert_payload(row),
        "created_at": row.created_at,
    }


def create_app(settings: Settings | None = None, *, catalog: list[dict] | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings.database_url)
        database.migrate()
        with database.sessions() as session:
            session.execute(
                update(CatalogLock).where(CatalogLock.id == 1).values(revision=CatalogLock.revision)
            )
            for raw in catalog if catalog is not None else load_catalog():
                game = Game.model_validate(raw)
                existing = session.get(GameRecord, game.id)
                if not existing:
                    session.add(GameRecord(id=game.id, title=game.title, payload=game.model_dump()))
                else:
                    existing.title = game.title
                    existing.payload = game.model_dump()
            session.commit()
        app.state.database = database
        app.state.executor = (
            RunExecutor(database, settings) if settings.run_execution_mode == "embedded" else None
        )
        app.state.notification_consumer = (
            NotificationConsumer(database) if settings.run_execution_mode == "embedded" else None
        )
        try:
            yield
        finally:
            if app.state.notification_consumer is not None:
                app.state.notification_consumer.close()
            if app.state.executor is not None:
                app.state.executor.close()
            database.close()

    app = FastAPI(title="PlayMatch API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter()

    @app.middleware("http")
    async def guard_requests(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                validate_origin(request)
            except HTTPException as error:
                return JSONResponse({"detail": error.detail}, status_code=error.status_code)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith(
            (
                "/api/v1/auth",
                "/api/v1/runs",
                "/api/v1/watchlist",
                "/api/v1/feedback",
                "/api/v1/notifications",
            )
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health(session: Session = Depends(get_db)):
        return {
            "status": "ok",
            "version": "0.1.0",
            "catalog_count": session.scalar(select(func.count()).select_from(GameRecord)),
        }

    @app.get("/api/v1/capabilities")
    def capabilities(session: Session = Depends(get_db)):
        return {
            "catalog_mode": "mixed"
            if any(
                row.payload.get("metadata_status") == "provider"
                for row in session.scalars(select(GameRecord))
            )
            else "curated_starter",
            "catalog_count": session.scalar(select(func.count()).select_from(GameRecord)),
            "agent_mode": "llm" if settings.llm_enabled else "rules",
            "retrieval_mode": settings.retrieval_mode,
            "embedding_configured": settings.embedding_enabled,
            "catalog_import": "operator_cli",
            "semantic_catalog_limit": 5000 if settings.retrieval_mode == "indexed_hybrid" else 128,
            "catalog_collection": "review_bundle_cli",
            "pricing_provider": "cheapshark",
            "pricing_currency": "USD",
            "supported_price_countries": ["US"],
            "notification_monitoring": "not_enabled",
            "price_alerts": {
                "mode": "own_query_observation",
                "channel": "in_app",
                "consent_version": CONSENT_VERSION,
                "automatic_price_polling": False,
                "external_email": False,
                "supported_price_countries": ["US"],
            },
            "execution_mode": "durable_sql_" + settings.run_execution_mode,
            "run_max_attempts": settings.run_max_attempts,
            "limitations": [
                "目录包含来源整理记录及可选的审核导入；尚未启用全量 IGDB 同步。",
                "默认 BM25；向量融合需显式配置，实际模式与降级原因见每次任务。",
                "仅查询 CheapShark US/USD 观测报价，不提供中国区人民币价格。",
                "版本与地区激活资格未核验的报价不会标记为保证可购买的最低价。",
                "收藏不自动开启提醒；可明确开启主动查价站内提醒，无后台抓价和邮件发送。",
                "推荐任务持久入队；工作进程中断后在租约到期时接续安全阶段，外部只读调用可能重试。",
            ],
        }

    @app.get("/api/v1/retrieval/index", response_model=IndexStatus)
    def retrieval_index(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return index_status(request.app.state.database, settings)

    @app.get("/api/v1/catalog/coverage", response_model=CatalogCoverage)
    def coverage(response: Response, session: Session = Depends(get_db)):
        response.headers["Cache-Control"] = "no-store"
        return catalog_coverage([row.payload for row in session.scalars(select(GameRecord))])

    @app.get("/api/v1/games", response_model=GameList)
    def games(
        q: str = Query(default="", max_length=200),
        theme: str = Query(default="", max_length=50),
        limit: int = Query(default=24, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
        session: Session = Depends(get_db),
    ):
        records = [
            row.payload for row in session.scalars(select(GameRecord).order_by(GameRecord.title))
        ]
        if q.strip():
            needle = q.strip().casefold()
            records = [
                game
                for game in records
                if needle
                in " ".join(
                    [
                        game["title"],
                        game["title_zh"],
                        *game.get("aliases", []),
                        game["description"],
                        *game["themes"],
                        *game["genres"],
                    ]
                ).casefold()
            ]
        if theme.strip():
            records = [
                game
                for game in records
                if theme.strip().casefold() in {item.casefold() for item in game["themes"]}
            ]
        return {"items": records[offset : offset + limit], "total": len(records)}

    @app.get("/api/v1/games/{game_id}", response_model=Game)
    def game(game_id: str, session: Session = Depends(get_db)):
        return game_or_404(session, game_id).payload

    @app.get("/api/v1/games/{game_id}/offers", response_model=OfferResponse)
    async def offers(
        game_id: str,
        request: Request,
        country: str = Query(default="US", pattern="^(US|CN)$"),
        session: Session = Depends(get_db),
    ):
        game = game_or_404(session, game_id).payload
        app.state.rate_limiter.check(
            "offers:" + (request.client.host if request.client else "unknown"), 30
        )
        async with CheapSharkClient() as provider:
            result = await provider.get_offers(game["steam_app_id"], country)
        return OfferResponse.model_validate({"game_id": game_id, **result})

    @app.post("/api/v1/auth/register", response_model=User, status_code=201)
    def register(
        body: Credentials, request: Request, response: Response, session: Session = Depends(get_db)
    ):
        app.state.rate_limiter.check(
            "register:" + (request.client.host if request.client else "unknown"), 8
        )
        user = UserRecord(
            id=str(uuid4()),
            email=body.email,
            password_hash=password_hasher.hash(body.password),
            created_at=now_iso(),
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "该邮箱已注册") from error
        set_session(response, session, user.id, settings)
        return {"id": user.id, "email": user.email}

    @app.post("/api/v1/auth/login", response_model=User)
    def login(
        body: Credentials, request: Request, response: Response, session: Session = Depends(get_db)
    ):
        app.state.rate_limiter.check(
            "login:" + (request.client.host if request.client else "unknown"), 15
        )
        user = session.scalar(select(UserRecord).where(UserRecord.email == body.email))
        matched = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
        if not user or not matched:
            raise HTTPException(401, "邮箱或密码错误")
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            session.execute(
                delete(SessionRecord).where(SessionRecord.token_hash == digest(previous))
            )
        set_session(response, session, user.id, settings)
        return {"id": user.id, "email": user.email}

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, session: Session = Depends(get_db)):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            session.execute(delete(SessionRecord).where(SessionRecord.token_hash == digest(token)))
            session.commit()
        response.delete_cookie(
            SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
        )

    @app.get("/api/v1/auth/me", response_model=User)
    def me(request: Request, session: Session = Depends(get_db)):
        user = require_user(request, session)
        return {"id": user.id, "email": user.email}

    @app.post("/api/v1/runs", response_model=RunCreated, status_code=202)
    def create_run(
        body: RunInput,
        request: Request,
        response: Response,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
        session: Session = Depends(get_db),
    ):
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", idempotency_key):
            raise HTTPException(422, "Idempotency-Key 必须为16至128位安全随机标识")
        user = optional_user(request, session)
        visitor = request.cookies.get(VISITOR_COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", visitor):
            visitor = secrets.token_urlsafe(32)
        scope = "user:" + user.id if user else "anon:" + digest(visitor)
        payload_hash = digest(json.dumps(body.model_dump(), sort_keys=True, ensure_ascii=False))
        key_hash = digest(idempotency_key)
        # Serialize idempotency and capacity admission in the same transaction.
        lock_queue(session)
        existing = session.scalar(
            select(RunRecord).where(
                RunRecord.idempotency_scope == scope, RunRecord.idempotency_key_hash == key_hash
            )
        )

        def created(record):
            if not user:
                response.set_cookie(
                    VISITOR_COOKIE,
                    visitor,
                    httponly=True,
                    secure=settings.cookie_secure,
                    samesite="lax",
                    max_age=30 * 86400,
                    path="/",
                )
            return {
                "id": record.id,
                "status": record.status,
                "access_token": None if user else capability(visitor, record.id),
            }

        if existing:
            if existing.payload_hash != payload_hash:
                raise HTTPException(409, "相同幂等键已用于不同请求")
            return created(existing)
        app.state.rate_limiter.check("run:" + scope, 12)
        app.state.rate_limiter.check(
            "run-ip:" + (request.client.host if request.client else "unknown"), 40
        )
        if (
            session.scalar(
                select(func.count())
                .select_from(RunRecord)
                .where(RunRecord.status.in_(["queued", "running"]))
            )
            >= settings.max_pending_runs
        ):
            raise HTTPException(503, "推荐队列已满，请稍后重试", headers={"Retry-After": "10"})
        run_id = str(uuid4())
        record = RunRecord(
            id=run_id,
            user_id=user.id if user else None,
            capability_hash=None if user else digest(capability(visitor, run_id)),
            idempotency_scope=scope,
            idempotency_key_hash=key_hash,
            payload_hash=payload_hash,
            query=body.query,
            request_payload=body.model_dump(),
            status="queued",
            mode="llm" if settings.llm_enabled else "rules",
            intent={},
            results=[],
            warnings=[],
            agent_state={},
            created_at=now_iso(),
            completed_at=None,
            deadline_at=database_now(session) + settings.run_timeout_seconds,
        )
        try:
            session.add(record)
            session.flush()
            append_event(
                session, run_id, "progress", {"stage": "queued", "message": "推荐任务已进入队列"}
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(RunRecord).where(
                    RunRecord.idempotency_scope == scope, RunRecord.idempotency_key_hash == key_hash
                )
            )
            if not existing or existing.payload_hash != payload_hash:
                raise HTTPException(409, "幂等请求冲突") from None
            return created(existing)
        return created(record)

    @app.get("/api/v1/runs/{run_id}", response_model=Run)
    def get_run(run_id: str, request: Request, session: Session = Depends(get_db)):
        return run_payload(authorize_run(run_id, request, session))

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=Run)
    def cancel_run(run_id: str, request: Request, session: Session = Depends(get_db)):
        authorize_run(run_id, request, session)
        record = RunQueue(app.state.database, settings).cancel(session, run_id)
        session.commit()
        return run_payload(record)

    @app.get("/api/v1/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        session: Session = Depends(get_db),
    ):
        authorize_run(run_id, request, session)
        # Yield dependencies can outlive the entire streaming response. Release
        # this authentication transaction before streaming; each polling read
        # below opens and closes its own short-lived session.
        session.close()
        if last_event_id is not None and (not last_event_id.isdigit() or len(last_event_id) > 12):
            raise HTTPException(422, "无效的 Last-Event-ID")
        after = int(last_event_id or 0)

        async def stream():
            nonlocal after
            idle = 0
            while not await request.is_disconnected():
                with app.state.database.sessions() as fresh:
                    rows = list(
                        fresh.scalars(
                            select(RunEventRecord)
                            .where(RunEventRecord.run_id == run_id, RunEventRecord.sequence > after)
                            .order_by(RunEventRecord.sequence)
                        )
                    )
                    status = fresh.get(RunRecord, run_id).status
                    events = [(row.sequence, row.event, row.payload) for row in rows]
                for sequence, name, payload in events:
                    yield f"id: {sequence}\nevent: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    after = sequence
                if status in TERMINAL:
                    break
                idle += 1
                if idle % 30 == 0:
                    yield ": ping\n\n"
                await asyncio.sleep(0.15)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/watchlist")
    def watchlist(request: Request, session: Session = Depends(get_db)):
        user = require_user(request, session)
        rows = session.scalars(
            select(WatchRecord)
            .where(WatchRecord.user_id == user.id)
            .order_by(WatchRecord.created_at.desc())
        )
        return {"items": [watch_payload(row, session) for row in rows]}

    @app.post("/api/v1/watchlist", response_model=WatchItem)
    def save_watchlist(body: WatchInput, request: Request, session: Session = Depends(get_db)):
        user = require_user(request, session)
        game_or_404(session, body.game_id)
        lock_alerts(session)
        row = session.scalar(
            select(WatchRecord).where(
                WatchRecord.user_id == user.id, WatchRecord.game_id == body.game_id
            )
        )
        if row:
            if (row.target_minor, row.currency, row.country) != (
                body.target_minor,
                body.currency,
                body.country,
            ):
                invalidate_alert(session, row)
            row.target_minor, row.currency, row.country = (
                body.target_minor,
                body.currency,
                body.country,
            )
        else:
            row = WatchRecord(
                id=str(uuid4()),
                user_id=user.id,
                game_id=body.game_id,
                target_minor=body.target_minor,
                currency=body.currency,
                country=body.country,
                monitoring_enabled=False,
                created_at=now_iso(),
            )
            session.add(row)
        session.commit()
        return watch_payload(row, session)

    @app.delete("/api/v1/watchlist/{game_id}", status_code=204)
    def remove_watchlist(game_id: str, request: Request, session: Session = Depends(get_db)):
        user = require_user(request, session)
        lock_alerts(session)
        row = session.scalar(
            select(WatchRecord).where(
                WatchRecord.user_id == user.id,
                WatchRecord.game_id == game_id,
            )
        )
        if row is not None:
            invalidate_alert(session, row)
            session.delete(row)
        session.commit()

    @app.put("/api/v1/watchlist/{game_id}/alert", response_model=AlertState)
    def set_price_alert(
        game_id: str, body: AlertInput, request: Request, session: Session = Depends(get_db)
    ):
        user = require_user(request, session)
        app.state.rate_limiter.check("alert-config:" + user.id, 30)
        watch = configure_alert(session, user.id, game_id, body)
        session.commit()
        return alert_payload(watch)

    @app.post("/api/v1/watchlist/{game_id}/check", response_model=OfferResponse)
    async def check_price_alert(game_id: str, request: Request, session: Session = Depends(get_db)):
        user = require_user(request, session)
        user_id = user.id
        app.state.rate_limiter.check("alert-check:" + user_id, 5)
        watch = session.scalar(
            select(WatchRecord).where(
                WatchRecord.user_id == user_id,
                WatchRecord.game_id == game_id,
            )
        )
        if watch is None:
            raise HTTPException(404, "收藏不存在")
        if watch.alert_state != "armed":
            raise HTTPException(409, "请先开启查价提醒；每次开启只生成一条通知")
        game = dict(game_or_404(session, game_id).payload)
        version = watch.alert_version
        started = database_now(session)
        session.rollback()  # Never hold a connection/transaction during provider HTTP.
        async with CheapSharkClient() as provider:
            raw = await provider.get_offers(game["steam_app_id"], "US")
        result = OfferResponse.model_validate({"game_id": game_id, **raw})
        try:
            with request.app.state.database.sessions() as fresh, fresh.begin():
                current_user = optional_user(request, fresh)
                if current_user is None or current_user.id != user_id:
                    raise HTTPException(401, "会话已失效，未写入提醒")
                lock_alerts(fresh)
                current_watch = fresh.scalar(
                    select(WatchRecord).where(
                        WatchRecord.user_id == user_id,
                        WatchRecord.game_id == game_id,
                    )
                )
                if current_watch is None or current_watch.alert_version != version:
                    result.alert_evaluation = "stale"
                else:
                    result.alert_evaluation = record_observation(
                        fresh,
                        user_id,
                        game,
                        result.model_dump(),
                        started,
                    )
        except SQLAlchemyError:
            result.alert_evaluation = "save_failed"
        return result

    @app.get("/api/v1/notifications", response_model=NotificationList)
    def notifications(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        session: Session = Depends(get_db),
    ):
        user = require_user(request, session)
        query = select(NotificationRecord).where(NotificationRecord.user_id == user.id)
        rows = session.scalars(
            query.order_by(
                NotificationRecord.created_at.desc(),
                NotificationRecord.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        def payload(row):
            return {
                "id": row.id,
                "payload": row.payload,
                "created_at": row.created_at,
                "read_at": row.read_at,
            }

        return {
            "items": [payload(row) for row in rows],
            "total": session.scalar(
                select(func.count())
                .select_from(NotificationRecord)
                .where(NotificationRecord.user_id == user.id)
            ),
            "unread": session.scalar(
                select(func.count())
                .select_from(NotificationRecord)
                .where(NotificationRecord.user_id == user.id, NotificationRecord.read_at.is_(None))
            ),
        }

    @app.post("/api/v1/notifications/{notification_id}/read", response_model=NotificationItem)
    def read_notification(
        notification_id: str, request: Request, session: Session = Depends(get_db)
    ):
        user = require_user(request, session)
        session.execute(
            update(NotificationRecord)
            .where(
                NotificationRecord.id == notification_id,
                NotificationRecord.user_id == user.id,
                NotificationRecord.read_at.is_(None),
            )
            .values(read_at=now_iso())
        )
        row = session.scalar(
            select(NotificationRecord).where(
                NotificationRecord.id == notification_id,
                NotificationRecord.user_id == user.id,
            )
        )
        if row is None:
            raise HTTPException(404, "通知不存在")
        session.commit()
        return {
            "id": row.id,
            "payload": row.payload,
            "created_at": row.created_at,
            "read_at": row.read_at,
        }

    @app.delete("/api/v1/notifications/{notification_id}", status_code=204)
    def delete_notification(
        notification_id: str, request: Request, session: Session = Depends(get_db)
    ):
        user = require_user(request, session)
        session.execute(
            delete(NotificationRecord).where(
                NotificationRecord.id == notification_id,
                NotificationRecord.user_id == user.id,
            )
        )
        session.commit()  # Deleting inbox history does not rearm its subscription.

    @app.get("/api/v1/feedback")
    def get_feedback(request: Request, session: Session = Depends(get_db)):
        user = require_user(request, session)
        return {
            "items": [
                {"game_id": row.game_id, "kind": row.kind, "created_at": row.created_at}
                for row in session.scalars(
                    select(FeedbackRecord).where(FeedbackRecord.user_id == user.id)
                )
            ]
        }

    @app.post("/api/v1/feedback", status_code=201)
    def feedback(body: FeedbackInput, request: Request, session: Session = Depends(get_db)):
        user = require_user(request, session)
        game_or_404(session, body.game_id)
        if body.kind in {"like", "dislike"}:
            opposite = "dislike" if body.kind == "like" else "like"
            session.execute(
                delete(FeedbackRecord).where(
                    FeedbackRecord.user_id == user.id,
                    FeedbackRecord.game_id == body.game_id,
                    FeedbackRecord.kind == opposite,
                )
            )
        row = session.scalar(
            select(FeedbackRecord).where(
                FeedbackRecord.user_id == user.id,
                FeedbackRecord.game_id == body.game_id,
                FeedbackRecord.kind == body.kind,
            )
        )
        if not row:
            row = FeedbackRecord(
                id=str(uuid4()),
                user_id=user.id,
                game_id=body.game_id,
                kind=body.kind,
                created_at=now_iso(),
            )
            session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        return {"game_id": body.game_id, "kind": body.kind, "saved": True}

    @app.delete("/api/v1/feedback/{game_id}/{kind}", status_code=204)
    def delete_feedback(
        game_id: str, kind: str, request: Request, session: Session = Depends(get_db)
    ):
        user = require_user(request, session)
        session.execute(
            delete(FeedbackRecord).where(
                FeedbackRecord.user_id == user.id,
                FeedbackRecord.game_id == game_id,
                FeedbackRecord.kind == kind,
            )
        )
        session.commit()

    @app.get("/api/v1/providers/health")
    def providers_health():
        return {
            "cheapshark": {
                "status": "configured_unprobed",
                "currency": "USD",
                "countries": ["US"],
                "live_probe_performed": False,
            },
            "igdb": {
                "status": "configured_unprobed"
                if settings.igdb_client_id and settings.igdb_client_secret
                else "not_configured",
                "catalog_sync_enabled": False,
            },
            "notifications": {
                "status": "in_app_outbox",
                "trigger": "explicit_own_query",
                "external_email": False,
                "automatic_price_polling": False,
            },
            "limitations": [
                "健康页不进行外部调用；未把未检查的来源标为健康。",
                "仅用户触发的报价请求，没有后台价格轮询。",
            ],
        }

    return app


app = create_app()
