"""Synthetic vectors test storage semantics, not neural recommendation quality."""

import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.main import create_app
from playmatch.models import EmbeddingIndex, EmbeddingPointer, EmbeddingVector, GameRecord
from playmatch.retrieval import EmbeddingUnavailable, document
from playmatch.semantic_index import (
    IndexUnavailable,
    activate_index,
    build_index,
    delete_index,
    index_status,
    model_identity,
    search_index,
)
from playmatch.workflow import build_graph
from sqlalchemy import select, text
from sqlalchemy.engine import make_url


class Synthetic:
    def __init__(self):
        self.docs = []
        self.queries = []
        self.fail = False

    def encode_documents(self, texts, timeout):
        self.docs.extend(texts)
        if self.fail:
            raise EmbeddingUnavailable("fixture secret must never escape")
        return [[1, 2, 3] for _ in texts]

    def encode(self, texts, timeout):
        self.queries.extend(texts)
        return [[1, 2, 3] for _ in texts]


@pytest.fixture
def index_settings():
    return Settings(
        _env_file=None,
        retrieval_mode="indexed_hybrid",
        embedding_model="synthetic-v1",
        embedding_revision="fixture-immutable-v1",
        embedding_base_url="http://127.0.0.1:9090/v1",
        llm_api_key="",
        llm_model="",
    )


