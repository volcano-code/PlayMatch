"""Operator-only, atomic import of reviewed IGDB adapter records. No implicit network."""

import hashlib
import json
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update

from playmatch.models import CatalogImport, CatalogLock, CatalogSource, GameRecord
from playmatch.schemas import Game

MAX_FILE_BYTES = 2_000_000
MAX_CATALOG = 5000


class CatalogImportError(ValueError):
    pass


def source_url(value: str, *, image=False) -> str:
    if image and not value:
        return ""
    try:
        parsed = urlparse(value)
        hosts = {"images.igdb.com"} if image else {"igdb.com", "www.igdb.com", "api.igdb.com"}
        if (
            parsed.scheme != "https" or parsed.hostname not in hosts
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443) or "\\" in value or "%" in value
            or any(ord(char) < 33 for char in value)
        ):
            raise ValueError
    except ValueError as error:
        raise ValueError("Expected a safe HTTPS IGDB URL") from error
    return value


class ImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    steam_app_id: int = Field(gt=0, le=2147483647)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=6000)
    themes: list[str] = Field(default_factory=list, max_length=30)
    genres: list[str] = Field(default_factory=list, max_length=30)
    platforms: list[str] = Field(default_factory=list, max_length=30)
    image_url: str = Field(default="", max_length=500)
    source_url: str = Field(max_length=500)

    @field_validator("title", "description")
    @classmethod
    def text(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("Invalid text")
        return value

    @field_validator("themes", "genres", "platforms")
    @classmethod
    def tags(cls, values):
        if any(not value.strip() or len(value) > 80 or not value.isprintable() for value in values):
            raise ValueError("Invalid tags")
        return sorted(set(value.strip() for value in values))

    @field_validator("source_url")
    @classmethod
    def source(cls, value):
        return source_url(value)

    @field_validator("image_url")
    @classmethod
    def image(cls, value):
        return source_url(value, image=True)


class ImportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: int = Field(ge=1, le=1)
    source: str = Field(pattern=r"^igdb$")
    observed_at: str = Field(max_length=40)
    items: list[ImportItem] = Field(min_length=1, max_length=200)

    @field_validator("observed_at")
    @classmethod
    def timestamp(cls, value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed > datetime.now(UTC):
            raise ValueError("Observation must have a timezone and must not be future-dated")
        return parsed.astimezone(UTC).isoformat()


def digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def normalized(item: ImportItem, observed_at: str) -> dict:
    platforms = {
        "pc (microsoft windows)": "PC", "windows": "PC", "pc": "PC", "mac": "Mac",
        "linux": "Linux", "nintendo switch": "Switch", "switch": "Switch",
        "playstation 4": "PlayStation", "playstation 5": "PlayStation",
        "xbox one": "Xbox", "xbox series x|s": "Xbox",
    }
    return Game.model_validate({
        "id": f"steam-{item.steam_app_id}", "steam_app_id": item.steam_app_id,
        "title": item.title, "title_zh": item.title, "description": item.description,
        "themes": item.themes, "genres": item.genres,
        "platforms": sorted({platforms[p.casefold()] for p in item.platforms if p.casefold() in platforms}),
        "chinese_support": "unknown", "coop": "unknown", "horror": "unknown",
        "image_url": item.image_url,
        "store_url": f"https://store.steampowered.com/app/{item.steam_app_id}/",
        "evidence": [{"label": "IGDB 元数据（硬条件未核验）", "url": item.source_url,
                      "field": "metadata", "observed_at": observed_at}],
        "metadata_status": "provider", "estimated_hours": None,
    }).model_dump()


def import_catalog(database, raw: dict, key: str, *, dry_run: bool = True) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", key):
        raise CatalogImportError("Invalid import key")
    manifest = ImportManifest.model_validate(raw)
    unique: dict[str, ImportItem] = {}
    steam_ids: dict[int, str] = {}
    for item in manifest.items:
        if item.provider_id in unique and unique[item.provider_id] != item:
            raise CatalogImportError("Conflicting duplicate provider identity")
        if item.steam_app_id in steam_ids and steam_ids[item.steam_app_id] != item.provider_id:
            raise CatalogImportError("Conflicting Steam identity")
        unique[item.provider_id] = item
        steam_ids[item.steam_app_id] = item.provider_id
    canonical = manifest.model_dump()
    canonical["items"] = [unique[key].model_dump() for key in sorted(unique)]
    content_hash = digest(canonical)
    with database.sessions() as session, session.begin():
        # All importers and startup seeding take this same transaction lock.
        locked = session.execute(update(CatalogLock).where(CatalogLock.id == 1).values(
            revision=CatalogLock.revision,
        ))
        if locked.rowcount != 1:
            raise CatalogImportError("Catalog migration is required")
        existing = session.get(CatalogImport, key)
        if existing:
            if existing.content_hash != content_hash:
                raise CatalogImportError("Import key already belongs to another payload")
            return {**existing.report, "replayed": True, "dry_run": dry_run}
        games = list(session.scalars(select(GameRecord)))
        by_steam: dict[int, list[GameRecord]] = {}
        for game in games:
            by_steam.setdefault(game.payload["steam_app_id"], []).append(game)
        report = {
            "key": key, "content_hash": content_hash, "inserted": 0, "updated": 0,
            "preserved": 0, "unchanged": 0, "deduplicated": len(manifest.items) - len(unique),
            "replayed": False, "dry_run": dry_run,
        }
        for item in unique.values():
            candidates = by_steam.get(item.steam_app_id, [])
            if len(candidates) > 1:
                raise CatalogImportError("Ambiguous existing Steam identity")
            record = candidates[0] if candidates else None
            source = session.get(CatalogSource, item.provider_id)
            if source and (record is None or source.game_id != record.id):
                raise CatalogImportError("Provider identity cannot be remapped")
            item_hash = digest(item.model_dump())
            if source:
                previous = datetime.fromisoformat(source.observed_at)
                incoming = datetime.fromisoformat(manifest.observed_at)
                if incoming < previous or (incoming == previous and item_hash != source.content_hash):
                    raise CatalogImportError("Stale or conflicting observation")
            if record is None:
                payload = normalized(item, manifest.observed_at)
                if session.get(GameRecord, payload["id"]) is not None:
                    raise CatalogImportError("Game slug collision")
                record = GameRecord(id=payload["id"], title=item.title, payload=payload)
                session.add(record)
                session.flush()
                games.append(record)
                by_steam[item.steam_app_id] = [record]
                report["inserted"] += 1
            elif record.payload["metadata_status"] == "curated":
                report["preserved"] += 1
            else:
                payload = normalized(item, manifest.observed_at)
                payload["id"] = record.id
                report["unchanged" if payload == record.payload else "updated"] += 1
                record.title, record.payload = item.title, payload
            owner = session.scalar(select(CatalogSource).where(CatalogSource.game_id == record.id))
            if owner and owner.provider_id != item.provider_id:
                raise CatalogImportError("Game already has another provider identity")
            if source is None:
                source = CatalogSource(provider_id=item.provider_id, game_id=record.id,
                                       observed_at=manifest.observed_at, content_hash=item_hash)
                session.add(source)
            else:
                source.observed_at, source.content_hash = manifest.observed_at, item_hash
        if len(games) > MAX_CATALOG:
            raise CatalogImportError("Catalog capacity exceeded")
        if dry_run:
            session.rollback()
            return report
        session.execute(update(CatalogLock).where(CatalogLock.id == 1).values(
            revision=CatalogLock.revision + 1,
        ))
        session.add(CatalogImport(key=key, content_hash=content_hash,
                                  created_at=datetime.now(UTC).isoformat(), report=report))
        return report
