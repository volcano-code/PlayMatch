#!/usr/bin/env python3
"""Evaluate the real local rules graph on a small, explicitly non-blind dev set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from playmatch.catalog_seed import load_catalog  # noqa: E402
from playmatch.config import Settings  # noqa: E402
from playmatch.schemas import Recommendation  # noqa: E402
from playmatch.workflow import build_graph, rule_intent  # noqa: E402


def assess(case: dict, output: dict) -> tuple[list[str], list[str]]:
    results = output.get("results", [])
    result_ids = [row["game"]["id"] for row in results]
    failures = []
    if output.get("mode") != "rules":
        failures.append("Evaluation must use rules mode")
    if case["expect_empty"] and results:
        failures.append("Expected no verified match, but returned candidates")
    if case["acceptable_ids"] and not set(result_ids[:3]) & set(case["acceptable_ids"]):
        failures.append("No manually acceptable result in top 3")
    if len(result_ids) != len(set(result_ids)):
        failures.append("Duplicate recommendation ids")
    if set(result_ids) & set(case.get("forbidden_ids", [])):
        failures.append("Returned an explicitly excluded id")
    for row in results:
        Recommendation.model_validate(row)
        for field, expected in case.get("require_game_fields", {}).items():
            if row["game"].get(field) != expected:
                failures.append(f"{row['game']['id']}: required {field}={expected}")
        if set(row["game"]["themes"]) & set(case.get("forbidden_themes", [])):
            failures.append(f"{row['game']['id']}: contains an excluded theme")
        required_tradeoffs = case.get("all_tradeoffs_contain_any", [])
        if required_tradeoffs and not any(
            value in " ".join(row["tradeoffs"]) for value in required_tradeoffs
        ):
            failures.append(f"{row['game']['id']}: missing required uncertainty explanation")
    for field, expected in case.get("intent_equals", {}).items():
        if output.get("intent", {}).get(field) != expected:
            failures.append(f"Intent {field} did not equal {expected!r}")
    required_warnings = case.get("warning_contains_any", [])
    if required_warnings and not any(
        word in " ".join(output.get("warnings", [])) for word in required_warnings
    ):
        failures.append("Missing required warning")
    return result_ids, failures


def evaluate(dataset: dict, retrieval_mode: str = "keyword_baseline") -> dict:
    catalog = load_catalog()
    valid_ids = {row["id"] for row in catalog}
    settings = Settings(_env_file=None, llm_api_key="", llm_model="", retrieval_mode=retrieval_mode)
    graph = build_graph(settings, lambda *_: None)
    evaluated = []
    no_network = "Offline evaluation attempted network access"
    # Guard both synchronous model HTTP and asynchronous price HTTP. Even if a
    # future graph accidentally adds a call, this evaluation will fail offline.
    with (
        patch("httpx.Client.request", side_effect=AssertionError(no_network)),
        patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=AssertionError(no_network))),
        patch.dict("os.environ", {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}),
    ):
        for case in dataset["cases"]:
            referenced = set(case["acceptable_ids"]) | set(case.get("forbidden_ids", []))
            if not referenced <= valid_ids:
                raise ValueError(f"{case['id']}: labels refer to unknown catalog ids")
            request = {
                "query": case["query"],
                "country": case["country"],
                "budget_minor": None,
                "exclude_owned": True,
            }
            baseline = rule_intent(request)
            if baseline["country"] == "US" and baseline["budget_minor"] is not None:
                raise ValueError(f"{case['id']}: US budget cases are outside this offline suite")
            output = {}
            try:
                output = graph.invoke(
                    {"request": request, "catalog": catalog, "feedback": case.get("feedback", [])},
                    config={"recursion_limit": 12},
                )
                result_ids, failures = assess(case, output)
            except Exception as exc:
                result_ids = []
                failures = [f"Execution/contract error: {type(exc).__name__}: {str(exc)[:300]}"]
            acceptable = set(case["acceptable_ids"])
            evaluated.append(
                {
                    "id": case["id"],
                    "kind": case["kind"],
                    "query": case["query"],
                    "acceptable_ids": case["acceptable_ids"],
                    "actual_ids": result_ids,
                    "hit_at_1": bool(acceptable & set(result_ids[:1])) if acceptable else None,
                    "hit_at_3": bool(acceptable & set(result_ids[:3])) if acceptable else None,
                    "passed": not failures,
                    "failures": failures,
                    "warnings": output.get("warnings", []),
                }
            )
    discovery = [case for case in evaluated if case["kind"] == "discovery"]
    constraints = [case for case in evaluated if case["kind"] == "constraint"]
    catalog_path = Path(os.getenv("PLAYMATCH_CATALOG_PATH", ROOT / "data/catalog.json"))
    return {
        "dataset": dataset["name"],
        "split": dataset["split"],
        "evaluated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "mode": "rules",
        "retrieval_mode": retrieval_mode,
        "network": "blocked",
        "catalog_size": len(catalog),
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "limitations": [
            "Small exploratory development set, manually labeled by the coding agent; not blind or independent.",
            "Acceptable ids are non-exhaustive positive labels; Hit@k is not precision, satisfaction or generalization.",
            "This evaluates the selected lexical ranker only, not LLM, embeddings, live prices or purchase eligibility.",
        ],
        "summary": {
            "total_cases": len(evaluated),
            "passed_cases": sum(case["passed"] for case in evaluated),
            "discovery_cases": len(discovery),
            "discovery_hit_at_1": sum(case["hit_at_1"] for case in discovery),
            "discovery_hit_at_3": sum(case["hit_at_3"] for case in discovery),
            "constraint_cases": len(constraints),
            "constraint_passed": sum(case["passed"] for case in constraints),
        },
        "cases": evaluated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/rules-dev-v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/rules-dev-v1-results.json")
    parser.add_argument(
        "--check", action="store_true", help="Exit nonzero if any frozen case fails"
    )
    parser.add_argument("--mode", choices=["keyword_baseline", "bm25"], default="keyword_baseline")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    if dataset.get("schema_version") != 1 or dataset.get("split") != "exploratory_development":
        parser.error("Expected a v1 exploratory development dataset")
    report = evaluate(dataset, args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    for case in report["cases"]:
        if not case["passed"]:
            print(f"FAIL {case['id']}: {'; '.join(case['failures'])}")
    return int(args.check and report["summary"]["passed_cases"] != report["summary"]["total_cases"])


if __name__ == "__main__":
    raise SystemExit(main())
