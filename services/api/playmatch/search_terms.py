"""Versioned lexical postings. Deterministic Chinese bigrams and Latin words, not embeddings."""

import re
import unicodedata
from collections import Counter

VERSION = "postings-v1"
MAX_TERMS = 512
STOP = {
    "the",
    "and",
    "with",
    "for",
    "game",
    "games",
    "play",
    "want",
    "recommend",
    "游戏",
    "推荐",
    "想玩",
    "一个",
    "可以",
    "不要",
    "喜欢",
}


def terms(text: str) -> list[str]:
    value = unicodedata.normalize("NFKC", text).casefold()
    found = re.findall(r"[a-z0-9][a-z0-9_'-]{0,79}", value)
    for chunk in re.findall(r"[\u4e00-\u9fff]+", value):
        found.extend(chunk[i : i + 2] for i in range(max(1, len(chunk) - 1)))
    return [word for word in found if word not in STOP]


def postings(payload: dict) -> dict[str, int]:
    # Names/taxonomy have priority within a bounded document index.
    names = " ".join(
        [payload.get("title", ""), payload.get("title_zh", ""), *payload.get("aliases", [])]
    )
    taxonomy = " ".join([*payload.get("themes", []), *payload.get("genres", [])])
    weights = Counter({term: 8 for term in terms(names)})
    for term in terms(taxonomy):
        weights[term] += 4
    weights.update(terms(payload.get("description", "")[:6000]))
    return {
        term: min(weight, 20)
        for term, weight in sorted(weights.items(), key=lambda r: (-r[1], r[0]))[:MAX_TERMS]
    }
