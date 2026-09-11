#!/usr/bin/env python3
"""Export Incorrect SOAP pairs for Phase-2 paraphrase audit.

Labels (manual): paraphrase | incomplete | wrong | format | other

Gate for Fix #4 (semantic matching): proceed only if paraphrase share > 35%.

Usage:
  python scripts/soap_incorrect_pair_audit.py
  python scripts/soap_incorrect_pair_audit.py --limit 80 --out results/incorrect_pair_audit.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medsum_testing.backend.services.soap_fact_scorer import (  # noqa: E402
    INCORRECT,
    classify_pair,
    load_scoring_config,
)


def _text(v) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    return str(v).strip()


def collect_incorrect(results_dir: Path) -> list[dict]:
    cfg = load_scoring_config(force_reload=True)
    rows: list[dict] = []
    for path in sorted(results_dir.glob("*.json")):
        if path.name.startswith("_") or path.name.startswith("."):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        soap = data.get("soap_comparison") or {}
        pair = soap.get("gt_vs_generated") if isinstance(soap, dict) else {}
        if not isinstance(pair, dict):
            continue
        for fact in pair.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            if _text(fact.get("result")) != INCORRECT:
                continue
            gt = fact.get("ground_truth")
            gen = fact.get("generated")
            fresh = classify_pair(gt, gen, cfg)["result"]
            rows.append(
                {
                    "result_file": path.name,
                    "section": _text(fact.get("section")),
                    "field": _text(fact.get("base_field") or fact.get("field")),
                    "ground_truth": _text(gt),
                    "generated": _text(gen),
                    "stored_result": INCORRECT,
                    "rescored_result": fresh,
                    "label": "",  # fill: paraphrase|incomplete|wrong|format|other
                    "notes": "",
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results",
        type=Path,
        default=ROOT / "results",
        help="Directory of run JSON files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=80,
        help="Max Incorrect pairs to sample for manual labeling",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for reproducible sampling",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "results" / "incorrect_pair_audit.csv",
        help="CSV output path",
    )
    args = parser.parse_args()

    all_rows = collect_incorrect(args.results)
    rng = random.Random(args.seed)
    sample = list(all_rows)
    rng.shuffle(sample)
    sample = sample[: max(0, args.limit)]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "result_file",
        "section",
        "field",
        "ground_truth",
        "generated",
        "stored_result",
        "rescored_result",
        "label",
        "notes",
    ]
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sample)

    still_incorrect = sum(1 for r in sample if r["rescored_result"] == INCORRECT)
    print(f"Wrote {len(sample)} Incorrect pairs -> {args.out}")
    print(f"Pool size: {len(all_rows)}")
    print(
        f"Still Incorrect under Phase-1 scorer: {still_incorrect}/{len(sample)} "
        f"({100.0 * still_incorrect / len(sample):.1f}%)"
        if sample
        else "No rows"
    )
    print(
        "Label column values: paraphrase | incomplete | wrong | format | other\n"
        "Fix #4 gate: paraphrase share > 35% of labeled rows."
    )


if __name__ == "__main__":
    main()