@pytest.fixture
def index_db(tmp_path, catalog):
    url = os.environ.get("INDEX_TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "index.db"))
    admin = None
    schema = None
    if os.environ.get("INDEX_TEST_DATABASE_URL"):
        assert os.environ.get("PLAYMATCH_ALLOW_DISPOSABLE_DB") == "yes", (
            "Explicit disposable-db consent required"
        )
        assert make_url(url).drivername.startswith("postgresql")
        schema = "pm_index_" + uuid4().hex
        admin = Database(url)
        with admin.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = (
            make_url(url)
            .update_query_dict({"options": "-csearch_path=" + schema})
            .render_as_string(hide_password=False)
        )
    db = Database(url)
    try:
        db.migrate()
        with db.sessions() as session, session.begin():
            for game in catalog:
                session.add(GameRecord(id=game["id"], title=game["title"], payload=game))
        yield db
    finally:
        db.close()
        if admin:
            with admin.engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.close()


def change(db, game_id="space"):
    with db.sessions() as session, session.begin():
        row = session.get(GameRecord, game_id)
        row.payload = {**row.payload, "description": row.payload["description"] + " new"}


def current_catalog(db):
    with db.sessions() as session:
        return [g.payload for g in session.scalars(select(GameRecord).order_by(GameRecord.id))]


def test_unconfigured_never_calls_provider(index_db, index_settings):
    index_settings.embedding_revision = ""
    assert index_status(index_db, index_settings)["status"] == "not_configured"
    with pytest.raises(IndexUnavailable, match="not_configured"):
        build_index(index_db, index_settings, client=Synthetic())


def test_build_is_staged_then_activation_and_restart(index_db, index_settings, catalog):
    client = Synthetic()
    built = build_index(index_db, index_settings, client=client)
    assert built["status"] == "ready" and built["encoded_count"] == len(catalog)
    assert index_status(index_db, index_settings)["status"] == "missing"
    activated = activate_index(index_db, index_settings, built["index_id"], 0)
    assert activated["generation"] == 1
    fresh = Database(index_db.engine.url.render_as_string(hide_password=False))
    try:
        scores, meta = search_index(fresh, index_settings, catalog, "太空", client=client)
        assert len(scores) == len(catalog) and all(abs(score - 1) < 1e-9 for score in scores)
        assert meta["index_id"] == built["index_id"]
        assert client.queries == ["太空"]
        assert len(client.docs) == len(catalog)  # No document encoding during search.
    finally:
        fresh.close()


def test_incremental_reuses_unchanged_documents(index_db, index_settings):
    first_client = Synthetic()
    old = build_index(index_db, index_settings, client=first_client, activate=True)
    change(index_db)
    assert index_status(index_db, index_settings)["status"] == "stale"
    next_client = Synthetic()
    new = build_index(index_db, index_settings, client=next_client, activate=True)
    assert new["encoded_count"] == 1 and new["reused_count"] == 3
    assert len(next_client.docs) == 1
    assert next_client.docs[0] == document(
        next(g for g in current_catalog(index_db) if g["id"] == "space")
    )
    with pytest.raises(IndexUnavailable, match="catalog_changed"):
        activate_index(index_db, index_settings, old["index_id"], 2)


def test_failed_build_preserves_active_and_is_resumable(index_db, index_settings):
    old = build_index(index_db, index_settings, client=Synthetic(), activate=True)
    index_settings.embedding_revision = "new-model-space"
    client = Synthetic()
    original = client.encode_documents
    calls = 0

    def fails_second(texts, timeout):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("upstream-private-secret")
        return original(texts, timeout)

    client.encode_documents = fails_second
    with pytest.raises(IndexUnavailable) as error:
        build_index(index_db, index_settings, client=client, batch_size=2, activate=True)
    assert "secret" not in str(error.value)
    with index_db.sessions() as session:
        assert session.get(EmbeddingPointer, 1).index_id == old["index_id"]
        failed = session.scalar(select(EmbeddingIndex).where(EmbeddingIndex.status == "failed"))
        assert failed.encoded_count == 2
        failed_id = failed.id
    resumed_client = Synthetic()
    result = build_index(
        index_db, index_settings, resume_id=failed_id, client=resumed_client, activate=True
    )
    assert len(resumed_client.docs) == 2 and result["encoded_count"] == 4
    assert index_status(index_db, index_settings)["status"] == "ready"


def test_live_builder_has_fenced_lease(index_db, index_settings):
    entered, proceed = threading.Event(), threading.Event()

    class Blocking(Synthetic):
        def encode_documents(self, texts, timeout):
            entered.set()
            assert proceed.wait(10)
            return super().encode_documents(texts, timeout)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(build_index, index_db, index_settings, client=Blocking())
        assert entered.wait(10)
        try:
            with index_db.sessions() as session:
                id_ = session.scalar(select(EmbeddingIndex.id))
            with pytest.raises(IndexUnavailable, match="build_busy"):
                build_index(index_db, index_settings, resume_id=id_, client=Synthetic())
        finally:
            proceed.set()
        assert future.result()["status"] == "ready"


def test_stale_catalog_during_build_is_not_published(index_db, index_settings):
    class Changing(Synthetic):
        def encode_documents(self, texts, timeout):
            change(index_db)
            return super().encode_documents(texts, timeout)

    with pytest.raises(IndexUnavailable, match="catalog_changed"):
        build_index(index_db, index_settings, client=Changing(), activate=True)
    assert index_status(index_db, index_settings)["status"] == "missing"


def test_two_ready_builds_need_compare_and_swap(index_db, index_settings):
    first = build_index(index_db, index_settings, client=Synthetic())
    second = build_index(index_db, index_settings, client=Synthetic())
    activate_index(index_db, index_settings, first["index_id"], 0)
    with pytest.raises(IndexUnavailable, match="activation_conflict"):
        activate_index(index_db, index_settings, second["index_id"], 0)
    # Explicit new generation allows selecting another ready snapshot of the same catalog/model.
    activate_index(index_db, index_settings, second["index_id"], 1)
    assert activate_index(index_db, index_settings, first["index_id"], 2)["generation"] == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("embedding_model", "other"),
        ("embedding_revision", "v2"),
        ("embedding_base_url", "http://localhost:9091/v1"),
    ],
)
def test_model_space_isolation(index_db, index_settings, catalog, field, value):
    build_index(index_db, index_settings, client=Synthetic(), activate=True)
    setattr(index_settings, field, value)
    assert index_status(index_db, index_settings)["status"] == "model_mismatch"
    with pytest.raises(IndexUnavailable, match="model_mismatch"):
        search_index(index_db, index_settings, catalog, "query", client=Synthetic())


