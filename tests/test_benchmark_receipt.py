"""CI gate: the committed benchmark receipt must match the current prompt, schema, and model.

If this fails, the extraction prompt, schema, model, or post-extraction conventions changed
since the benchmark last ran. Run the benchmark (it calls the paid API) and commit the
updated benchmark/receipt.json together with the change:

    .venv/bin/python scripts/evaluate_extraction.py --set all
"""

from leasedesk import benchmark_receipt, extraction


def test_benchmark_ran_on_the_current_prompt_schema_and_model():
    receipt = benchmark_receipt.load_receipt()
    assert receipt is not None, f"No benchmark receipt. Run: {benchmark_receipt.RERUN_COMMAND}"
    assert receipt["fingerprint"] == benchmark_receipt.fingerprint(), (
        "The extraction prompt, schema, model, or conventions changed since the benchmark last ran. "
        f"Run `{benchmark_receipt.RERUN_COMMAND}` and commit benchmark/receipt.json with the change.")
    assert {r["set"] for r in receipt["results"]} == {"standard", "hard"}


def test_fingerprint_changes_when_the_prompt_or_model_changes(monkeypatch):
    from leasedesk import config

    before = benchmark_receipt.fingerprint()
    monkeypatch.setattr(extraction, "SYSTEM_PROMPT", extraction.SYSTEM_PROMPT + " Be brief.")
    assert benchmark_receipt.fingerprint() != before
    monkeypatch.undo()
    monkeypatch.setattr(config, "EXTRACTION_MODEL", "some-other-model")
    assert benchmark_receipt.fingerprint() != before


def test_fingerprint_ignores_unrelated_code():
    # Same inputs, same fingerprint: refactoring error messages or the UI doesn't force a paid rerun.
    assert benchmark_receipt.fingerprint() == benchmark_receipt.fingerprint(benchmark_receipt.extraction_inputs())
