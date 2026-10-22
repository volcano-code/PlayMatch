"""Durable application checkpoints around a bounded LangGraph workflow.

This is not LangGraph's native checkpointer. Only completed safe stages are
replayed; uncommitted read-only provider calls may repeat after a crash.
"""

import hashlib
import json
import logging
import threading
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from playmatch.candidate_search import CatalogChanged, catalog_context, shortlist
from playmatch.models import FeedbackRecord
from playmatch.run_queue import (  # re-export stable API imports
    CHECKPOINT_VERSION,
    Claim,
    LeaseLost,
    RunQueue,
    owned,
)
from playmatch.run_queue import (
    TERMINAL as TERMINAL,
)
from playmatch.run_queue import (
    append_event as append_event,
)
from playmatch.run_queue import (
    now_iso as now_iso,
)
from playmatch.run_queue import (
    run_payload as run_payload,
)
from playmatch.semantic_index import search_index
from playmatch.workflow import build_graph

logger = logging.getLogger(__name__)
SAFE_STAGES = {"understand", "retrieve", "filter"}
MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024
# Public metadata only. A key rotation must not invalidate an otherwise same model.
POLICY_FIELDS = (
    "retrieval_mode",
    "retrieval_candidate_limit",
    "embedding_base_url",
    "embedding_model",
    "embedding_revision",
    "semantic_min_score",
    "llm_base_url",
    "llm_model",
)