def test_key_rotation_not_a_new_vector_space(index_settings):
    before = model_identity(index_settings)
    index_settings.embedding_api_key = "rotated-private-key"
    assert model_identity(index_settings) == before


@pytest.mark.parametrize("damage", ["missing", "checksum", "nan", "dimension", "text"])
def test_corrupt_vectors_fail_closed(index_db, index_settings, catalog, damage):
    index = build_index(index_db, index_settings, client=Synthetic(), activate=True)
    with index_db.sessions() as session, session.begin():
        row = session.scalar(
            select(EmbeddingVector).where(EmbeddingVector.index_id == index["index_id"])
        )
        if damage == "missing":
            session.delete(row)
        if damage == "checksum":
            row.vector_hash = "bad"
        if damage == "nan":
            row.vector = ["nan"]  # JSON-compatible invalid scalar; both SQL dialects.
        if damage == "dimension":
            row.vector = [1]
        if damage == "text":
            row.text_hash = "bad"
    client = Synthetic()
    with pytest.raises(EmbeddingUnavailable):
        search_index(index_db, index_settings, catalog, "query", client=client)
    assert not client.queries


def test_changed_query_dimension_rejected(index_db, index_settings, catalog):
    build_index(index_db, index_settings, client=Synthetic(), activate=True)
    client = Synthetic()
    client.encode = lambda *_: [[1, 0]]
    with pytest.raises(IndexUnavailable, match="dimension_mismatch"):
        search_index(index_db, index_settings, catalog, "query", client=client)


def test_more_than_128_entries_uses_only_query_embedding(index_db, index_settings, catalog):
    with index_db.sessions() as session, session.begin():
        for i in range(130):
            game = {
                **copy.deepcopy(catalog[0]),
                "id": f"bulk-{i}",
                "title": f"Synthetic {i}",
                "steam_app_id": 10000 + i,
            }
            session.add(GameRecord(id=game["id"], title=game["title"], payload=game))
    client = Synthetic()
    result = build_index(index_db, index_settings, client=client, activate=True)
    assert result["document_count"] == 134
    client.docs.clear()
    scores, _ = search_index(
        index_db, index_settings, current_catalog(index_db), "太空", client=client
    )
    assert len(scores) == 134 and not client.docs and len(client.queries) == 1


def test_workflow_actual_mode_and_fallback(index_db, index_settings, catalog):
    def resolver(catalog, query):
        return search_index(index_db, index_settings, catalog, query, client=Synthetic())

    graph = build_graph(index_settings, lambda *_: None, semantic_resolver=resolver)
    inputs = {"request": {"query": "中文太空", "country": "CN"}, "catalog": catalog, "feedback": []}
    output = graph.invoke(inputs)
    assert output["intent"]["retrieval"] == "bm25" and any(
        "已降级" in w for w in output["warnings"]
    )
    built = build_index(index_db, index_settings, client=Synthetic(), activate=True)
    output = graph.invoke(inputs)
    assert output["intent"]["retrieval"] == "indexed_hybrid_rrf"
    assert output["intent"]["index_id"] == built["index_id"]
    assert all(r["game"]["chinese_support"] == "verified" for r in output["results"])


def test_public_status_excludes_secrets_and_does_not_probe(index_db, index_settings):
    config = index_settings.model_copy(
        update={"database_url": index_db.engine.url.render_as_string(hide_password=False)}
    )
    with TestClient(create_app(config, catalog=[])) as client:
        response = client.get("/api/v1/retrieval/index")
        assert response.status_code == 200 and response.json()["status"] == "missing"
        assert response.json()["provider_probed"] is False
        assert "9090" not in response.text and "synthetic" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/capabilities").json()["semantic_catalog_limit"] == 5000


def test_delete_requires_non_active_snapshot(index_db, index_settings):
    active = build_index(index_db, index_settings, client=Synthetic(), activate=True)
    with pytest.raises(IndexUnavailable, match="active_index_protected"):
        delete_index(index_db, active["index_id"])
    extra = build_index(index_db, index_settings, client=Synthetic())
    assert delete_index(index_db, extra["index_id"])["deleted_index_id"] == extra["index_id"]
    with index_db.sessions() as session:
        assert not list(
            session.scalars(
                select(EmbeddingVector).where(EmbeddingVector.index_id == extra["index_id"])
            )
        )


