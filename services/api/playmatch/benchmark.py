"""Reproducible development measurements and ranker-blind review handoff.

Partial positive labels support hit/MRR only. Pooled nDCG is withheld until two
complete, separately identified review sheets are supplied; provenance remains
self-attested, never automatically declared independent human ground truth.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import socket
import unicodedata
from copy import deepcopy
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict, Field, model_validator

from playmatch.config import Settings
from playmatch.retrieval import fingerprint
from playmatch.workflow import build_graph

MODES = ("keyword_baseline", "bm25_without_aliases", "bm25")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def catalog_digest(catalog: list[dict]) -> str:
    return digest(sorted(catalog, key=lambda game: game["id"]))


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z0-9-]{1,60}$")
    family: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=r"^(discovery|alias|constraint)$")
    query: str = Field(min_length=2, max_length=500)
    relevant_ids: list[str] = Field(default_factory=list, max_length=20)
    forbidden_ids: list[str] = Field(default_factory=list, max_length=100)
    required_fields: dict[str, str] = Field(default_factory=dict)
    excluded_themes: list[str] = Field(default_factory=list)
    expect_empty: bool = False
    evidence_urls: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def consistent(self):
        if len(self.relevant_ids) != len(set(self.relevant_ids)):
            raise ValueError("Duplicate positive label")
        if set(self.relevant_ids) & set(self.forbidden_ids) or (
            self.expect_empty and self.relevant_ids
        ):
            raise ValueError("Contradictory labels")
        if self.kind != "constraint" and not self.relevant_ids:
            raise ValueError("Discovery requires non-exhaustive positive labels")
        if not set(self.required_fields) <= {"chinese_support", "coop", "horror"}:
            raise ValueError("Unsupported constraint")
        return self


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: int = Field(ge=2, le=2)
    name: str
    split: str = Field(pattern=r"^development_challenge$")
    label_origin: str = Field(pattern=r"^coding_agent_provisional$")
    independent_human_review: bool
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: list[Case] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def integrity(self):
        if self.independent_human_review:
            raise ValueError("Development labels are not independent human judgments")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case id")
        queries = [
            "".join(unicodedata.normalize("NFKC", c.query).casefold().split()) for c in self.cases
        ]
        if len(set(queries)) != len(queries):
            raise ValueError(
                "Duplicate normalized query; paraphrase families are not independent samples"
            )
        return self


def validate_dataset(raw: dict, catalog: list[dict]) -> Dataset:
    dataset = Dataset.model_validate(raw)
    if dataset.catalog_sha256 != catalog_digest(catalog):
        raise ValueError("Dataset/catalog mismatch: re-review labels for this exact snapshot")
    valid = {game["id"] for game in catalog}
    for case in dataset.cases:
        if not (set(case.relevant_ids) | set(case.forbidden_ids)) <= valid:
            raise ValueError("Unknown labeled game")
        if case.kind != "constraint" and not case.evidence_urls:
            raise ValueError("Missing label evidence")
    return dataset


def evaluate(raw: dict, catalog: list[dict]) -> dict:
    dataset = validate_dataset(raw, catalog)
    reports = {}
    # Guard all sockets, including stream/send paths; no provider data, costs or LLM claims.
    with (
        patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("Offline benchmark network forbidden"),
        ),
        patch.dict("os.environ", {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}),
    ):
        for mode in MODES:
            settings = Settings(
                _env_file=None,
                llm_api_key="",
                llm_model="",
                retrieval_mode="bm25" if mode == "bm25_without_aliases" else mode,
            )
            games = deepcopy(catalog)
            if mode == "bm25_without_aliases":
                for game in games:
                    game.pop("aliases", None)
            graph = build_graph(settings, lambda *_: None)
            results = []
            for case in dataset.cases:
                output = graph.invoke(
                    {
                        "request": {
                            "query": case.query,
                            "country": "CN",
                            "budget_minor": None,
                            "exclude_owned": True,
                        },
                        "catalog": games,
                        "feedback": [
                            {"game_id": game, "kind": "exclude"} for game in case.forbidden_ids
                        ],
                    }
                )
                ranked = [row["game"] for row in output["results"]]
                ids = [game["id"] for game in ranked]
                violations = []
                if len(ids) != len(set(ids)):
                    violations.append("duplicate_result")
                if case.expect_empty and ids:
                    violations.append("expected_empty")
                for game in ranked:
                    if game["id"] in case.forbidden_ids:
                        violations.append("excluded_game")
                    if set(case.excluded_themes) & set(game["themes"]):
                        violations.append("excluded_theme")
                    for field, expected in case.required_fields.items():
                        if game[field] != expected:
                            violations.append(f"{field}_violation")
                ranks = [i + 1 for i, game in enumerate(ids) if game in case.relevant_ids]
                results.append(
                    {
                        "id": case.id,
                        "kind": case.kind,
                        "family": case.family,
                        "ranked_ids": ids,
                        "hit_at_1": bool(ranks and min(ranks) == 1) if case.relevant_ids else None,
                        "hit_at_5": bool(ranks) if case.relevant_ids else None,
                        "reciprocal_rank_at_5": 1 / min(ranks) if ranks else 0.0,
                        "hard_violations": violations,
                    }
                )
            labeled = [r for r in results if r["hit_at_5"] is not None]
            slices = {}
            for kind in ("discovery", "alias"):
                subset = [r for r in labeled if r["kind"] == kind]
                slices[kind] = {
                    "cases": len(subset),
                    "hit_at_5_count": sum(r["hit_at_5"] for r in subset),
                }
            reports[mode] = {
                "summary": {
                    "cases": len(results),
                    "positive_labeled_cases": len(labeled),
                    "hit_at_1_count": sum(r["hit_at_1"] for r in labeled),
                    "hit_at_5_count": sum(r["hit_at_5"] for r in labeled),
                    "mrr_at_5": sum(r["reciprocal_rank_at_5"] for r in labeled) / len(labeled)
                    if labeled
                    else None,
                    "hard_violation_cases": sum(bool(r["hard_violations"]) for r in results),
                    "slices": slices,
                },
                "cases": results,
            }
    return {
        "schema_version": 1,
        "dataset": dataset.name,
        "dataset_sha256": digest(raw),
        "catalog_sha256": catalog_digest(catalog),
        "retrieval_text_sha256": fingerprint(catalog),
        "catalog_size": len(catalog),
        "label_origin": dataset.label_origin,
        "independent_human_review": False,
        "network": "blocked",
        "quality_gate": "hard_constraints_only",
        "ndcg_at_5": None,
        "limitations": [
            "Coding-agent provisional, non-exhaustive positives; not independent, blind or a user-satisfaction measure.",
            "Unjudged games are NOT negatives; precision/recall/nDCG withheld.",
            "Same catalog and rules parser for all modes; aliases ablation changes only document aliases.",
            "No live prices, paid LLM, embedding benchmark or performance conclusion.",
        ],
        "modes": reports,
    }


def prepare_review(raw: dict, catalog: list[dict], report: dict) -> dict:
    dataset = validate_dataset(raw, catalog)
    if report["dataset_sha256"] != digest(raw) or report["catalog_sha256"] != catalog_digest(
        catalog
    ):
        raise ValueError("Report belongs to another dataset/catalog")
    games = {game["id"]: game for game in catalog}
    rng = random.Random(digest([raw, report]))
    tasks, rankings, key = [], {}, {}
    for case in dataset.cases:
        if case.kind == "constraint":
            continue
        ranked = {
            mode: next(
                r["ranked_ids"] for r in report["modes"][mode]["cases"] if r["id"] == case.id
            )
            for mode in MODES
        }
        # Include non-exhaustive positives to expose missed candidates, but do not reveal that status.
        pool = sorted(set(case.relevant_ids) | {game for ids in ranked.values() for game in ids})
        rng.shuffle(pool)
        candidates = []
        for index, game_id in enumerate(pool):
            token = digest([case.id, index, game_id, report["dataset_sha256"]])[:24]
            key[token] = game_id
            g = games[game_id]
            candidates.append(
                {
                    "candidate_id": token,
                    "title": g["title"],
                    "title_zh": g["title_zh"],
                    "description": g["description"],
                    "evidence": g["evidence"],
                    "grade": None,
                }
            )
        tasks.append(
            {
                "case_id": case.id,
                "family": case.family,
                "query": case.query,
                "candidates": candidates,
            }
        )
        rankings[case.id] = ranked
    if not tasks:
        raise ValueError("No rankable cases")
    rng.shuffle(tasks)
    body = {
        "schema_version": 1,
        "dataset_sha256": digest(raw),
        "catalog_sha256": catalog_digest(catalog),
        "reviewer_id": "",
        "provenance": "pending",
        "instructions": "Grade every candidate 0 (irrelevant) to 3 (strong fit). Candidate order is shuffled and ranker identity/scores/seed labels are withheld. Record your own judgment before discussing it with other reviewers.",
        "tasks": tasks,
    }
    body["pack_id"] = digest(body)
    return {
        "review_sheet": body,
        "scoring_key": {
            "pack_id": body["pack_id"],
            "catalog_sha256": body["catalog_sha256"],
            "dataset_sha256": body["dataset_sha256"],
            "candidate_games": key,
            "rankings": rankings,
            "expected_tasks": {
                t["case_id"]: [c["candidate_id"] for c in t["candidates"]] for t in tasks
            },
        },
        "status": "awaiting_two_complete_reviewers",
    }


def score_reviews(key: dict, sheets: list[dict]) -> dict:
    if len(sheets) != 2:
        raise ValueError("Exactly two complete reviewer sheets are required")
    reviewers, all_grades = set(), []
    expected = key["expected_tasks"]
    for sheet in sheets:
        reviewer = sheet.get("reviewer_id", "").strip()
        if (
            not reviewer
            or reviewer in reviewers
            or sheet.get("provenance") != "self_attested_human"
        ):
            raise ValueError(
                "Distinct named, self-attested reviewers required; templates are not labels"
            )
        reviewers.add(reviewer)
        if any(sheet.get(k) != key[k] for k in ("pack_id", "dataset_sha256", "catalog_sha256")):
            raise ValueError("Review snapshot mismatch")
        template = deepcopy(sheet)
        template.pop("pack_id", None)
        template["reviewer_id"] = ""
        template["provenance"] = "pending"
        for task in template.get("tasks", []):
            for candidate in task.get("candidates", []):
                candidate["grade"] = None
        if digest(template) != key["pack_id"]:
            raise ValueError(
                "Review text, evidence or order was altered; use the exported template"
            )
        tasks = sheet.get("tasks", [])
        if len(tasks) != len(expected) or {t["case_id"] for t in tasks} != set(expected):
            raise ValueError("Missing or duplicate review task")
        grades = {}
        for task in tasks:
            candidates = task["candidates"]
            tokens = [c["candidate_id"] for c in candidates]
            if len(tokens) != len(set(tokens)) or set(tokens) != set(expected[task["case_id"]]):
                raise ValueError("Review candidate coverage mismatch")
            for candidate in candidates:
                grade = candidate["grade"]
                if type(grade) is not int or not 0 <= grade <= 3:
                    raise ValueError("Every pooled candidate needs an integer 0..3 grade")
                grades[candidate["candidate_id"]] = grade
        all_grades.append(grades)
    means = {token: (all_grades[0][token] + all_grades[1][token]) / 2 for token in all_grades[0]}
    scores: dict[str, list[float]] = {mode: [] for mode in MODES}
    no_relevant = 0
    for case, tokens in expected.items():
        g = {key["candidate_games"][token]: means[token] for token in tokens}

        def dcg(values):
            return sum((2**grade - 1) / math.log2(i + 2) for i, grade in enumerate(values[:5]))

        ideal = dcg(sorted(g.values(), reverse=True))
        if not ideal:
            no_relevant += 1
            continue
        for mode in MODES:
            ranking = key["rankings"][case][mode]
            if len(ranking) != len(set(ranking)) or not set(ranking) <= set(g):
                raise ValueError("Invalid ranking in scoring key")
            scores[mode].append(dcg([g[game] for game in ranking]) / ideal)
    differences = [token for token in means if all_grades[0][token] != all_grades[1][token]]
    return {
        "pack_id": key["pack_id"],
        "provenance": "two_self_attested_humans_not_externally_verified",
        "scope": "fully_judged_union_of_top5_plus_seed_positives_NOT_full_catalog",
        "judged_pairs": len(means),
        "exact_grade_agreement": (len(means) - len(differences)) / len(means),
        "disagreement_count": len(differences),
        "no_relevant_pool_cases": no_relevant,
        "pooled_ndcg_at_5": {
            mode: sum(values) / len(values) if values else None for mode, values in scores.items()
        },
        "limitations": [
            "Review identity and independence are self-attested, not authenticated by this CLI.",
            "Relative to this retrieval pool, not full-catalog recall or unbiased production performance.",
            "No automated LLM grades are accepted as independent human evidence.",
        ],
    }
