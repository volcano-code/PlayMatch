#!/usr/bin/env python3
"""Opt-in real CPU-model probe; isolated model interpreter, loopback HTTP, temporary DB.

This is an integration/development evaluation, not an independent recommendation benchmark.
The model environment is deliberately separate from the locked application environment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
MODEL_ALIAS = "playmatch-minilm-pinned-v1"


def serve(ready_file: Path) -> None:
    # Optional dependencies exist only in --model-python, never implicitly installed by the app.
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(2)
    model = SentenceTransformer(
        MODEL_ID,
        revision=MODEL_REVISION,
        device="cpu",
        trust_remote_code=False,
        model_kwargs={"use_safetensors": True},
    )
    model_lock = threading.Lock()
    counts = {"requests": 0, "texts": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, code, payload):
            body = json.dumps(payload, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/stats":
                return self.send_json(404, {"detail": "not_found"})
            with model_lock:
                return self.send_json(200, dict(counts))

        def do_POST(self):
            if self.path != "/v1/embeddings":
                return self.send_json(404, {"detail": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 400_000:
                    return self.send_json(413, {"detail": "invalid_size"})
                payload = json.loads(self.rfile.read(length))
                texts = payload["input"]
                if (
                    payload.get("model") != MODEL_ALIAS
                    or not isinstance(texts, list)
                    or not 1 <= len(texts) <= 16
                    or any(
                        not isinstance(text, str) or not text.strip() or len(text) > 6000
                        for text in texts
                    )
                ):
                    return self.send_json(400, {"detail": "invalid_input"})
                with model_lock:
                    vectors = model.encode(
                        texts, normalize_embeddings=True, batch_size=16, show_progress_bar=False
                    ).tolist()
                    counts["requests"] += 1
                    counts["texts"] += len(texts)
                return self.send_json(
                    200,
                    {
                        "model": MODEL_ALIAS,
                        "data": [
                            {"index": i, "embedding": vector} for i, vector in enumerate(vectors)
                        ],
                    },
                )
            except (ValueError, KeyError, TypeError):
                return self.send_json(400, {"detail": "invalid_input"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ready_file.write_text(
        json.dumps(
            {
                "port": server.server_port,
                "model_revision": MODEL_REVISION,
                "max_seq_length": model.max_seq_length,
            }
        )
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def probe(model_python: str) -> dict:
    sys.path.insert(0, str(ROOT / "services/api"))
    sys.path.insert(0, str(ROOT / "scripts"))
    import httpx
    from evaluate_recommendations import assess
    from playmatch.catalog_seed import load_catalog
    from playmatch.config import Settings
    from playmatch.database import Database
    from playmatch.models import GameRecord
    from playmatch.retrieval import document, fingerprint
    from playmatch.semantic_index import build_index, search_index
    from playmatch.workflow import build_graph

    with tempfile.TemporaryDirectory(prefix="playmatch-real-model-") as directory:
        temp = Path(directory)
        ready = temp / "ready.json"
        model_log = (temp / "model.log").open("w+")
        env = {
            **os.environ,
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_XET": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
        # No API keys, private data or production DB are passed to the model subprocess.
        for key in list(env):
            if any(term in key.upper() for term in ("TOKEN", "SECRET", "API_KEY", "DATABASE_URL")):
                env.pop(key)
        process = subprocess.Popen(
            [model_python, str(Path(__file__).resolve()), "--serve", str(ready)],
            stdout=model_log,
            stderr=model_log,
            env=env,
        )
        db = None
        try:
            deadline = time.monotonic() + 360
            while not ready.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        "Pinned model download/startup failed; inspect the probe environment"
                    )
                time.sleep(0.5)
            info = json.loads(ready.read_text())
            base = f"http://127.0.0.1:{info['port']}"
            settings = Settings(
                _env_file=None,
                retrieval_mode="indexed_hybrid",
                embedding_base_url=base + "/v1",
                embedding_model=MODEL_ALIAS,
                embedding_revision=MODEL_REVISION,
                embedding_api_key="",
                embedding_timeout_seconds=20,
                llm_api_key="",
                llm_model="",
                database_url="sqlite:///" + str(temp / "index.db"),
            )
            catalog = load_catalog()
            db = Database(settings.database_url)
            db.migrate()
            with db.sessions() as session, session.begin():
                for game in catalog:
                    session.add(GameRecord(id=game["id"], title=game["title"], payload=game))
            started = time.perf_counter()
            first = build_index(db, settings, activate=True)
            elapsed = time.perf_counter() - started
            assert first["dimensions"] == 384 and first["encoded_count"] == len(catalog)
            with httpx.Client(trust_env=False) as http:
                before = http.get(base + "/stats").json()
                second = build_index(db, settings, activate=True)
                after = http.get(base + "/stats").json()
            assert second["reused_count"] == len(catalog) and second["encoded_count"] == 0
            assert before == after, "An unchanged rebuild must not send document requests"
            db.close()
            db = Database(settings.database_url)  # A new engine must read the durable snapshot.
            scores, meta = search_index(db, settings, catalog, document(catalog[0]))
            assert abs(scores[0] - 1) < 1e-5 and meta["index_id"] == second["index_id"]
            dataset = json.loads((ROOT / "evals/rules-dev-v1.json").read_text())
            evaluated = []
            for mode in ("keyword_baseline", "bm25", "indexed_hybrid"):
                config = settings.model_copy(update={"retrieval_mode": mode})

                def resolver(games, query):
                    return search_index(db, settings, games, query)

                graph = build_graph(config, lambda *_: None, semantic_resolver=resolver)
                cases = []
                for case in dataset["cases"]:
                    output = graph.invoke(
                        {
                            "request": {
                                "query": case["query"],
                                "country": case["country"],
                                "budget_minor": None,
                                "exclude_owned": True,
                            },
                            "catalog": catalog,
                            "feedback": case.get("feedback", []),
                        }
                    )
                    ids, failures = assess(case, output)
                    if mode == "indexed_hybrid":
                        assert output["intent"]["retrieval"] == "indexed_hybrid_rrf", (
                            "Unexpected semantic fallback"
                        )
                    cases.append(
                        {
                            "id": case["id"],
                            "kind": case["kind"],
                            "actual_ids": ids,
                            "passed": not failures,
                            "failures": failures,
                        }
                    )
                evaluated.append(
                    {
                        "mode": mode,
                        "passed": sum(c["passed"] for c in cases),
                        "total": len(cases),
                        "cases": cases,
                    }
                )
            with httpx.Client(trust_env=False) as http:
                counts = http.get(base + "/stats").json()
            return {
                "status": "integration_passed",
                "model_id": MODEL_ID,
                "model_revision": MODEL_REVISION,
                "dimensions": first["dimensions"],
                "max_seq_length": info["max_seq_length"],
                "catalog_count": len(catalog),
                "catalog_fingerprint": fingerprint(catalog),
                "cold_build_seconds": round(elapsed, 3),
                "unchanged_rebuild_encoded": 0,
                "unchanged_rebuild_reused": second["reused_count"],
                "restart_read": True,
                "http_stats": counts,
                "development_evaluation": evaluated,
                "limitations": [
                    "Not a blind or independent quality benchmark; labels are from the existing exploratory set.",
                    "Not a production deployment, external paid provider test, or throughput benchmark.",
                    "Documents longer than model max_seq_length are truncated by the pinned model.",
                    "Quality regression is reported, not hidden by changing labels or thresholds.",
                ],
            }
        finally:
            if db:
                db.close()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            model_log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--model-python")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return 0
    if not args.allow_model_download or not args.model_python or not args.output:
        parser.error("--allow-model-download, --model-python and --output are required")
    if args.output.exists():
        parser.error("Output exists; use a fresh report path")
    result = probe(args.model_python)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "development_evaluation"},
            ensure_ascii=False,
        )
    )
    print(
        json.dumps(
            [
                {k: mode[k] for k in ("mode", "passed", "total")}
                for mode in result["development_evaluation"]
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
