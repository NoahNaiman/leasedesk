"""Proof that the benchmark ran on the current prompt, schema, and model.

Running the benchmark costs money, so CI can't run it on every push. Instead, each full
live run records a fingerprint of everything that changes what the AI is asked to do:
the model, the prompts, the request settings, the schema sent to the API, and the
conventions applied after extraction. The receipt (``benchmark/receipt.json``) is
committed with the results.

``tests/test_benchmark_receipt.py`` recomputes the fingerprint offline. If the prompt,
schema, or model changed without a new benchmark run, the test (and CI) fails with the
command to run.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime
from pathlib import Path

from leasedesk import config, extraction, wire_format

RECEIPT_PATH = config.PROJECT_ROOT / "benchmark" / "receipt.json"
RERUN_COMMAND = ".venv/bin/python scripts/evaluate_extraction.py --set all"


def extraction_inputs() -> dict:
    """Everything that determines what the AI is asked and how its answer is read."""
    return {
        "model": config.EXTRACTION_MODEL,
        "max_tokens": extraction.MAX_TOKENS,
        "effort": extraction.EFFORT,
        "system_prompt": extraction.SYSTEM_PROMPT,
        "user_prompt": extraction.USER_PROMPT,
        "wire_schema": wire_format.WIRE_SCHEMA,
        "conventions": inspect.getsource(extraction.apply_conventions),
    }


def fingerprint(inputs: dict | None = None) -> str:
    payload = json.dumps(inputs or extraction_inputs(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def write_receipt(results: list[dict], path: Path = RECEIPT_PATH) -> dict:
    receipt = {
        "fingerprint": fingerprint(),
        "model": config.EXTRACTION_MODEL,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "results": [{k: r[k] for k in ("set", "accuracy", "correct", "total", "cost", "citation_coverage")
                     if k in r} for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def load_receipt(path: Path = RECEIPT_PATH) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None
