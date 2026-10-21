from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))


class SessionRecord(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class GameRecord(Base):
    __tablename__ = "games"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    search_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    search_themes: Mapped[str] = mapped_column(Text, default="", server_default="")


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("idempotency_scope", "idempotency_key_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    capability_hash: Mapped[str | None] = mapped_column(String(64))
    idempotency_scope: Mapped[str] = mapped_column(String(80))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    request_payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), index=True)
    mode: Mapped[str] = mapped_column(String(12))
    intent: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    agent_state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40))
    completed_at: Mapped[str | None] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[float | None] = mapped_column(Float)
    deadline_at: Mapped[float | None] = mapped_column(Float)
    available_at: Mapped[float] = mapped_column(Float, default=0, server_default="0")


class RunQueueLock(Base):
    __tablename__ = "run_queue_lock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)


class WatchRecord(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("user_id", "game_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    target_minor: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    country: Mapped[str] = mapped_column(String(2))
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    alert_state: Mapped[str] = mapped_column(String(16), default="off", server_default="off")
    alert_armed_at: Mapped[float | None] = mapped_column(Float)
    alert_consent: Mapped[str | None] = mapped_column(String(40))
    alert_command: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))


class FeedbackRecord(Base):
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("user_id", "game_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(40))


class CatalogLock(Base):
    __tablename__ = "catalog_lock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class CatalogSource(Base):
    __tablename__ = "catalog_sources"
    provider_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), unique=True)
    observed_at: Mapped[str] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64))


class CatalogImport(Base):
    __tablename__ = "catalog_imports"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))
    report: Mapped[dict] = mapped_column(JSON)


class EmbeddingIndex(Base):
    __tablename__ = "embedding_indexes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity: Mapped[str] = mapped_column(String(64))
    catalog_hash: Mapped[str] = mapped_column(String(64))
    text_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16))
    document_count: Mapped[int] = mapped_column(Integer)
    dimensions: Mapped[int | None] = mapped_column(Integer)
    reused_count: Mapped[int] = mapped_column(Integer, default=0)
    encoded_count: Mapped[int] = mapped_column(Integer, default=0)
    started_generation: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    completed_at: Mapped[str | None] = mapped_column(String(40))
    failure_code: Mapped[str | None] = mapped_column(String(50))
    owner: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[float] = mapped_column(Float, default=0)


class EmbeddingVector(Base):
    __tablename__ = "embedding_vectors"
    index_id: Mapped[str] = mapped_column(
        ForeignKey("embedding_indexes.id", ondelete="CASCADE"), primary_key=True
    )
    game_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list] = mapped_column(JSON)
    vector_hash: Mapped[str] = mapped_column(String(64))


class EmbeddingPointer(Base):
    __tablename__ = "embedding_pointer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index_id: Mapped[str | None] = mapped_column(ForeignKey("embedding_indexes.id"))
    generation: Mapped[int] = mapped_column(Integer, default=0)


class AlertLock(Base):
    __tablename__ = "alert_lock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class PriceAlertEvent(Base):
    __tablename__ = "price_alert_events"
    __table_args__ = (UniqueConstraint("watch_id", "alert_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    watch_id: Mapped[str] = mapped_column(String(36), index=True)
    alert_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[float] = mapped_column(Float)
    available_at: Mapped[float] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float | None] = mapped_column(Float)
    failure_code: Mapped[str | None] = mapped_column(String(40))


class NotificationRecord(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("price_alert_events.id"), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
    read_at: Mapped[str | None] = mapped_column(String(40))


class SteamDirectoryApp(Base):
    """Discovery only: never invent a recommendation Game from an app name."""

    __tablename__ = "steam_directory_apps"
    appid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(1024))
    name_normalized: Mapped[str] = mapped_column(String(512), index=True)
    source_modified: Mapped[int | None] = mapped_column(BigInteger)
    price_change_number: Mapped[int | None] = mapped_column(BigInteger)
    first_seen: Mapped[float] = mapped_column(Float)
    last_seen: Mapped[float] = mapped_column(Float)
    full_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)


class SteamSyncState(Base):
    __tablename__ = "steam_sync_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    completed_generation: Mapped[int] = mapped_column(Integer, default=0)
    cycle_id: Mapped[str | None] = mapped_column(String(36))
    mode: Mapped[str] = mapped_column(String(16), default="full")
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    since: Mapped[int] = mapped_column(BigInteger, default=0)
    watermark: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[float | None] = mapped_column(Float)
    last_success: Mapped[float | None] = mapped_column(Float)
    last_full_success: Mapped[float | None] = mapped_column(Float)
    heartbeat_at: Mapped[float | None] = mapped_column(Float)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    next_due: Mapped[float] = mapped_column(Float, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(40))
    pages: Mapped[int] = mapped_column(Integer, default=0)
    rows_seen: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    request_day: Mapped[int] = mapped_column(Integer, default=0)
    daily_requests: Mapped[int] = mapped_column(Integer, default=0)
    last_page_hash: Mapped[str | None] = mapped_column(String(64))


class SteamSyncReceipt(Base):
    __tablename__ = "steam_sync_receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[float] = mapped_column(Float)
    completed_at: Mapped[float] = mapped_column(Float)
    since: Mapped[int] = mapped_column(BigInteger)
    watermark: Mapped[int] = mapped_column(BigInteger)
    pages: Mapped[int] = mapped_column(Integer)
    rows_seen: Mapped[int] = mapped_column(Integer)
    final_cursor: Mapped[int] = mapped_column(BigInteger)
    final_page_hash: Mapped[str] = mapped_column(String(64))
