"""Versioned exact-vector snapshots. Network work never holds a DB transaction.

Ready snapshots are immutable. Publishing the active pointer is a catalog-locked,
compare-and-swap transaction; incomplete or stale builds are never served.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, update

from playmatch.models import (
    CatalogLock,
    EmbeddingIndex,
    EmbeddingPointer,
    EmbeddingVector,
    GameRecord,
)
from playmatch.retrieval import (
    EmbeddingUnavailable,
    document,
    embedding_client,
    fingerprint,
    normalize_vector,
)

TEXT_VERSION = "retrieval-text-v1"
MAX_INDEX_GAMES = 5000
MAX_SNAPSHOTS = 20


class IndexUnavailable(EmbeddingUnavailable):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def model_identity(settings) -> str:
    # Endpoint is hashed, never returned. API key rotation does not change the vector space.
    return sha(
        [
            settings.embedding_base_url.rstrip("/"),
            settings.embedding_model,
            settings.embedding_revision,
            TEXT_VERSION,
        ]
    )


def configured(settings) -> bool:
    return settings.embedding_enabled and bool(settings.embedding_revision.strip())


def lock_catalog(session) -> None:
    result = session.execute(
        update(CatalogLock)
        .where(CatalogLock.id == 1)
        .values(
            revision=CatalogLock.revision,
        )
    )
    if result.rowcount != 1:
        raise IndexUnavailable("migration_required")


def catalog_rows(session) -> list[dict]:
    from playmatch.candidate_search import eligibility
    from playmatch.run_queue import database_now

    rows = list(
        session.scalars(
            select(GameRecord.payload)
            .where(*eligibility(database_now(session)))
            .order_by(GameRecord.id)
            .limit(MAX_INDEX_GAMES + 1)
        )
    )
    if len(rows) > MAX_INDEX_GAMES:
        raise IndexUnavailable("catalog_too_large")
    return rows


def checked_vector(row: EmbeddingVector, dimensions: int | None = None) -> list[float]:
    value = row.vector
    normalized = normalize_vector(value)
    # Stored vectors must already be normalized and match the stored checksum.
    if dimensions is not None and len(value) != dimensions:
        raise IndexUnavailable("index_corrupt")
    if not math.isclose(math.hypot(*value), 1, abs_tol=1e-6):
        raise IndexUnavailable("index_corrupt")
    if sha(value) != row.vector_hash:
        raise IndexUnavailable("index_corrupt")
    return normalized


def _report(index: EmbeddingIndex) -> dict:
    return {
        "index_id": index.id,
        "status": index.status,
        "catalog_fingerprint": index.catalog_hash,
        "document_count": index.document_count,
        "dimensions": index.dimensions,
        "reused_count": index.reused_count,
        "encoded_count": index.encoded_count,
        "started_generation": index.started_generation,
        "failure_code": index.failure_code,
    }


def index_status(database, settings) -> dict:
    """Metadata readiness only: does not probe the provider or validate every vector."""
    with database.sessions() as session:
        pointer = session.get(EmbeddingPointer, 1)
        generation = pointer.generation if pointer else 0
        index = (
            session.get(EmbeddingIndex, pointer.index_id) if pointer and pointer.index_id else None
        )
        status = "not_configured" if not configured(settings) else "missing"
        if index and configured(settings):
            if index.status != "ready":
                status = "corrupt"
            elif index.identity != model_identity(settings):
                status = "model_mismatch"
            else:
                try:
                    status = (
                        "ready"
                        if index.catalog_hash == fingerprint(catalog_rows(session))
                        else "stale"
                    )
                except IndexUnavailable:
                    status = "stale"
        return {
            "status": status,
            "active_index_id": index.id if index else None,
            "generation": generation,
            "document_count": index.document_count if index else 0,
            "dimensions": index.dimensions if index else None,
            "catalog_fingerprint": index.catalog_hash if index else None,
            "search_method": "exact_cosine",
            "max_documents": MAX_INDEX_GAMES,
            "provider_probed": False,
        }


def activate_index(database, settings, index_id: str, expected_generation: int) -> dict:
    if type(expected_generation) is not int or expected_generation < 0:
        raise IndexUnavailable("invalid_generation")
    if not configured(settings):
        raise IndexUnavailable("not_configured")
    with database.sessions() as session, session.begin():
        lock_catalog(session)
        pointer = session.get(EmbeddingPointer, 1)
        index = session.get(EmbeddingIndex, index_id)
        if not pointer or not index or index.status != "ready" or not index.dimensions:
            raise IndexUnavailable("index_not_ready")
        if pointer.generation != expected_generation:
            raise IndexUnavailable("activation_conflict")
        if index.identity != model_identity(settings):
            raise IndexUnavailable("model_mismatch")
        catalog = catalog_rows(session)
        if index.catalog_hash != fingerprint(catalog):
            raise IndexUnavailable("catalog_changed")
        expected = {game["id"]: sha(document(game)) for game in catalog}
        vectors = list(
            session.scalars(select(EmbeddingVector).where(EmbeddingVector.index_id == index.id))
        )
        if len(vectors) != index.document_count or len(vectors) != len(expected):
            raise IndexUnavailable("index_corrupt")
        for row in vectors:
            if expected.get(row.game_id) != row.text_hash:
                raise IndexUnavailable("index_corrupt")
            checked_vector(row, index.dimensions)
        changed = pointer.index_id != index_id
        if changed:
            pointer.index_id = index_id
            pointer.generation += 1
        return {
            **_report(index),
            "active": True,
            "generation": pointer.generation,
            "changed": changed,
        }


def build_index(
    database,
    settings,
    *,
    resume_id: str | None = None,
    activate: bool = False,
    batch_size: int = 16,
    timeout_seconds: float = 300,
    client=None,
) -> dict:
    """Explicit operator command; resumable batch writes, never an HTTP background task."""
    if not configured(settings):
        raise IndexUnavailable("not_configured")
    if type(batch_size) is not int or not 1 <= batch_size <= 16:
        raise IndexUnavailable("invalid_batch_size")
    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 3600:
        raise IndexUnavailable("invalid_timeout")
    deadline = time.monotonic() + timeout_seconds
    identity = model_identity(settings)
    owner = str(uuid4())
    # Snapshot and claim build ownership without doing network work under the lock.
    with database.sessions() as session, session.begin():
        lock_catalog(session)
        catalog = catalog_rows(session)
        if not 1 <= len(catalog) <= MAX_INDEX_GAMES:
            raise IndexUnavailable("catalog_size")
        catalog_hash = fingerprint(catalog)
        pointer = session.get(EmbeddingPointer, 1)
        if not pointer:
            raise IndexUnavailable("migration_required")
        active_id = pointer.index_id
        if resume_id:
            index = session.get(EmbeddingIndex, resume_id)
            if not index or index.identity != identity or index.catalog_hash != catalog_hash:
                raise IndexUnavailable("resume_mismatch")
            if index.status not in {"building", "failed", "ready"}:
                raise IndexUnavailable("resume_mismatch")
            if index.status == "ready":
                ready_report = _report(index)
                index_id = index.id
                expected_generation = index.started_generation
            else:
                if index.lease_until > time.time():
                    raise IndexUnavailable("build_busy")
                ready_report = None
                index_id = index.id
                expected_generation = index.started_generation
        else:
            if session.scalar(select(func.count()).select_from(EmbeddingIndex)) >= MAX_SNAPSHOTS:
                raise IndexUnavailable("snapshot_limit")
            index = EmbeddingIndex(
                id=str(uuid4()),
                identity=identity,
                catalog_hash=catalog_hash,
                text_version=TEXT_VERSION,
                status="building",
                document_count=len(catalog),
                dimensions=None,
                reused_count=0,
                encoded_count=0,
                started_generation=pointer.generation,
                created_at=now(),
                completed_at=None,
                failure_code=None,
                owner=None,
                lease_until=0,
            )
            session.add(index)
            ready_report = None
            index_id = index.id
            expected_generation = index.started_generation
        if ready_report is None:
            index.status, index.failure_code = "building", None
            index.owner = owner
            index.lease_until = time.time() + min(60, timeout_seconds + 5)
    if ready_report is not None:
        return (
            activate_index(database, settings, index_id, expected_generation)
            if activate
            else ready_report
        )

    client = client or embedding_client(
        settings.embedding_base_url, settings.embedding_model, settings.embedding_api_key
    )
    try:
        with database.sessions() as session:
            staged = {
                row.game_id: row
                for row in session.scalars(
                    select(EmbeddingVector).where(
                        EmbeddingVector.index_id == index_id,
                    )
                )
            }
            index = session.get(EmbeddingIndex, index_id)
            dimensions = index.dimensions
            for game in catalog:
                row = staged.get(game["id"])
                if row:
                    if row.text_hash != sha(document(game)):
                        raise IndexUnavailable("index_corrupt")
                    checked_vector(row, dimensions)
            previous = session.get(EmbeddingIndex, active_id) if active_id else None
            reusable = {}
            if previous and previous.status == "ready" and previous.identity == identity:
                for row in session.scalars(
                    select(EmbeddingVector).where(EmbeddingVector.index_id == active_id)
                ):
                    try:
                        reusable[(row.game_id, row.text_hash)] = checked_vector(
                            row, previous.dimensions
                        )
                    except EmbeddingUnavailable:
                        # A corrupt previous row is re-encoded, not propagated into a new snapshot.
                        pass
        pending = [game for game in catalog if game["id"] not in staged]
        for start in range(0, len(pending), batch_size):
            if time.monotonic() >= deadline:
                raise IndexUnavailable("build_timeout")
            batch = pending[start : start + batch_size]
            cached = {
                game["id"]: reusable[(game["id"], sha(document(game)))]
                for game in batch
                if (game["id"], sha(document(game))) in reusable
            }
            missing = [game for game in batch if game["id"] not in cached]
            # Renew ownership before a bounded HTTP batch. It expires after a crashed process.
            with database.sessions() as session, session.begin():
                renewed = session.execute(
                    update(EmbeddingIndex)
                    .where(
                        EmbeddingIndex.id == index_id,
                        EmbeddingIndex.owner == owner,
                        EmbeddingIndex.status == "building",
                    )
                    .values(lease_until=time.time() + 60)
                )
                if renewed.rowcount != 1:
                    raise IndexUnavailable("build_ownership_lost")
            encoded = (
                client.encode_documents(
                    [document(game) for game in missing],
                    min(settings.embedding_timeout_seconds, max(0.01, deadline - time.monotonic())),
                )
                if missing
                else []
            )
            if len(encoded) != len(missing):
                raise IndexUnavailable("invalid_embeddings")
            vectors = {
                **cached,
                **{
                    game["id"]: normalize_vector(vector)
                    for game, vector in zip(missing, encoded, strict=True)
                },
            }
            for vector in vectors.values():
                dimensions = dimensions or len(vector)
                if len(vector) != dimensions:
                    raise IndexUnavailable("dimension_mismatch")
            with database.sessions() as session, session.begin():
                # Fencing token prevents a timed-out process from committing after a resume.
                fenced = session.execute(
                    update(EmbeddingIndex)
                    .where(
                        EmbeddingIndex.id == index_id,
                        EmbeddingIndex.owner == owner,
                        EmbeddingIndex.status == "building",
                    )
                    .values(
                        dimensions=dimensions,
                        reused_count=EmbeddingIndex.reused_count + len(cached),
                        encoded_count=EmbeddingIndex.encoded_count + len(missing),
                    )
                )
                if fenced.rowcount != 1:
                    raise IndexUnavailable("build_ownership_lost")
                for game in batch:
                    vector = vectors[game["id"]]
                    session.add(
                        EmbeddingVector(
                            index_id=index_id,
                            game_id=game["id"],
                            text_hash=sha(document(game)),
                            vector=vector,
                            vector_hash=sha(vector),
                        )
                    )
        with database.sessions() as session, session.begin():
            lock_catalog(session)
            index = session.get(EmbeddingIndex, index_id)
            if index.owner != owner or index.status != "building":
                raise IndexUnavailable("build_ownership_lost")
            if fingerprint(catalog_rows(session)) != catalog_hash:
                raise IndexUnavailable("catalog_changed")
            count = session.scalar(
                select(func.count())
                .select_from(EmbeddingVector)
                .where(
                    EmbeddingVector.index_id == index_id,
                )
            )
            if count != len(catalog) or not dimensions:
                raise IndexUnavailable("index_corrupt")
            index.status, index.completed_at = "ready", now()
            index.owner, index.lease_until = None, 0
            report = _report(index)
    except Exception as error:
        code = error.code if isinstance(error, IndexUnavailable) else "embedding_or_storage_failure"
        with database.sessions() as session, session.begin():
            session.execute(
                update(EmbeddingIndex)
                .where(
                    EmbeddingIndex.id == index_id,
                    EmbeddingIndex.owner == owner,
                    EmbeddingIndex.status == "building",
                )
                .values(status="failed", failure_code=code, owner=None, lease_until=0)
            )
        raise IndexUnavailable(code) from None
    return activate_index(database, settings, index_id, expected_generation) if activate else report


def search_index(
    database, settings, catalog: list[dict], query: str, *, client=None
) -> tuple[list[float], dict]:
    if not configured(settings):
        raise IndexUnavailable("not_configured")
    with database.sessions() as session:
        pointer = session.get(EmbeddingPointer, 1)
        index = (
            session.get(EmbeddingIndex, pointer.index_id) if pointer and pointer.index_id else None
        )
        if not index or index.status != "ready":
            raise IndexUnavailable("index_missing")
        if index.identity != model_identity(settings):
            raise IndexUnavailable("model_mismatch")
        if index.catalog_hash != fingerprint(catalog):
            raise IndexUnavailable("index_stale")
        records = {
            row.game_id: row
            for row in session.scalars(
                select(EmbeddingVector).where(
                    EmbeddingVector.index_id == index.id,
                )
            )
        }
        if len(records) != len(catalog) or len(records) != index.document_count:
            raise IndexUnavailable("index_corrupt")
        vectors = []
        for game in catalog:
            row = records.get(game["id"])
            if not row or row.text_hash != sha(document(game)):
                raise IndexUnavailable("index_corrupt")
            vectors.append(checked_vector(row, index.dimensions))
        metadata = {
            "index_id": index.id,
            "index_generation": pointer.generation,
            "index_dimensions": index.dimensions,
        }
        dimensions = index.dimensions
    # At most one query embedding; document vectors come only from the ready snapshot.
    client = client or embedding_client(
        settings.embedding_base_url, settings.embedding_model, settings.embedding_api_key
    )
    encoded = client.encode([query[:6000]], settings.embedding_timeout_seconds)
    if len(encoded) != 1:
        raise IndexUnavailable("invalid_embeddings")
    vector = normalize_vector(encoded[0])
    if len(vector) != dimensions:
        raise IndexUnavailable("dimension_mismatch")
    scores = [sum(a * b for a, b in zip(row, vector, strict=True)) for row in vectors]
    return scores, metadata


def delete_index(database, index_id: str) -> dict:
    """Explicit operator maintenance: cannot remove an active or currently leased build."""
    with database.sessions() as session, session.begin():
        lock_catalog(session)
        pointer = session.get(EmbeddingPointer, 1)
        index = session.get(EmbeddingIndex, index_id)
        if not index:
            raise IndexUnavailable("index_missing")
        if pointer and pointer.index_id == index_id:
            raise IndexUnavailable("active_index_protected")
        if index.status == "building" and index.lease_until > time.time():
            raise IndexUnavailable("build_busy")
        session.delete(index)
        return {"deleted_index_id": index_id}
