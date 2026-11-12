"""Measure AI extraction accuracy against leases whose correct abstracts are known.

Two sets:
  standard  the 12 demo leases: clean, one layout. Mostly a regression check.
  hard      10 leases built around what trips up real abstraction (prose rent schedules,
            formula dates, struck-through corrections, a degraded 16-page scan, distractor
            figures, a lease and amendment in one file...). See leasedesk/benchmark_hard.py.

Usage (costs about $1.40 for standard, $2-3 for hard):
    .venv/bin/python scripts/evaluate_extraction.py [--set standard|hard|all]

The API key comes from ANTHROPIC_API_KEY or the app's Settings page.
"""

import argparse
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leasedesk import benchmark_hard, demo_data, evaluation, extraction, settings  # noqa: E402


def cases_for(name: str) -> list[tuple[str, bytes, object]]:
    if name == "standard":
        return [(lease.file_name, lease.pdf_bytes, lease.abstract) for lease in demo_data.build_demo_portfolio()]
    return [(case.file_name, case.pdf_bytes, case.abstract) for case in benchmark_hard.build_hard_cases()]


RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmark_results"


def run_set(name: str, client, save_to: Path | None = None) -> dict:
    """Abstract every lease in the set and score it. The AI's raw answers are saved to
    ``save_to`` so a run can be re-scored later without paying for it again."""
    cases = cases_for(name)
    with tempfile.TemporaryDirectory() as tmp:
        def one(case):
            file_name, pdf, expected = case
            path = Path(tmp) / file_name
            path.write_bytes(pdf)
            try:
                result = extraction.abstract_pdf(client, path)
            except extraction.ExtractionError as exc:
                # A failed extraction misses every field; leaving it out would flatter the score.
                missed = [evaluation.FieldResult(f.field, f.expected, None, False)
                          for f in evaluation.compare(expected, expected)]
                return file_name, missed, str(exc), 0.0
            if save_to:
                save_to.mkdir(parents=True, exist_ok=True)
                (save_to / f"{Path(file_name).stem}.json").write_text(result.abstract.model_dump_json(indent=2))
            return file_name, evaluation.compare(expected, result.abstract), None, result.cost_usd

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(one, cases))

    print(f"\n=== {name} set ({len(cases)} leases) ===")
    misses: Counter[str] = Counter()
    correct = total = 0
    per_lease, cost = [], 0.0
    for file_name, fields, error, case_cost in outcomes:
        cost += case_cost
        if error:
            print(f"  FAILED  {file_name}: {error} (all {len(fields)} fields counted as missed)")
        score = evaluation.accuracy(fields)
        per_lease.append(score)
        correct += sum(f.correct for f in fields)
        total += len(fields)
        if error:
            continue
        print(f"{score:7.1%}  {file_name}")
        for f in fields:
            if not f.correct:
                misses[f.field.split("[")[0]] += 1
                print(f"           ✗ {f.field}: expected {f.expected!r}, got {f.actual!r}")
    field_acc = correct / total if total else 0.0
    print(f"Field accuracy: {field_acc:.1%} ({correct}/{total} fields); "
          f"mean per lease {sum(per_lease) / max(1, len(per_lease)):.1%}; cost ${cost:.2f}")
    if misses:
        print("Most-missed: " + ", ".join(f"{k} ({v})" for k, v in misses.most_common(6)))
    return {"set": name, "accuracy": field_acc, "correct": correct, "total": total, "cost": cost}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=["standard", "hard", "all"], default="all")
    args = parser.parse_args()
    key = settings.get_api_key()
    if not key:
        print("Set ANTHROPIC_API_KEY or save a key in the app's Settings page first.")
        return 1
    client = extraction.make_client(key)
    sets = ["standard", "hard"] if args.set == "all" else [args.set]
    from datetime import datetime

    run_dir = RESULTS_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    results = [run_set(name, client, run_dir / name) for name in sets]
    print(f"\nAI answers saved in {run_dir}")
    if len(results) > 1:
        c, t = sum(r["correct"] for r in results), sum(r["total"] for r in results)
        print(f"\nOverall: {c / t:.1%} ({c}/{t} fields); total cost ${sum(r['cost'] for r in results):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
