"""Synthetic metadata/postings scale smoke, never a live catalog or quality evaluation."""

import argparse
import json
import os
import platform
import tempfile
import time
from pathlib import Path

from playmatch.candidate_search import catalog_context, shortlist
from playmatch.database import Database
from playmatch.models import GameRecord, GameSearchTerm
from playmatch.workflow import rule_intent
from sqlalchemy import func, select


def benchmark(url, rows):
    database = Database(url)
    try:
        database.migrate()
        with database.sessions() as session:
            if session.scalar(select(func.count()).select_from(GameRecord)):
                raise ValueError("Use a fresh empty disposable database")
        start = time.perf_counter()
        for offset in range(0, rows, 200):
            with database.sessions() as session, session.begin():
                for i in range(offset, min(rows, offset + 200)):
                    appid = 1000000000 + i
                    payload = {
                        "id": f"steam-{appid}",
                        "steam_app_id": appid,
                        "title": f"Synthetic Fixture {i}",
                        "title_zh": f"合成容量记录{i}",
                        "description": "Synthetic generated science fiction exploration puzzle metadata. Not a real game. "
                        + ("rarequantumzebra" if i == rows - 1 else "ordinary"),
                        "aliases": [],
                        "themes": ["科幻", "探索"],
                        "genres": ["Puzzle"],
                        "platforms": ["PC"],
                        "chinese_support": "unknown",
                        "coop": "unknown",
                        "horror": "unknown",
                        "image_url": "",
                        "store_url": "https://example.invalid/synthetic",
                        "evidence": [
                            {
                                "field": "description",
                                "label": "Synthetic fixture",
                                "url": "https://example.invalid",
                                "observed_at": None,
                            }
                        ],
                        "metadata_status": "provider",
                        "estimated_hours": None,
                    }
                    session.add(
                        GameRecord(id=payload["id"], title=payload["title"], payload=payload)
                    )
        insert_seconds = time.perf_counter() - start
        database.close()
        database = Database(url)
        start = time.perf_counter()
        counts = []
        with database.sessions() as session:
            initial, context = catalog_context(session, 256)
            assert len(initial) == 0 and context["eligible_count"] == rows
            for query in ("rarequantumzebra", "科幻", "不存在的zzqqvxx"):
                payloads, scope = shortlist(
                    session, rule_intent({"query": query, "country": "CN"}), query, [], 256
                )
                assert len(payloads) <= 256
                if query == "rarequantumzebra":
                    assert (
                        len(payloads) == 1 and payloads[0]["steam_app_id"] == 1000000000 + rows - 1
                    )
                counts.append(
                    {
                        "query_kind": query,
                        "payloads": len(payloads),
                        "truncated": scope["candidate_truncated"],
                    }
                )
            postings_count = session.scalar(select(func.count()).select_from(GameSearchTerm))
        return {
            "dataset": "synthetic_metadata_not_real_games",
            "network_requests": 0,
            "metadata_records": rows,
            "posting_records": postings_count,
            "restart_read": True,
            "storage": database.engine.dialect.name,
            "python": platform.python_version(),
            "insert_seconds": round(insert_seconds, 3),
            "three_queries_seconds": round(time.perf_counter() - start, 3),
            "payload_cap": 256,
            "queries": counts,
            "claim": "single scale smoke; not production P95, parallel throughput, retrieval recall or live coverage",
        }
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1000 <= args.rows <= 100000:
        parser.error("rows must be 1000..100000")
    url = os.getenv("METADATA_BENCH_DATABASE_URL")
    if url and os.getenv("PLAYMATCH_ALLOW_DISPOSABLE_DB") != "yes":
        parser.error("Dedicated disposable database permission is required")
    with tempfile.TemporaryDirectory(prefix="playmatch-metadata-bench-") as path:
        result = benchmark(url or "sqlite:///" + str(Path(path) / "synthetic.db"), args.rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
