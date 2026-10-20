"""Bounded BM25 and opt-in embedding retrieval. Scores are never probabilities."""

import hashlib
import json
import math
import re
import threading
import time
import unicodedata
from collections import Counter, OrderedDict
from functools import lru_cache

import httpx

STOPWORDS = {
    "游戏",
    "推荐",
    "一个",
    "给我",
    "想玩",
    "可以",
    "一些",
    "适合",
    "什么",
    "需要",
    "喜欢",
    "game",
    "games",
    "recommend",
    "want",
    "the",
    "a",
    "to",
    "play",
    "and",
    "with",
}
MAX_SEMANTIC_CATALOG = 128


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    terms = re.findall(r"[a-z0-9]+", text)
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.extend(chunk[i : i + 2] for i in range(max(1, len(chunk) - 1)))
    return [term for term in terms if term not in STOPWORDS]


def document(game: dict) -> str:
    return " ".join(
        [
            game["title"],
            game["title_zh"],
            *game["themes"],
            *game["genres"],
            game["description"],
        ]
    )[:6000]


def fingerprint(catalog: list[dict]) -> str:
    rows = sorted((game["id"], document(game)) for game in catalog)
    encoded = json.dumps(["retrieval-text-v1", rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        if not math.isfinite(k1) or k1 <= 0 or not math.isfinite(b) or not 0 <= b <= 1:
            raise ValueError("Invalid BM25 parameters")
        self.k1, self.b = k1, b
        self.documents = [Counter(tokens(text)) for text in documents]
        self.lengths = [sum(row.values()) for row in self.documents]
        self.average = sum(self.lengths) / len(documents) if documents else 0.0
        self.frequency = Counter(term for row in self.documents for term in row)

    def scores(self, query: str) -> list[float]:
        count = len(self.documents)
        result = []
        for row, length in zip(self.documents, self.lengths, strict=True):
            score = 0.0
            for term in set(tokens(query)):
                frequency = row[term]
                if not frequency:
                    continue
                df = self.frequency[term]
                idf = math.log(1 + (count - df + 0.5) / (df + 0.5))
                norm = 1 - self.b + self.b * length / (self.average or 1)
                score += idf * frequency * (self.k1 + 1) / (frequency + self.k1 * norm)
            result.append(score)
        return result


class EmbeddingUnavailable(ValueError):
    """A sanitized error; provider responses and credentials never enter warnings."""


def normalize_vector(value) -> list[float]:
    if not isinstance(value, list) or not 1 <= len(value) <= 2048:
        raise EmbeddingUnavailable("Invalid embedding")
    if any(type(item) not in (int, float) for item in value):
        raise EmbeddingUnavailable("Invalid embedding")
    try:
        norm = math.hypot(*value)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError
        result = [float(item) / norm for item in value]
        if not all(math.isfinite(item) for item in result):
            raise ValueError
        return result
    except (OverflowError, ValueError) as error:
        raise EmbeddingUnavailable("Invalid embedding") from error


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, key: str, *, transport=None):
        self.base_url, self.model, self.key = base_url, model, key
        self.transport = transport
        self.cache: OrderedDict[str, list[float]] = OrderedDict()
        self.lock = threading.Lock()

    def _request(self, texts: list[str], deadline: float) -> list[list[float]]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EmbeddingUnavailable("Embedding deadline exceeded")
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        try:
            with httpx.Client(
                timeout=min(remaining, 5),
                follow_redirects=False,
                transport=self.transport,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    self.base_url.rstrip("/") + "/embeddings",
                    headers=headers,
                    json={"model": self.model, "input": texts, "encoding_format": "float"},
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000 or time.monotonic() > deadline:
                            raise EmbeddingUnavailable("Embedding response limit exceeded")
                    payload = json.loads(body)
            rows = payload["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError
            ordered: dict[int, list[float]] = {}
            for row in rows:
                index = row["index"]
                if type(index) is not int or not 0 <= index < len(texts) or index in ordered:
                    raise ValueError
                ordered[index] = normalize_vector(row["embedding"])
            vectors = [ordered[i] for i in range(len(texts))]
            if len({len(vector) for vector in vectors}) != 1:
                raise ValueError
            return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError) as error:
            raise EmbeddingUnavailable("Embedding service unavailable or invalid") from error

    def encode_documents(self, texts: list[str], timeout: float) -> list[list[float]]:
        """Bounded offline batch. No implicit query or process-local document cache."""
        if not texts or len(texts) > 16 or any(not t.strip() or len(t) > 6000 for t in texts):
            raise EmbeddingUnavailable("Embedding input limit exceeded")
        return self._request(texts, time.monotonic() + timeout)

    def encode(self, texts: list[str], timeout: float) -> list[list[float]]:
        # Last text is the query: it is never retained in the document cache.
        if not texts or len(texts) > 129 or any(not t.strip() or len(t) > 6000 for t in texts):
            raise EmbeddingUnavailable("Embedding input limit exceeded")
        deadline = time.monotonic() + timeout
        if not self.lock.acquire(timeout=timeout):
            raise EmbeddingUnavailable("Embedding cache busy")
        try:
            docs, query = texts[:-1], texts[-1]
            keys = [hashlib.sha256(text.encode()).hexdigest() for text in docs]
            staged = dict(self.cache)
            missing = list(dict.fromkeys(key for key in keys if key not in staged))
            by_key = dict(zip(keys, docs, strict=True))
            for start in range(0, len(missing), 16):
                batch = missing[start : start + 16]
                vectors = self._request([by_key[key] for key in batch], deadline)
                staged.update(zip(batch, vectors, strict=True))
            query_vector = self._request([query], deadline)[0]
            result = [staged[key] for key in keys] + [query_vector]
            if len({len(vector) for vector in result}) != 1:
                self.cache.clear()
                raise EmbeddingUnavailable("Embedding dimensions changed")
            for key in keys:
                self.cache[key] = staged[key]
                self.cache.move_to_end(key)
            while len(self.cache) > 256:
                self.cache.popitem(last=False)
            return result
        finally:
            self.lock.release()


@lru_cache(maxsize=4)
def embedding_client(base_url: str, model: str, key: str) -> EmbeddingClient:
    return EmbeddingClient(base_url, model, key)


def search_catalog(
    catalog: list[dict],
    intent: dict,
    query: str,
    feedback: list[dict],
    settings,
    *,
    semantic_resolver=None,
):
    texts = [document(game) for game in catalog]
    lexical = BM25(texts).scores(query + " " + " ".join(intent.get("search_terms", [])))
    matched = [sorted(set(intent.get("themes", [])) & set(game["themes"])) for game in catalog]
    lexical = [score + 3 * len(themes) for score, themes in zip(lexical, matched, strict=True)]
    semantic = None
    index_metadata = {}
    warnings = []
    mode = "bm25"
    if settings.retrieval_mode == "hybrid" and catalog:
        try:
            if not settings.embedding_enabled or len(catalog) > MAX_SEMANTIC_CATALOG:
                raise EmbeddingUnavailable("Embedding disabled or catalog exceeds bound")
            vectors = embedding_client(
                settings.embedding_base_url,
                settings.embedding_model,
                settings.embedding_api_key,
            ).encode([*texts, query[:6000]], settings.embedding_timeout_seconds)
            semantic = [
                sum(a * b for a, b in zip(vector, vectors[-1], strict=True))
                for vector in vectors[:-1]
            ]
            mode = "hybrid_rrf"
        except EmbeddingUnavailable:
            warnings.append("语义检索未配置、超限或暂不可用，已降级为 BM25；没有伪造向量结果。")
    if settings.retrieval_mode == "indexed_hybrid" and catalog:
        try:
            if semantic_resolver is None:
                raise EmbeddingUnavailable("index_missing")
            semantic, index_metadata = semantic_resolver(catalog, query)
            if len(semantic) != len(catalog) or not all(math.isfinite(v) for v in semantic):
                raise EmbeddingUnavailable("invalid_index_scores")
            mode = "indexed_hybrid_rrf"
        except EmbeddingUnavailable:
            semantic, index_metadata = None, {}
            warnings.append(
                "持久向量索引缺失、过期、模型不匹配或服务暂不可用，已降级为 BM25；请检查索引状态。"
            )
    lexical_ids = sorted(
        [i for i, score in enumerate(lexical) if score > 0],
        key=lambda i: (-lexical[i], catalog[i]["id"]),
    )
    semantic_ids = (
        []
        if semantic is None
        else sorted(
            [i for i, score in enumerate(semantic) if score >= settings.semantic_min_score],
            key=lambda i: (-semantic[i], catalog[i]["id"]),
        )
    )
    fused = Counter()
    for ranking in (lexical_ids, semantic_ids):
        for rank, index in enumerate(ranking, 1):
            fused[index] += 100 / (60 + rank)
    liked = {row["game_id"] for row in feedback if row["kind"] == "like"}
    disliked = {row["game_id"] for row in feedback if row["kind"] == "dislike"}
    liked_themes = Counter(t for game in catalog if game["id"] in liked for t in game["themes"])
    filter_only = not intent.get("themes") and (
        any(
            intent.get(key)
            for key in (
                "chinese_required",
                "coop_required",
                "exclude_horror",
                "excluded_themes",
                "players",
                "single_player_required",
            )
        )
        or intent.get("platform", "PC") != "PC"
    )
    relevant = set(lexical_ids) | set(semantic_ids)
    rows = []
    for i, game in enumerate(catalog):
        if i not in relevant and not filter_only:
            continue
        affinity = min(2.0, sum(liked_themes[t] * 0.1 for t in game["themes"]))
        score = fused[i] if semantic is not None else lexical[i]
        score += (
            affinity + (0.3 if game["id"] in liked else 0) - (0.6 if game["id"] in disliked else 0)
        )
        rows.append(
            {
                "game": game,
                "score": round(score, 6),
                "matched_themes": matched[i],
                "query_score": lexical[i],
                "lexical_score": lexical[i],
                "semantic_score": None if semantic is None else semantic[i],
                "retrieval": mode,
            }
        )
    return (
        sorted(rows, key=lambda row: (-row["score"], row["game"]["id"])),
        {
            **index_metadata,
            "retrieval": mode,
            "catalog_fingerprint": fingerprint(catalog),
            "embedding_model": settings.embedding_model if semantic is not None else None,
        },
        warnings,
    )