def test_two_concurrent_activations_only_one_wins(index_db, index_settings):
    first = build_index(index_db, index_settings, client=Synthetic())
    second = build_index(index_db, index_settings, client=Synthetic())

    def switch(id_):
        try:
            return activate_index(index_db, index_settings, id_, 0)["active"]
        except IndexUnavailable as error:
            assert error.code == "activation_conflict"
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(switch, [first["index_id"], second["index_id"]]))
    assert sum(results) == 1


def test_full_api_run_persists_index_and_explicit_stale_fallback(
    index_db, index_settings, catalog, monkeypatch
):
    from uuid import uuid4

    vector_client = Synthetic()
    monkeypatch.setattr("playmatch.semantic_index.embedding_client", lambda *_: vector_client)
    built = build_index(index_db, index_settings, client=vector_client, activate=True)
    config = index_settings.model_copy(
        update={"database_url": index_db.engine.url.render_as_string(hide_password=False)}
    )
    with TestClient(create_app(config, catalog=catalog)) as client:
        for expected in ("indexed_hybrid_rrf", "bm25"):
            response = client.post(
                "/api/v1/runs",
                json={"query": "中文太空探索", "country": "CN"},
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert response.status_code == 202
            created = response.json()
            headers = {"X-Run-Token": created["access_token"]}
            stream = client.get(f"/api/v1/runs/{created['id']}/events", headers=headers)
            assert "event: done" in stream.text
            result = client.get(f"/api/v1/runs/{created['id']}", headers=headers).json()
            assert result["intent"]["retrieval"] == expected
            if expected == "indexed_hybrid_rrf":
                assert result["intent"]["index_id"] == built["index_id"]
                change(index_db)
            else:
                assert result["status"] == "partial"
                assert any("已降级" in warning for warning in result["warnings"])


def test_killed_process_build_can_be_resumed(tmp_path, catalog, index_settings):
    """Real subprocess death, then simulate lease expiry instead of waiting sixty seconds."""
    import subprocess
    import sys
    import time
    from pathlib import Path

    db = Database("sqlite:///" + str(tmp_path / "killed.db"))
    db.migrate()
    try:
        with db.sessions() as session, session.begin():
            for game in catalog:
                session.add(GameRecord(id=game["id"], title=game["title"], payload=game))
        config_path = tmp_path / "config.json"
        config_path.write_text(
            index_settings.model_copy(
                update={
                    "database_url": db.engine.url.render_as_string(hide_password=False),
                }
            ).model_dump_json()
        )
        marker = tmp_path / "partial.marker"
        program = """
import json, sys, time
from pathlib import Path
from playmatch.config import Settings
from playmatch.database import Database
from playmatch.semantic_index import build_index
class Client:
    calls = 0
    def encode_documents(self, texts, timeout):
        self.calls += 1
        if self.calls == 2:
            Path(sys.argv[2]).write_text("partial batch already committed")
            time.sleep(60)
        return [[1,2,3] for _ in texts]
settings = Settings(_env_file=None, **json.loads(Path(sys.argv[1]).read_text()))
build_index(Database(settings.database_url), settings, client=Client(), batch_size=2)
"""
        root = Path(__file__).resolve().parents[3]
        process = subprocess.Popen(
            [sys.executable, "-c", program, str(config_path), str(marker)],
            env={**os.environ, "PYTHONPATH": str(root / "services/api")},
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
            assert marker.exists()
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        with db.sessions() as session, session.begin():
            index = session.scalar(select(EmbeddingIndex))
            assert index.status == "building" and index.encoded_count == 2
            index.lease_until = 0  # Explicit simulated passage of lease time.
            id_ = index.id
        vector_client = Synthetic()
        resumed = build_index(
            db, index_settings, resume_id=id_, client=vector_client, activate=True
        )
        assert resumed["status"] == "ready" and len(vector_client.docs) == 2
    finally:
        db.close()
