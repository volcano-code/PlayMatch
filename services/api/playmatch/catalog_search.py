"""Database-side metadata filtering; no per-request full payload materialization."""

from sqlalchemy import event, func, select

from playmatch.models import GameRecord


def search_fields(payload: dict) -> tuple[str, str]:
    text = " ".join(
        [
            payload.get("title", ""),
            payload.get("title_zh", ""),
            *payload.get("aliases", []),
            payload.get("description", ""),
            *payload.get("themes", []),
            *payload.get("genres", []),
        ]
    ).casefold()
    themes = "\x1f" + "\x1f".join(value.casefold() for value in payload.get("themes", [])) + "\x1f"
    return text, themes


@event.listens_for(GameRecord, "before_insert")
@event.listens_for(GameRecord, "before_update")
def update_search_fields(_mapper, _connection, record):
    record.search_text, record.search_themes = search_fields(record.payload)


def metadata_page(session, *, q="", theme="", limit=24, offset=0):
    predicates = []
    if q.strip():
        predicates.append(GameRecord.search_text.contains(q.strip().casefold(), autoescape=True))
    if theme.strip():
        predicates.append(
            GameRecord.search_themes.contains(
                "\x1f" + theme.strip().casefold() + "\x1f", autoescape=True
            )
        )
    total = session.scalar(select(func.count()).select_from(GameRecord).where(*predicates))
    items = session.scalars(
        select(GameRecord.payload)
        .where(*predicates)
        .order_by(GameRecord.title, GameRecord.id)
        .offset(offset)
        .limit(limit)
    )
    return {"items": list(items), "total": total}


@event.listens_for(GameRecord, "after_insert")
@event.listens_for(GameRecord, "after_update")
def maintain_postings(_mapper, connection, record):
    from sqlalchemy import delete, insert, update

    from playmatch.models import GameSearchTerm, SearchRevision
    from playmatch.search_terms import postings

    connection.execute(delete(GameSearchTerm).where(GameSearchTerm.game_id == record.id))
    values = [
        {"game_id": record.id, "term": term, "weight": weight}
        for term, weight in postings(record.payload).items()
    ]
    if values:
        connection.execute(insert(GameSearchTerm), values)
    connection.execute(
        update(SearchRevision)
        .where(SearchRevision.id == 1)
        .values(revision=SearchRevision.revision + 1)
    )


@event.listens_for(GameRecord, "after_delete")
def mark_removed(_mapper, connection, _record):
    from sqlalchemy import update

    from playmatch.models import SearchRevision

    connection.execute(
        update(SearchRevision)
        .where(SearchRevision.id == 1)
        .values(revision=SearchRevision.revision + 1)
    )


def coverage_sql(session) -> dict:
    """Aggregate field coverage in the database rather than loading every JSON document."""
    from sqlalchemy import case

    evidence = (
        func.json_array_elements(GameRecord.payload["evidence"]).table_valued("value").alias("ev")
        if session.bind.dialect.name == "postgresql"
        else func.json_each(GameRecord.payload, "$.evidence").table_valued("value").alias("ev")
    )

    def observed(field):
        if session.bind.dialect.name == "postgresql":
            key, date = (
                evidence.c.value.op("->>")("field"),
                evidence.c.value.op("->>")("observed_at"),
            )
        else:
            key, date = (
                func.json_extract(evidence.c.value, "$.field"),
                func.json_extract(evidence.c.value, "$.observed_at"),
            )
        return (
            select(1)
            .select_from(evidence)
            .where(key == field, date.is_not(None), date != "")
            .correlate(GameRecord)
            .exists()
        )

    def total(expression):
        return func.coalesce(func.sum(case((expression, 1), else_=0)), 0)

    length = func.json_array_length
    columns = {
        "total_games": func.count(GameRecord.id),
        "source_linked_games": total(length(GameRecord.payload["evidence"]) > 0),
        "description_observed": total(observed("description")),
        "chinese_interface_verified": total(
            (GameRecord.payload["chinese_support"].as_string() == "verified")
            & observed("chinese_support")
        ),
        "online_coop_verified": total(
            (GameRecord.payload["coop"].as_string() == "online") & observed("coop")
        ),
        "local_coop_verified": total(
            (GameRecord.payload["coop"].as_string() == "local") & observed("coop")
        ),
        "horror_unknown": total(GameRecord.payload["horror"].as_string() == "unknown"),
        "with_aliases": total(length(GameRecord.payload["aliases"]) > 0),
    }
    counts = dict(
        session.execute(select(*[v.label(k) for k, v in columns.items()]).select_from(GameRecord))
        .mappings()
        .one()
    )
    return {
        **counts,
        "automatic_sync": False,
        "notes": [
            "覆盖统计不等于全部属性核验，也不代表最低购买价。",
            "简介与别名包含开发助手整理内容，不是独立玩家评测。",
            "自动补全的运行状态与有效资料数量请查看资料补全面板；本统计包含历史资料。",
        ],
    }
