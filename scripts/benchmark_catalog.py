#!/usr/bin/env python3
"""Offline catalog benchmark and reviewer handoff; never calls live providers."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api"))
from playmatch.benchmark import evaluate, prepare_review, score_reviews  # noqa: E402
from playmatch.catalog_seed import load_catalog  # noqa: E402


def read(path):
    if path.stat().st_size > 5_000_000:
        raise ValueError("Input exceeds 5 MB")
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("evaluate")
    run.add_argument("--dataset", type=Path, default=ROOT / "evals/catalog-challenge-v06.json")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument(
        "--check",
        action="store_true",
        help="Gate hard constraints, not a tuned positive-hit target",
    )
    prep = sub.add_parser("prepare")
    prep.add_argument("--dataset", type=Path, default=ROOT / "evals/catalog-challenge-v06.json")
    prep.add_argument("--report", type=Path, required=True)
    prep.add_argument("--directory", type=Path, required=True)
    score = sub.add_parser("score")
    score.add_argument("--key", type=Path, required=True)
    score.add_argument("--reviews", type=Path, nargs=2, required=True)
    score.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "evaluate":
            report = evaluate(read(args.dataset), load_catalog())
            write(args.output, report)
            print(
                json.dumps(
                    {mode: data["summary"] for mode, data in report["modes"].items()},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return int(
                args.check
                and any(
                    data["summary"]["hard_violation_cases"] for data in report["modes"].values()
                )
            )
        if args.command == "prepare":
            bundle = prepare_review(read(args.dataset), load_catalog(), read(args.report))
            # Refuse replacement of completed or partly completed work.
            if args.directory.exists():
                raise ValueError("Review directory exists; use a new directory")
            args.directory.mkdir(parents=True)
            write(args.directory / "review-template.json", bundle["review_sheet"])
            write(args.directory / "scoring-key.json", bundle["scoring_key"])
            print("Prepared ungraded sheets; keep scoring-key.json away from reviewers.")
            return 0
        report = score_reviews(read(args.key), [read(path) for path in args.reviews])
        write(args.output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Benchmark rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
