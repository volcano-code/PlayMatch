"""Transactional queue and fenced writes shared by API and workers.

No network calls run inside a queue transaction. A small singleton writer lock
serializes admission/claim on both PostgreSQL and SQLite; execution is concurrent.
All event writers obtain the run-row lock before calculating its next sequence.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_hex

from sqlalchemy import and_, func, or_, select, update

from playmatch.models import RunEventRecord, RunQueueLock, RunRecord

TERMINAL = {"completed", "partial", "failed", "cancelled"}
CHECKPOINT_VERSION = "run-stages-v04"


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    run_id: str
    token: str
    attempt: int
    remaining: float


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def database_now(session) -> float:
    # One authority for expiry even when API/worker machine clocks differ.
    if session.bind.dialect.name == "postgresql":
        return float(session.scalar(select(func.extract("epoch", func.clock_timestamp()))))
    return float(session.scalar(select((func.julianday("now") - 2440587.5) * 86400)))


def lock_queue(session) -> None:
    changed = session.execute(
        update(RunQueueLock).where(RunQueueLock.id == 1).values(revision=RunQueueLock.revision)
    )
    if changed.rowcount != 1:
        raise RuntimeError("Durable queue migration is required")


def append_event(session, run_id: str, event: str, payload: dict) -> None:
    last = (
        session.scalar(
            select(func.max(RunEventRecord.sequence)).where(RunEventRecord.run_id == run_id)
        )
        or 0
    )
    session.add(
        RunEventRecord(
            run_id=run_id,
            sequence=last + 1,
            event=event,
            payload={**payload, "sequence": last + 1},
        )
    )
    session.flush()


def run_payload(record: RunRecord) -> dict:
    result = {
        key: getattr(record, key)
        for key in (
            "id",
            "status",
            "query",
            "intent",
            "results",
            "warnings",
            "mode",
            "created_at",
            "completed_at",
            "attempt_count",
        )
    }
    result["last_checkpoint"] = (record.agent_state or {}).get("stage")
    return result


def owned(session, claim: Claim, *, enforce_time=True) -> RunRecord:
    now = database_now(session)
    conditions = [
        RunRecord.id == claim.run_id,
        RunRecord.status == "running",
        RunRecord.lease_token == claim.token,
        RunRecord.attempt_count == claim.attempt,
    ]
    if enforce_time:
        conditions.extend([RunRecord.lease_expires_at > now, RunRecord.deadline_at > now])
    count = session.execute(
        update(RunRecord)
        .where(*conditions)
        .values(
            attempt_count=RunRecord.attempt_count,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if count != 1:
        raise LeaseLost("Attempt no longer owns the run")
    # Expire any identity-map value loaded before the lock.
    record = session.get(RunRecord, claim.run_id, populate_existing=True)
    if enforce_time:
        locked_now = database_now(session)
        if record.lease_expires_at <= locked_now or record.deadline_at <= locked_now:
            raise LeaseLost("Lease expired while acquiring the row lock")
    return record


def terminal(session, record, status: str, message: str | None = None) -> None:
    record.status = status
    record.completed_at = now_iso()
    record.lease_token = record.lease_expires_at = None
    if message:
        record.warnings = [*record.warnings, message]
        append_event(
            session,
            record.id,
            "error" if status == "failed" else "progress",
            {
                "stage": status,
                "message": message,
            },
        )
    record.agent_state = {
        "version": CHECKPOINT_VERSION,
        "stage": (record.agent_state or {}).get("stage"),
    }
    append_event(session, record.id, "result", {"run": run_payload(record)})
    append_event(session, record.id, "done", {"status": status})


class RunQueue:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings

    def claim(self) -> Claim | None:
        with self.database.sessions() as session, session.begin():
            lock_queue(session)
            now = database_now(session)
            eligible = or_(
                and_(RunRecord.status == "queued", RunRecord.available_at <= now),
                and_(
                    RunRecord.status == "running",
                    or_(
                        RunRecord.lease_expires_at.is_(None),
                        RunRecord.lease_expires_at <= now,
                        RunRecord.deadline_at <= now,
                    ),
                ),
            )
            # Bound cleanup work per transaction; oldest jobs receive priority.
            records = list(
                session.scalars(
                    select(RunRecord)
                    .where(eligible)
                    .order_by(
                        RunRecord.created_at,
                        RunRecord.id,
                    )
                    .limit(100)
                )
            )
            for selected in records:
                # Lock/recheck after a concurrent heartbeat/finish/cancel.
                count = session.execute(
                    update(RunRecord)
                    .where(
                        RunRecord.id == selected.id,
                        eligible,
                    )
                    .values(attempt_count=RunRecord.attempt_count)
                    .execution_options(
                        synchronize_session=False,
                    )
                ).rowcount
                if not count:
                    continue
                record = session.get(RunRecord, selected.id, populate_existing=True)
                now = database_now(session)
                if record.deadline_at is not None and record.deadline_at <= now:
                    terminal(session, record, "failed", "任务已超过总时限，请重新提交。")
                    continue
                if record.attempt_count >= self.settings.run_max_attempts:
                    terminal(session, record, "failed", "任务已达到最大恢复次数，请重新提交。")
                    continue
                record.deadline_at = record.deadline_at or now + self.settings.run_timeout_seconds
                record.attempt_count += 1
                record.status = "running"
                record.lease_token = token_hex(24)
                record.lease_expires_at = min(
                    now + self.settings.run_lease_seconds, record.deadline_at
                )
                resumed = record.attempt_count > 1
                append_event(
                    session,
                    record.id,
                    "progress",
                    {
                        "stage": "resumed" if resumed else "started",
                        "message": f"工作进程已接续任务（第 {record.attempt_count} 次尝试）"
                        if resumed
                        else "推荐任务已开始",
                        "attempt_count": record.attempt_count,
                    },
                )
                return Claim(
                    record.id, record.lease_token, record.attempt_count, record.deadline_at - now
                )
        return None

    def renew(self, claim: Claim) -> bool:
        try:
            with self.database.sessions() as session, session.begin():
                record = owned(session, claim)
                record.lease_expires_at = min(
                    database_now(session) + self.settings.run_lease_seconds, record.deadline_at
                )
            return True
        except LeaseLost:
            return False

    def is_owned(self, claim: Claim) -> bool:
        with self.database.sessions() as session:
            now = database_now(session)
            return bool(
                session.scalar(
                    select(RunRecord.id).where(
                        RunRecord.id == claim.run_id,
                        RunRecord.lease_token == claim.token,
                        RunRecord.attempt_count == claim.attempt,
                        RunRecord.status == "running",
                        RunRecord.lease_expires_at > now,
                        RunRecord.deadline_at > now,
                    )
                )
            )

    def event(self, claim: Claim, stage: str, message: str, updates: dict) -> None:
        with self.database.sessions() as session, session.begin():
            record = owned(session, claim)
            for key in ("intent", "mode"):
                if key in updates:
                    setattr(record, key, updates[key])
            append_event(
                session,
                record.id,
                "progress",
                {"stage": stage, "message": message, "attempt_count": claim.attempt},
            )

    def checkpoint(self, claim: Claim, snapshot: dict) -> None:
        with self.database.sessions() as session, session.begin():
            record = owned(session, claim)
            record.agent_state = snapshot

    def complete(self, claim: Claim, state: dict) -> None:
        with self.database.sessions() as session, session.begin():
            record = owned(session, claim)
            record.intent, record.results = state["intent"], state["results"]
            record.warnings, record.mode = state["warnings"], state["mode"]
            uncertain = any(
                "预算尚未核验" in text for row in record.results for text in row["tradeoffs"]
            )
            degraded = (self.settings.llm_enabled and record.mode == "rules") or (
                self.settings.retrieval_mode in {"hybrid", "indexed_hybrid"}
                and record.intent.get("retrieval") not in {"hybrid_rrf", "indexed_hybrid_rrf"}
            )
            record.agent_state = {"stage": "explain"}
            terminal(
                session,
                record,
                "partial" if not record.results or uncertain or degraded else "completed",
            )

    def fail(self, claim: Claim, message="推荐任务暂时失败，请重试。没有伪造替代结果。") -> None:
        try:
            with self.database.sessions() as session, session.begin():
                record = owned(session, claim, enforce_time=False)
                terminal(session, record, "failed", message)
        except LeaseLost:
            pass

    def abandon(self, claim: Claim) -> None:
        """Supervisor has reaped its child, or embedded execution has returned."""
        try:
            with self.database.sessions() as session, session.begin():
                record = owned(session, claim, enforce_time=False)
                now = database_now(session)
                if (
                    now >= record.deadline_at
                    or record.attempt_count >= self.settings.run_max_attempts
                ):
                    terminal(session, record, "failed", "任务超时或恢复次数已用尽，请重新提交。")
                else:
                    record.status = "queued"
                    record.available_at = now + min(2 ** (claim.attempt - 1), 8)
                    record.lease_token = record.lease_expires_at = None
                    append_event(
                        session,
                        record.id,
                        "progress",
                        {
                            "stage": "retrying",
                            "message": "工作进程中断；任务已保存，将从安全检查点重试。",
                        },
                    )
        except LeaseLost:
            pass

    def cancel(self, session, run_id: str) -> RunRecord:
        # Authorization is performed by the API before entering this function.
        session.execute(
            update(RunRecord)
            .where(RunRecord.id == run_id)
            .values(
                attempt_count=RunRecord.attempt_count,
            )
            .execution_options(synchronize_session=False)
        )
        record = session.get(RunRecord, run_id, populate_existing=True)
        if record.status not in TERMINAL:
            terminal(session, record, "cancelled", "任务已取消；不再接受工作进程的迟到结果。")
        return record
