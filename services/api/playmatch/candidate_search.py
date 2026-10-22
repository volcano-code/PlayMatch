"""Bounded payload retrieval using a persistent SQL inverted index.

This is lexical candidate generation + the existing reranker, not global ANN.
The cap limits payload materialization; SQL may still aggregate many posting hits.
"""

from sqlalchemy import func, or_, select

from playmatch.models import GameRecord, GameSearchTerm, SearchRevision
from playmatch.run_queue import database_now
from playmatch.search_terms import VERSION, terms


class CatalogChanged(RuntimeError):
    pass


def eligibility(now):
    return [
        GameRecord.recommendation_blocked.is_(False),
        or_(GameRecord.metadata_valid_until.is_(None), GameRecord.metadata_valid_until > now),
    ]


def catalog_context(session, limit: int):
    now = database_now(session)
    predicates = eligibility(now)
    count = session.scalar(select(func.count()).select_from(GameRecord).where(*predicates))
    revision = session.get(SearchRevision, 1).revision
    rows = (
        list(
            session.scalars(
                select(GameRecord.payload).where(*predicates).order_by(GameRecord.id).limit(limit)
            )
        )
        if count <= limit
        else []
    )
    return rows, {
        "revision": revision,
        "eligible_count": count,
        "candidate_limit": limit,
        "index_version": VERSION,
    }


def shortlist(session, intent, query, feedback, limit, expected_revision=None):
    revision = session.get(SearchRevision, 1).revision
    if expected_revision is not None and revision != expected_revision:
        raise CatalogChanged("Catalog changed before retrieval")
    predicates = eligibility(database_now(session))
    count = session.scalar(select(func.count()).select_from(GameRecord).where(*predicates))
    if count <= limit:
        rows = list(
            session.scalars(
                select(GameRecord.payload).where(*predicates).order_by(GameRecord.id).limit(limit)
            )
        )
        return rows, {
            "candidate_source": "complete_small_catalog",
            "eligible_corpus_size": count,
            "candidate_count": len(rows),
            "candidate_truncated": False,
        }
    excluded = [
        r["game_id"]
        for r in feedback
        if r["kind"] == "exclude" or (r["kind"] == "owned" and intent.get("exclude_owned", True))
    ]
    if excluded:
        predicates.append(GameRecord.id.not_in(excluded))
    platform = intent.get("platform", "PC")
    predicates.append(
        GameRecord.payload["platforms"].as_string().contains('"' + platform + '"', autoescape=True)
    )
    for key, value in (
        ("chinese_support", "verified" if intent.get("chinese_required") else None),
        ("coop", intent.get("coop_mode", "online") if intent.get("coop_required") else None),
        ("horror", "no" if intent.get("exclude_horror") else None),
    ):
        if value:
            predicates.append(GameRecord.payload[key].as_string() == value)
    for theme in intent.get("excluded_themes", []):
        predicates.append(
            ~GameRecord.search_themes.contains("\x1f" + theme.casefold() + "\x1f", autoescape=True)
        )
    query_terms = sorted(
        set(
            terms(
                " ".join([query[:2000], *intent.get("themes", []), *intent.get("search_terms", [])])
            )
        )
    )[:64]
    metadata = {
        "candidate_source": "sql_postings",
        "eligible_corpus_size": count,
        "candidate_limit": limit,
        "query_terms_used": len(query_terms),
        "index_version": VERSION,
    }
    if intent.get("players") and intent["players"] > 1:
        return [], {**metadata, "candidate_count": 0, "candidate_truncated": False}
    filter_only = not intent.get("themes") and (
        any(
            intent.get(k)
            for k in (
                "chinese_required",
                "coop_required",
                "exclude_horror",
                "excluded_themes",
                "single_player_required",
            )
        )
        or platform != "PC"
    )
    if filter_only:
        ids = list(
            session.scalars(
                select(GameRecord.id).where(*predicates).order_by(GameRecord.id).limit(limit + 1)
            )
        )
    elif query_terms:
        ids = list(
            session.scalars(
                select(GameSearchTerm.game_id)
                .join(GameRecord, GameRecord.id == GameSearchTerm.game_id)
                .where(GameSearchTerm.term.in_(query_terms), *predicates)
                .group_by(GameSearchTerm.game_id)
                .order_by(func.sum(GameSearchTerm.weight).desc(), GameSearchTerm.game_id)
                .limit(limit + 1)
            )
        )
    else:
        ids = []
    chosen = ids[:limit]
    rows = (
        {
            g["id"]: g
            for g in session.scalars(select(GameRecord.payload).where(GameRecord.id.in_(chosen)))
        }
        if chosen
        else {}
    )
    # Detect a write observed under READ COMMITTED during candidate selection.
    actual_revision = session.scalar(select(SearchRevision.revision).where(SearchRevision.id == 1))
    if actual_revision != revision:
        raise CatalogChanged("Catalog changed during retrieval")
    return [rows[i] for i in chosen if i in rows], {
        **metadata,
        "candidate_count": len(rows),
        "candidate_truncated": len(ids) > limit,
    }