def hash_json(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def context_hash(state: dict, settings) -> str:
    return hash_json(
        {
            "catalog": state["catalog"],
            "catalog_context": state.get("catalog_context")
            if (state.get("catalog_context") or {}).get("eligible_count", 0)
            > settings.retrieval_candidate_limit
            else None,
            "feedback": state["feedback"],
            "request": state["request"],
            "policy": {k: getattr(settings, k) for k in POLICY_FIELDS},
            "llm_enabled": settings.llm_enabled,
            "embedding_enabled": settings.embedding_enabled,
        }
    )


def snapshot_state(state: dict, stage: str, context: str) -> dict | None:
    # Catalog and feedback are freshly loaded and fingerprinted for each attempt.
    payload = {
        key: deepcopy(value) for key, value in state.items() if key not in {"catalog", "feedback"}
    }
    snapshot = {"version": CHECKPOINT_VERSION, "stage": stage, "context": context, "state": payload}
    snapshot["checksum"] = hash_json(snapshot)
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_CHECKPOINT_BYTES:
        return None  # Retain the previous safe checkpoint; never truncate state.
    return snapshot


def restore_state(initial: dict, snapshot: dict, context: str) -> tuple[dict, str | None]:
    if (
        not snapshot
        or snapshot.get("version") != CHECKPOINT_VERSION
        or snapshot.get("context") != context
    ):
        return initial, None
    check = {k: v for k, v in snapshot.items() if k != "checksum"}
    try:
        if snapshot.get("checksum") != hash_json(check):
            return initial, None
        stage = snapshot.get("stage")
        values = snapshot["state"]
        required = {"request", "intent", "mode", "warnings"}
        if stage in {"retrieve", "filter"}:
            required.add("candidates")
        if (
            stage not in SAFE_STAGES
            or not isinstance(values, dict)
            or not required <= values.keys()
        ):
            return initial, None
        return {
            **initial,
            **deepcopy(values),
            "catalog": initial["catalog"],
            "feedback": initial["feedback"],
        }, stage
    except (TypeError, ValueError, KeyError):
        return initial, None


def execute_claim(database, settings, claim: Claim, *, stop_event=None) -> None:
    queue = RunQueue(database, settings)
    finished, lost = threading.Event(), threading.Event()

    def heartbeat():
        while not finished.wait(settings.run_lease_seconds / 3):
            try:
                if not queue.renew(claim):
                    lost.set()
                    return
            except SQLAlchemyError:
                lost.set()
                return

    beat = threading.Thread(target=heartbeat, daemon=True, name="run-heartbeat")
    beat.start()
    try:
        with database.sessions() as session, session.begin():
            record = owned(session, claim)
            catalog, catalog_version = catalog_context(session, settings.retrieval_candidate_limit)
            initial = {
                "request": deepcopy(record.request_payload),
                "catalog": catalog,
                "catalog_context": catalog_version,
                "feedback": [
                    {"game_id": row.game_id, "kind": row.kind}
                    for row in session.scalars(
                        select(FeedbackRecord)
                        .where(FeedbackRecord.user_id == record.user_id)
                        .order_by(FeedbackRecord.game_id, FeedbackRecord.kind)
                    )
                ]
                if record.user_id
                else [],
            }
            snapshot = deepcopy(record.agent_state)
        context = context_hash(initial, settings)
        state, stage = restore_state(initial, snapshot, context)
        if claim.attempt > 1:
            queue.event(
                claim,
                "checkpoint",
                f"已恢复 {stage} 阶段。"
                if stage
                else "检查点缺失、过期或版本变化，重新执行并继续遵守原始条件。",
                {},
            )

        def check_active():
            if lost.is_set() or (stop_event and stop_event.is_set()):
                raise LeaseLost("Worker stopped or lease expired")

        def progress(stage: str, message: str, updates: dict):
            check_active()
            queue.event(claim, stage, message, updates)

        def candidate_resolver(intent, query, feedback):
            with database.sessions() as session:
                return shortlist(
                    session,
                    intent,
                    query,
                    feedback,
                    settings.retrieval_candidate_limit,
                    expected_revision=catalog_version["revision"],
                )

        graph = build_graph(
            settings,
            progress,
            resume_after=stage,
            catalog_resolver=candidate_resolver,
            semantic_resolver=lambda catalog, query: search_index(
                database, settings, catalog, query
            ),
        )
        # v1 update format is intentionally explicit for the locked LangGraph API.
        for update in graph.stream(
            state, stream_mode="updates", version="v1", config={"recursion_limit": 10}
        ):
            check_active()
            for stage, delta in update.items():
                state.update(delta)
                if stage in SAFE_STAGES:
                    checkpoint = snapshot_state(state, stage, context)
                    if checkpoint is not None:
                        queue.checkpoint(claim, checkpoint)
                # Budget is never a resumable checkpoint: re-query prices after
                # interruption. Explain and terminal events commit atomically below.
        queue.complete(claim, state)
    except CatalogChanged:
        queue.abandon(claim)  # Next attempt hashes the new metadata revision.
    except LeaseLost:
        pass  # Never let an old attempt publish or alter its successor.
    except SQLAlchemyError:
        logger.warning("run_database_unavailable run_id=%s attempt=%s", claim.run_id, claim.attempt)
        # The lease will expire. No success is emitted from volatile state.
    except Exception as error:
        logger.warning(
            "run_failed run_id=%s attempt=%s error_type=%s",
            claim.run_id,
            claim.attempt,
            type(error).__name__,
        )
        queue.fail(
            claim,
            "任务超时，请重新提交。"
            if isinstance(error, TimeoutError)
            else "推荐任务暂时失败，请重试。没有伪造替代结果。",
        )
    finally:
        finished.set()
        beat.join(timeout=settings.run_lease_seconds / 3 + 1)


class RunExecutor:
    """Local quickstart: embedded consumers of the same durable queue.

    External workers add a subprocess supervisor for hard termination. Embedded mode
    is cooperative and intentionally does not claim it can kill blocked Python threads.
    """

    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self.stop = threading.Event()
        self.threads = [
            threading.Thread(target=self.loop, daemon=True, name=f"run-consumer-{i}")
            for i in range(settings.run_workers)
        ]
        for thread in self.threads:
            thread.start()

    def loop(self):
        queue = RunQueue(self.database, self.settings)
        while not self.stop.is_set():
            try:
                claim = queue.claim()
                if claim:
                    execute_claim(self.database, self.settings, claim, stop_event=self.stop)
                    queue.abandon(claim)  # No-op if completed or ownership has changed.
                    continue
            except SQLAlchemyError:
                logger.warning("run_consumer_database_unavailable")
            self.stop.wait(self.settings.run_poll_seconds)

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=2)
