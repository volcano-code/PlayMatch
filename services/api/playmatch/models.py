from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
