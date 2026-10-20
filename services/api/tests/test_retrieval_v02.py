import math
from unittest.mock import patch

import httpx
import pytest
from playmatch.config import Settings
from playmatch.retrieval import (
    BM25,
    EmbeddingClient,
    EmbeddingUnavailable,
    fingerprint,
    normalize_vector,
    search_catalog,
    tokens,
)
from playmatch.workflow import build_graph, filter_candidates, rule_intent


def settings(**values):
    return Settings(_env_file=None, llm_api_key="", llm_model="", **values)


def intent(query="太空探索"):
    return rule_intent({"query": query, "country": "CN", "budget_minor": None})


def test_bm25_math_and_empty():
    model = BM25(["cat cat", "dog"])
    expected = math.log(2) * 2 * 2.5 / (2 + 1.5 * (0.25 + 0.75 * 2 / 1.5))
    assert model.scores("cat")[0] == pytest.approx(expected)
    assert model.scores("cat")[1] == 0
    assert BM25([]).scores("anything") == []
    assert BM25([""]).scores("") == [0]
    assert "farm" in tokens("ＦＡＲＭ 太空")
    assert "太空" in tokens("ＦＡＲＭ 太空")


@pytest.mark.parametrize("k,b", [(0, 0.5), (-1, 0.5), (math.nan, 0.5), (1, -0.1), (1, 1.1)])
def test_bad_parameters(k, b):
    with pytest.raises(ValueError):
        BM25([], k, b)


def test_fingerprint_and_no_profile_only_relevance(catalog):
    assert fingerprint(catalog) == fingerprint(list(reversed(catalog)))
    changed = [{**g, "description": "different"} for g in catalog]
    assert fingerprint(changed) != fingerprint(catalog)
    rows, meta, warnings = search_catalog(
        catalog,
        intent("zzzzqqqq"),
        "zzzzqqqq",
        [
            {"game_id": "farm", "kind": "like"},
        ],
        settings(retrieval_mode="bm25"),
    )
    assert not rows and not warnings and meta["retrieval"] == "bm25"


def test_stable_ties_and_constraints(catalog):
    request = intent("推荐中文游戏")
    rows, _, _ = search_catalog(catalog, request, "推荐中文游戏", [], settings())
    accepted, _ = filter_candidates(rows, request, [])
    assert accepted and all(r["game"]["chinese_support"] == "verified" for r in accepted)
    forward, _, _ = search_catalog(catalog, intent(), "太空探索", [], settings())
    backward, _, _ = search_catalog(list(reversed(catalog)), intent(), "太空探索", [], settings())
    assert [r["game"]["id"] for r in forward] == [r["game"]["id"] for r in backward]


@pytest.mark.parametrize(
    "value", [[], [0, 0], [True, 1], ["1", 2], [math.nan], [math.inf], [1] * 2049, None]
)
def test_invalid_vectors(value):
    with pytest.raises(EmbeddingUnavailable):
        normalize_vector(value)


def test_large_finite_vector():
    vector = normalize_vector([1e300, 1e300])
    assert sum(v * v for v in vector) == pytest.approx(1)


def test_embeddings_reorder_cache_and_query_privacy():
    calls = []

    def handler(request):
        import json

        payload = json.loads(request.content)
        calls.append(payload["input"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [i + 1, 1]}
                    for i in reversed(range(len(payload["input"])))
                ]
            },
        )

    client = EmbeddingClient(
        "https://embedding.invalid/v1", "test", "secret", transport=httpx.MockTransport(handler)
    )
    first = client.encode(["doc-one", "doc-two", "query-private"], 5)
    assert len(first) == 3
    assert first[0] == pytest.approx(normalize_vector([1, 1]))
    client.encode(["doc-one", "doc-two", "query-private"], 5)
    assert calls == [["doc-one", "doc-two"], ["query-private"], ["query-private"]]
    assert len(client.cache) == 2
    client.encode(["new-document", "query-private"], 5)
    assert calls[-2] == ["new-document"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": []},
        {"data": [{"index": True, "embedding": [1, 0]}]},
        {"data": [{"index": -1, "embedding": [1, 0]}]},
        {"data": [{"index": 1, "embedding": [1, 0]}]},
        {"data": [{"index": 0, "embedding": [0, 0]}]},
    ],
)
def test_embedding_contract_failure(payload):
    client = EmbeddingClient(
        "https://embedding.invalid",
        "test",
        "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with pytest.raises(EmbeddingUnavailable):
        client.encode(["query"], 5)
    assert not client.cache


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_http_failure_is_not_retried_or_leaked(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://other.invalid"}, text="secret")

    client = EmbeddingClient(
        "https://embedding.invalid", "test", "secret", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(EmbeddingUnavailable) as error:
        client.encode(["query"], 5)
    assert "secret" not in str(error.value) and len(calls) == 1


def test_dimension_change_clears_cache():
    def handler(request):
        import json

        texts = json.loads(request.content)["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1, 0] if text == "doc" else [1, 0, 0]}
                    for i, text in enumerate(texts)
                ]
            },
        )

    client = EmbeddingClient(
        "https://embedding.invalid", "test", "secret", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(EmbeddingUnavailable):
        client.encode(["doc", "query"], 5)
    assert not client.cache


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.invalid",
        "https://user:pass@host",
        "https://host?q=secret",
        "https://host/#secret",
        "nonsense",
    ],
)
def test_unsafe_configuration(url):
    assert not settings(
        embedding_base_url=url, embedding_model="test", embedding_api_key="x"
    ).embedding_enabled


def test_hybrid_and_explicit_fallback(catalog):
    config = settings(
        retrieval_mode="hybrid",
        embedding_base_url="http://127.0.0.1:9090/v1",
        embedding_model="test",
    )

    class Synthetic:
        def encode(self, texts, timeout):
            return [[1, 0] for _ in texts]

    with patch("playmatch.retrieval.embedding_client", return_value=Synthetic()):
        rows, meta, warnings = search_catalog(catalog, intent(), "太空探索", [], config)
    assert rows and meta["retrieval"] == "hybrid_rrf" and not warnings
    assert all(row["semantic_score"] == 1 for row in rows)
    with patch("playmatch.retrieval.embedding_client", side_effect=EmbeddingUnavailable("safe")):
        rows, meta, warnings = search_catalog(catalog, intent(), "太空探索", [], config)
    assert meta["retrieval"] == "bm25" and "已降级" in warnings[0]
    assert all(row["semantic_score"] is None for row in rows)


def test_actual_mode_is_persistable_and_hard_filters_survive(catalog):
    updates = []
    config = settings(retrieval_mode="hybrid", embedding_base_url="", embedding_model="")
    output = build_graph(config, lambda _, __, update: updates.append(update)).invoke(
        {
            "request": {"query": "中文太空探索", "country": "CN"},
            "catalog": catalog,
            "feedback": [],
        }
    )
    assert output["intent"]["retrieval"] == "bm25"
    assert output["intent"]["catalog_fingerprint"] == fingerprint(catalog)
    assert any("已降级" in message for message in output["warnings"])
    assert any(update.get("intent", {}).get("retrieval") == "bm25" for update in updates)
    assert all(row["game"]["chinese_support"] == "verified" for row in output["results"])


@pytest.mark.parametrize(
    "query,platform",
    [("machines in space", "PC"), ("mac game", "Mac"), ("mac中文", "Mac"), ("bioshock", "PC")],
)
def test_platform_word_boundaries(query, platform):
    assert intent(query)["platform"] == platform
