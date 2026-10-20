"""Bounded IGDB collection to a content-addressed review bundle, never directly to DB."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError

from playmatch.catalog_import import MAX_FILE_BYTES, ImportItem, ImportManifest, digest
from playmatch.providers.cheapshark import ProviderRequestError
from playmatch.providers.igdb import API_URL, IGDBClient, _parse_game

POLICY = "pc-base-game-steam-mapping-v1"
FIELDS = (
    "id,name,summary,url,genres.name,themes.name,platforms.name,cover.url,"
    "game_type.type,parent_game,version_parent,updated_at,"
    "external_games.uid,external_games.url,external_games.external_game_source.name"
)


def mapped_steam_id(raw: dict) -> int | None:
    """Ambiguous or contradictory Steam identities are rejected, not first-match wins."""
    identities = set()
    for external in (
        raw.get("external_games", []) if isinstance(raw.get("external_games"), list) else []
    ):
        if not isinstance(external, dict):
            continue
        source = external.get("external_game_source")
        named_steam = isinstance(source, dict) and str(source.get("name", "")).casefold() == "steam"
        url_id = None
        url = external.get("url")
        if isinstance(url, str) and url:
            try:
                parsed = urlparse(url)
                match = re.fullmatch(r"/app/([1-9][0-9]*)(?:/[^?#]*)?", parsed.path)
                if (
                    parsed.scheme == "https"
                    and parsed.hostname == "store.steampowered.com"
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment
                    and parsed.port in {None, 443}
                    and match
                    and not any(ord(char) < 33 for char in url)
                ):
                    url_id = int(match[1])
                elif named_steam:
                    return None
            except ValueError:
                if named_steam:
                    return None
        uid = external.get("uid")
        uid_id = int(uid) if isinstance(uid, str) and re.fullmatch(r"[1-9][0-9]*", uid) else None
        if named_steam:
            if uid_id is None or (url_id is not None and uid_id != url_id):
                return None
            identities.add(uid_id)
        elif url_id is not None:
            # If another named source claims a Steam URL, do not silently reclassify it.
            if isinstance(source, dict) and source.get("name"):
                return None
            identities.add(url_id)
    return next(iter(identities)) if len(identities) == 1 else None


def normalize_collected(raw: dict) -> tuple[dict | None, str | None]:
    kind = raw.get("game_type")
    allowed = {"main_game", "main game", "remake", "remaster", "port"}
    if (
        not isinstance(kind, dict)
        or str(kind.get("type", "")).casefold() not in allowed
        or raw.get("parent_game") is not None
        or raw.get("version_parent") is not None
    ):
        return None, "not_verified_base_game"
    parsed = _parse_game(raw)
    if not parsed:
        return None, "invalid_game"
    parsed["steam_app_id"] = mapped_steam_id(raw)
    if parsed["steam_app_id"] is None:
        return None, "missing_or_ambiguous_steam_mapping"
    if "PC (Microsoft Windows)" not in parsed["platforms"]:
        return None, "pc_platform_missing"
    try:
        return ImportItem.model_validate(parsed).model_dump(), None
    except ValidationError:
        return None, "invalid_metadata"


async def collect_catalog(
    client: IGDBClient,
    *,
    after_id: int = 0,
    pages: int = 1,
    page_size: int = 50,
    timeout_seconds: float = 90,
) -> dict:
    if type(after_id) is not int or not 0 <= after_id <= 2**31 - 1:
        raise ValueError("Invalid cursor")
    if (
        type(pages) is not int
        or not 1 <= pages <= 4
        or type(page_size) is not int
        or not 1 <= page_size <= 50
    ):
        raise ValueError("Collection is bounded to four pages of 50 records")
    if not 1 <= timeout_seconds <= 180:
        raise ValueError("Invalid collection timeout")
    if not client.configured:
        raise ProviderRequestError("not_configured")
    cursor, scanned, fetched = after_id, 0, 0
    candidates, rejected, receipts = [], [], []
    exhausted = False
    async with asyncio.timeout(timeout_seconds):
        for _ in range(pages):
            body = (
                f"fields {FIELDS}; where id > {cursor} & platforms = (6) & "
                f"version_parent = null; sort id asc; limit {page_size};"
            )
            rows = await client.catalog_page(body)
            if len(rows) > page_size:
                raise ProviderRequestError("invalid_page")
            ids = [row.get("id") for row in rows]
            if any(type(id_) is not int or not cursor < id_ <= 2**31 - 1 for id_ in ids):
                raise ProviderRequestError("invalid_cursor")
            if ids != sorted(set(ids)):
                raise ProviderRequestError("non_monotonic_page")
            receipts.append(
                {"after_id": cursor, "count": len(rows), "response_sha256": digest(rows)}
            )
            fetched += 1
            for raw in rows:
                scanned += 1
                item, reason = normalize_collected(raw)
                if item:
                    candidates.append(item)
                else:
                    rejected.append({"provider_id": str(raw["id"]), "reason": reason})
            if rows:
                cursor = ids[-1]
            if len(rows) < page_size:
                exhausted = True
                break
    # One Steam app mapped to two works is ambiguous; reject both, not just the second.
    steam_counts = {}
    for item in candidates:
        steam_counts[item["steam_app_id"]] = steam_counts.get(item["steam_app_id"], 0) + 1
    accepted = []
    for item in candidates:
        if steam_counts[item["steam_app_id"]] > 1:
            rejected.append(
                {"provider_id": item["provider_id"], "reason": "cross_record_mapping_conflict"}
            )
        else:
            accepted.append(item)
    observed_at = datetime.now(UTC).isoformat()
    manifest = {
        "schema_version": 1,
        "source": "igdb",
        "observed_at": observed_at,
        "items": accepted,
    }
    if accepted:
        manifest = ImportManifest.model_validate(manifest).model_dump()
    review = {
        "schema_version": 1,
        "kind": "playmatch_catalog_review",
        "policy": POLICY,
        "source_url": API_URL,
        "observed_at": observed_at,
        "manifest": manifest,
        "start_after_id": after_id,
        "next_after_id": cursor,
        "source_exhausted": exhausted,
        "pages_fetched": fetched,
        "scanned": scanned,
        "accepted": len(accepted),
        "rejected": rejected,
        "page_receipts": receipts,
    }
    return {**review, "review_sha256": digest(review)}


def unwrap_review(
    payload: dict, *, approval: str | None = None, apply: bool = False
) -> tuple[dict, str | None]:
    if not isinstance(payload, dict):
        raise ValueError("Expected a catalog object")
    if payload.get("kind") != "playmatch_catalog_review":
        if approval:
            raise ValueError("A review hash requires a review bundle")
        return payload, None
    review = dict(payload)
    supplied = review.pop("review_sha256", None)
    calculated = digest(review)
    if (
        supplied != calculated
        or review.get("schema_version") != 1
        or review.get("policy") != POLICY
    ):
        raise ValueError("Review bundle modified or unsupported")
    if apply and approval != calculated:
        raise ValueError("Explicit approval must match the reviewed content hash")
    if approval and approval != calculated:
        raise ValueError("Approval does not match")
    return ImportManifest.model_validate(review["manifest"]).model_dump(), calculated


def write_review(path: Path, payload: dict) -> None:
    """Publish one complete JSON file atomically, without overwriting existing reviews."""
    body = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    if len(body) > MAX_FILE_BYTES:
        raise ValueError("Review exceeds import byte limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".review-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # Atomic no-clobber publication on the same filesystem.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
