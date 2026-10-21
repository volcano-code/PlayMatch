import json
from copy import deepcopy
from pathlib import Path

import pytest
from playmatch.benchmark import (
    MODES,
    catalog_digest,
    evaluate,
    prepare_review,
    score_reviews,
    validate_dataset,
)
from playmatch.catalog_seed import load_catalog

ROOT = Path(__file__).resolve().parents[3]


def raw():
    return json.loads((ROOT / "evals/catalog-challenge-v06.json").read_text())


@pytest.fixture(scope="module")
def evaluated():
    catalog = load_catalog()
    data = raw()
    report = evaluate(data, catalog)
    return data, catalog, report


def test_frozen_cases_and_ablation(evaluated):
    data, catalog, report = evaluated
    assert len(catalog) == 48 and len(data["cases"]) == 40
    assert report["catalog_sha256"] == catalog_digest(catalog)
    assert report["independent_human_review"] is False
    assert report["ndcg_at_5"] is None
    for mode in MODES:
        assert report["modes"][mode]["summary"]["hard_violation_cases"] == 0
    # Structural alias retrieval regression, not independent recommendation accuracy.
    assert report["modes"]["bm25"]["summary"]["slices"]["alias"]["hit_at_5_count"] == 8


@pytest.mark.parametrize(
    "change",
    ["snapshot", "duplicate", "normalized_query", "unknown", "independence", "contradiction"],
)
def test_invalid_or_misrepresented_dataset(change):
    data = raw()
    if change == "snapshot":
        data["catalog_sha256"] = "0" * 64
    elif change == "duplicate":
        data["cases"].append(deepcopy(data["cases"][0]))
    elif change == "normalized_query":
        data["cases"][1]["query"] = " " + data["cases"][0]["query"] + " "
    elif change == "unknown":
        data["cases"][0]["relevant_ids"] = ["not-in-catalog"]
    elif change == "independence":
        data["independent_human_review"] = True
    else:
        data["cases"][0]["expect_empty"] = True
    with pytest.raises(ValueError):
        validate_dataset(data, load_catalog())


def test_digests_cover_hard_facts_not_just_text():
    catalog = load_catalog()
    updated = deepcopy(catalog)
    updated[0]["chinese_support"] = "unknown"
    assert catalog_digest(catalog) != catalog_digest(updated)
    assert catalog_digest(catalog) == catalog_digest(list(reversed(catalog)))


def reviewed_bundle(evaluated):
    data, catalog, report = evaluated
    bundle = prepare_review(data, catalog, report)
    sheets = []
    for who in ("rater-alpha", "rater-beta"):
        sheet = deepcopy(bundle["review_sheet"])
        sheet["reviewer_id"] = who
        sheet["provenance"] = "self_attested_human"
        for task in sheet["tasks"]:
            for candidate in task["candidates"]:
                candidate["grade"] = 1
        sheets.append(sheet)
    return bundle["scoring_key"], sheets


def test_review_export_is_ungraded_and_ranker_blind(evaluated):
    data, catalog, report = evaluated
    bundle = prepare_review(data, catalog, report)
    assert bundle == prepare_review(data, catalog, report)
    assert bundle["status"] == "awaiting_two_complete_reviewers"
    sheet = bundle["review_sheet"]
    assert sheet["provenance"] == "pending" and not sheet["reviewer_id"]
    assert len(sheet["tasks"]) == 32
    for task in sheet["tasks"]:
        assert not {"relevant_ids", "kind", "rankings"} & set(task)
        for candidate in task["candidates"]:
            assert not {"rank", "score", "model", "mode", "source_ranker"} & set(candidate)
            assert candidate["grade"] is None
    with pytest.raises(ValueError):
        score_reviews(bundle["scoring_key"], [sheet, sheet])


@pytest.mark.parametrize(
    "change",
    [
        "missing_grade",
        "bool_grade",
        "float_grade",
        "out_of_range",
        "same_rater",
        "wrong_pack",
        "wrong_snapshot",
        "missing_candidate",
        "duplicate_candidate",
        "missing_task",
        "unattested",
    ],
)
def test_review_requires_complete_two_person_snapshot(evaluated, change):
    key, sheets = reviewed_bundle(evaluated)
    candidate = sheets[0]["tasks"][0]["candidates"][0]
    if change in {"missing_grade", "bool_grade", "float_grade", "out_of_range"}:
        candidate["grade"] = {
            "missing_grade": None,
            "bool_grade": True,
            "float_grade": 1.5,
            "out_of_range": 4,
        }[change]
    elif change == "same_rater":
        sheets[1]["reviewer_id"] = sheets[0]["reviewer_id"]
    elif change == "wrong_pack":
        sheets[0]["pack_id"] = "bad"
    elif change == "wrong_snapshot":
        sheets[0]["catalog_sha256"] = "bad"
    elif change == "missing_candidate":
        sheets[0]["tasks"][0]["candidates"].pop()
    elif change == "duplicate_candidate":
        sheets[0]["tasks"][0]["candidates"].append(candidate)
    elif change == "missing_task":
        sheets[0]["tasks"].pop()
    else:
        sheets[0]["provenance"] = "llm"
    with pytest.raises(ValueError):
        score_reviews(key, sheets)


def test_pooled_ndcg_and_disagreement_are_qualified(evaluated):
    key, sheets = reviewed_bundle(evaluated)
    score = score_reviews(key, sheets)
    assert score["exact_grade_agreement"] == 1
    assert all(v is not None and 0 <= v <= 1 for v in score["pooled_ndcg_at_5"].values())
    assert "not_externally_verified" in score["provenance"] and "NOT_full_catalog" in score["scope"]
    sheets[1]["tasks"][0]["candidates"][0]["grade"] = 3
    assert score_reviews(key, sheets)["disagreement_count"] == 1


def test_no_relevant_pools_are_not_artificial_perfect_scores(evaluated):
    key, sheets = reviewed_bundle(evaluated)
    for sheet in sheets:
        for task in sheet["tasks"]:
            for candidate in task["candidates"]:
                candidate["grade"] = 0
    score = score_reviews(key, sheets)
    assert score["no_relevant_pool_cases"] == 32
    assert all(value is None for value in score["pooled_ndcg_at_5"].values())


@pytest.mark.parametrize("field", ["query", "title", "description", "evidence"])
def test_review_text_cannot_change_under_same_pack_id(evaluated, field):
    key, sheets = reviewed_bundle(evaluated)
    task = sheets[0]["tasks"][0]
    if field == "query":
        task[field] = "Another requirement"
    elif field == "evidence":
        task["candidates"][0][field] = []
    else:
        task["candidates"][0][field] = "Altered content"
    with pytest.raises(ValueError):
        score_reviews(key, sheets)
