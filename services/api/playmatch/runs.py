import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import func, select

from playmatch.config import Settings
from playmatch.database import Database
from playmatch.models import FeedbackRecord, GameRecord, RunEventRecord, RunRecord
from playmatch.workflow import build_graph

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "partial", "failed"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def run_payload(record: RunRecord) -> dict:
    return {
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
        )
    }


def append_event(session, run_id: str, event: str, payload: dict) -> None:
    last = (
        session.scalar(
            select(func.max(RunEventRecord.sequence)).where(RunEventRecord.run_id == run_id)
        )
        or 0
    )
    sequence = last + 1
    session.add(
        RunEventRecord(
            run_id=run_id, sequence=sequence, event=event, payload={"sequence": sequence, **payload}
        )
    )
    session.flush()


def recover_interrupted(database: Database) -> None:
    with database.sessions() as session:
        for run in session.scalars(
            select(RunRecord).where(RunRecord.status.in_(["queued", "running"]))
        ):
            message = "上一次服务进程在任务完成前中断。结果未被伪造为成功，请使用新的请求重试。"
            run.status = "failed"
            run.completed_at = now_iso()
            run.warnings = [*run.warnings, message]
            append_event(session, run.id, "error", {"message": message})
            append_event(session, run.id, "done", {"status": "failed"})
        session.commit()


class RunExecutor:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self.pool = ThreadPoolExecutor(
            max_workers=settings.run_workers, thread_name_prefix="playmatch-run"
        )
        self.slots = threading.BoundedSemaphore(settings.max_pending_runs)

    def reserve(self) -> bool:
        return self.slots.acquire(blocking=False)

    def release(self) -> None:
        self.slots.release()

    def submit_reserved(self, run_id: str) -> None:
        future = self.pool.submit(self.execute, run_id)
        future.add_done_callback(lambda _future: self.release())

    def execute(self, run_id: str) -> None:
        started = time.monotonic()
        try:
            with self.database.sessions() as session:
                record = session.get(RunRecord, run_id)
                if not record or record.status != "queued":
                    return
                record.status = "running"
                request = record.request_payload
                catalog = [
                    game.payload
                    for game in session.scalars(select(GameRecord).order_by(GameRecord.id))
                ]
                feedback = (
                    [
                        {"game_id": row.game_id, "kind": row.kind}
                        for row in session.scalars(
                            select(FeedbackRecord).where(FeedbackRecord.user_id == record.user_id)
                        )
                    ]
                    if record.user_id
                    else []
                )
                append_event(
                    session, run_id, "progress", {"stage": "started", "message": "推荐任务已开始"}
                )
                session.commit()

            def progress(stage: str, message: str, updates: dict):
                if time.monotonic() - started > self.settings.run_timeout_seconds:
                    raise TimeoutError("run deadline")
                with self.database.sessions() as session:
                    record = session.get(RunRecord, run_id)
                    if not record or record.status != "running":
                        raise RuntimeError("run no longer active")
                    record.agent_state = {
                        "last_stage": stage,
                        "updated_at": now_iso(),
                        "workflow_version": "0.1.0",
                    }
                    for key in ("intent", "mode"):
                        if key in updates:
                            setattr(record, key, updates[key])
                    append_event(session, run_id, "progress", {"stage": stage, "message": message})
                    session.commit()

            result = build_graph(self.settings, progress).invoke(
                {"request": request, "catalog": catalog, "feedback": feedback},
                config={"recursion_limit": 10},
            )
            with self.database.sessions() as session:
                record = session.get(RunRecord, run_id)
                if record.status != "running":
                    return
                record.intent = result["intent"]
                record.results = result["results"]
                record.warnings = result["warnings"]
                record.mode = result["mode"]
                uncertain_budget = any(
                    "预算尚未核验" in text for row in record.results for text in row["tradeoffs"]
                )
                degraded_model = self.settings.llm_enabled and record.mode == "rules"
                record.status = (
                    "partial"
                    if not record.results or uncertain_budget or degraded_model
                    else "completed"
                )
                record.completed_at = now_iso()
                append_event(session, run_id, "result", {"run": run_payload(record)})
                append_event(session, run_id, "done", {"status": record.status})
                session.commit()
        except Exception as error:
            # Deliberately omit raw exception text: provider/model errors can contain credentials.
            logger.warning("run_failed run_id=%s error_type=%s", run_id, type(error).__name__)
            with self.database.sessions() as session:
                record = session.get(RunRecord, run_id)
                if record and record.status not in TERMINAL:
                    record.status = "failed"
                    record.completed_at = now_iso()
                    message = (
                        "任务超时，请稍后重试。"
                        if isinstance(error, TimeoutError)
                        else "推荐任务暂时失败，请重试。没有生成替代价格或伪造结果。"
                    )
                    record.warnings = [*record.warnings, message]
                    append_event(session, run_id, "error", {"message": message})
                    append_event(session, run_id, "done", {"status": "failed"})
                    session.commit()

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)
