"""The hard benchmark's answer keys must agree with their own PDFs and with themselves.

A wrong answer key would make the AI look worse (or better) than it is, so these run
offline on every test run.
"""

import importlib.util
import io
import json
from datetime import date
from pathlib import Path

import pdfplumber
import pytest
from dateutil.relativedelta import relativedelta

from leasedesk import benchmark_hard as bh
from leasedesk import evaluation, wire_format

CASES = {c.code: c for c in bh.build_hard_cases()}


def pdf_text(case) -> str:
    with pdfplumber.open(io.BytesIO(case.pdf_bytes)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def page_count(case) -> int:
    with pdfplumber.open(io.BytesIO(case.pdf_bytes)) as pdf:
        return len(pdf.pages)


def test_ten_distinct_cases():
    assert list(CASES) == [f"H{i}" for i in range(1, 11)]
    assert len({c.file_name for c in CASES.values()}) == 10


@pytest.mark.parametrize("code", list(CASES))
def test_answer_key_is_internally_consistent(code):
    a = CASES[code].abstract
    assert evaluation.accuracy(evaluation.compare(a, a)) == 1.0
    steps = a.base_rent_schedule
    assert steps and steps[0].start_date >= a.commencement_date
    for prev, nxt in zip(steps, steps[1:]):
        assert prev.end_date + relativedelta(days=1) == nxt.start_date, "rent steps must be contiguous"
    assert steps[-1].end_date == a.expiration_date
    for o in a.options:
        assert o.notice_deadline is not None and o.notice_deadline > a.commencement_date


@pytest.mark.parametrize("code", ["H1", "H2", "H3", "H4", "H7", "H8", "H9", "H10"])
def test_text_pdfs_state_the_answer_keys_inputs(code):
    case, text = CASES[code], pdf_text(CASES[code])
    a = case.abstract
    assert a.tenant_name.split(",")[0] in text
    assert a.suite in text
    assert f"{a.square_feet:,.0f}" in text


def test_h1_rent_matches_an_independent_calculation():
    psf, expected = 31.50, []
    for _ in range(5):
        expected.append(round(psf * 2350 / 12 + 1e-9, 2))
        psf = round(psf * 1.03 + 1e-9, 2)
    assert [s.monthly_rent for s in CASES["H1"].abstract.base_rent_schedule] == expected


def test_h2_formula_dates():
    a = CASES["H2"].abstract
    assert (a.commencement_date, a.rent_commencement_date, a.expiration_date) == (
        date(2025, 6, 1), date(2025, 7, 31), date(2030, 7, 31))
    assert "the fourteenth day of January, two thousand twenty-five" in " ".join(pdf_text(CASES["H2"]).split())


def test_h3_and_h9_option_series():
    assert [o.notice_deadline for o in CASES["H3"].abstract.options] == [date(2034, 3, 31), date(2039, 3, 31)]
    # The second option term ends February 29, 2040 (a leap day), so its deadline is September 2.
    assert [o.notice_deadline for o in CASES["H9"].abstract.options] == [
        date(2034, 9, 1), date(2039, 9, 2), date(2044, 9, 1)]
    assert CASES["H3"].abstract.security_deposit is None and CASES["H9"].abstract.security_deposit is None


def test_h4_is_abstracted_as_amended():
    a = CASES["H4"].abstract
    assert a.expiration_date == date(2029, 6, 30) and len(a.base_rent_schedule) == 10
    text = pdf_text(CASES["H4"])
    assert "FIRST AMENDMENT TO LEASE" in text and "2029" in text


def test_h5_and_h6_are_image_only_scans():
    assert pdf_text(CASES["H5"]).strip() == "" and pdf_text(CASES["H6"]).strip() == ""
    assert page_count(CASES["H6"]) >= 15


def test_h7_share_is_computed_from_the_formula():
    assert CASES["H7"].abstract.recoveries.pro_rata_share_pct == round(2760 / 64200 * 100, 2) == 4.30
    assert "4.30" not in pdf_text(CASES["H7"])  # never stated outright


def test_h10_distractors_are_present_but_not_the_answer():
    text, a = pdf_text(CASES["H10"]), CASES["H10"].abstract
    assert "3,600 square feet" in text and "$19.50" in text and "4,250,000" in text
    assert a.square_feet == 3050 and a.guarantor == "Margaret Ellison"
    assert "Guarantor: see Exhibit D" in text and "Eleven Thousand One Hundred Eighty-Three" in text


def test_words_helpers():
    assert bh.words(1_968_000) == "one million nine hundred sixty-eight thousand"
    assert bh.dollars_in_words(8400.00) == "Eight Thousand Four Hundred and 00/100 Dollars ($8,400.00)"


def test_benchmark_script_scores_a_perfect_fake_api_at_100_percent(monkeypatch, capsys):
    """Run the real benchmark script end to end against a fake API that returns the answer keys."""
    import anthropic
    import httpx2 as httpx

    from leasedesk import extraction

    answers = {c.pdf_bytes: c.abstract for c in CASES.values()}

    def handler(request):
        import base64

        data = base64.b64decode(json.loads(request.content)["messages"][0]["content"][0]["source"]["data"])
        abstract = answers[data]
        return httpx.Response(200, json={
            "id": "msg", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": json.dumps(wire_format.to_wire_json(abstract))}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 100}})

    fake = anthropic.Anthropic(api_key="test", max_retries=0,
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    path = Path(__file__).resolve().parent.parent / "scripts" / "evaluate_extraction.py"
    spec = importlib.util.spec_from_file_location("evaluate_extraction", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    # PDFs embed a creation time, so hand the script these exact cases rather than rebuilding them.
    monkeypatch.setattr(bh, "build_hard_cases", lambda: list(CASES.values()))
    result = script.run_set("hard", fake)
    out = capsys.readouterr().out
    assert result["accuracy"] == 1.0 and result["total"] > 200, out
    assert "Field accuracy: 100.0%" in out and "FAILED" not in out


def test_a_failed_extraction_counts_as_all_fields_missed(monkeypatch):
    path = Path(__file__).resolve().parent.parent / "scripts" / "evaluate_extraction.py"
    spec = importlib.util.spec_from_file_location("evaluate_extraction", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    from leasedesk import extraction

    def refuse(client, path):
        raise extraction.ExtractionError("The AI declined to process this document.")

    monkeypatch.setattr(extraction, "abstract_pdf", refuse)
    result = script.run_set("hard", client=None)
    assert result["correct"] == 0 and result["total"] > 200
