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
