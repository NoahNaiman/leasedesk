"""Measure AI extraction accuracy on the demo leases.

Generates the 12 demo lease PDFs, has Claude abstract each one, and compares
the result with the known-correct abstract field by field.

Usage (costs roughly $1-2 in API usage):
    ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python scripts/evaluate_extraction.py
"""

import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leasedesk import demo_data, evaluation, extraction, settings  # noqa: E402


def main() -> int:
    key = settings.get_api_key()
    if not key:
        print("Set ANTHROPIC_API_KEY or save a key in the app's Settings page first.")
        return 1
    client = extraction.make_client(key)
    misses: Counter[str] = Counter()
    scores, cost = [], 0.0

    with tempfile.TemporaryDirectory() as tmp:
        for lease in demo_data.build_demo_portfolio():
            path = Path(tmp) / lease.file_name
            path.write_bytes(lease.pdf_bytes)
            result = extraction.abstract_pdf(client, path)
            cost += result.cost_usd
            fields = evaluation.compare(lease.abstract, result.abstract)
            score = evaluation.accuracy(fields)
            scores.append(score)
            print(f"{score:6.1%}  {lease.file_name}")
            for f in fields:
                if not f.correct:
                    misses[f.field.split("[")[0]] += 1
                    print(f"         ✗ {f.field}: expected {f.expected!r}, got {f.actual!r}")

    print(f"\nOverall field accuracy: {sum(scores) / len(scores):.1%} across {len(scores)} leases")
    print(f"API cost: ${cost:.2f}")
    if misses:
        print("Most-missed fields:", ", ".join(f"{k} ({v})" for k, v in misses.most_common(5)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
