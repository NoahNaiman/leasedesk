"""Extraction tests run against a fake Anthropic server, so no API key or cost is needed."""

import json
from datetime import date

import anthropic
import httpx2 as httpx
import pytest

from leasedesk import db, demo_data, documents, extraction, wire_format
from leasedesk.models import LeaseAbstract


def fake_client(handler) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def message_response(abstract: LeaseAbstract, stop_reason="end_turn") -> httpx.Response:
    return httpx.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": json.dumps(wire_format.to_wire_json(abstract))}],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 12000, "output_tokens": 1500},
    })


@pytest.fixture
def expected():
    return demo_data.build_demo_portfolio(date(2026, 11, 10))[0]


def test_sends_pdf_with_schema_and_parses_result(tmp_path, expected):
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return message_response(expected.abstract)

    pdf = tmp_path / "lease.pdf"
    pdf.write_bytes(expected.pdf_bytes)
    result = extraction.abstract_pdf(fake_client(handler), pdf)

    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["messages"][0]["content"][0]["type"] == "document"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert result.abstract == expected.abstract
    assert result.cost_usd == pytest.approx(12000 * 4 / 1e6 + 1500 * 20 / 1e6)


def test_refusal_becomes_friendly_error(tmp_path, expected):
    pdf = tmp_path / "lease.pdf"
    pdf.write_bytes(expected.pdf_bytes)
    client = fake_client(lambda r: message_response(expected.abstract, stop_reason="refusal"))
    with pytest.raises(extraction.ExtractionError, match="declined"):
        extraction.abstract_pdf(client, pdf)


def test_bad_api_key_becomes_friendly_error(tmp_path, expected):
    pdf = tmp_path / "lease.pdf"
    pdf.write_bytes(expected.pdf_bytes)
    client = fake_client(lambda r: httpx.Response(
        401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
    with pytest.raises(extraction.ExtractionError, match="Settings page"):
        extraction.abstract_pdf(client, pdf)


def test_batch_processing_records_success_and_failure(conn, expected):
    good = documents.import_pdf_bytes(conn, "good.pdf", expected.pdf_bytes).doc_id
    other = demo_data.build_demo_portfolio(date(2026, 11, 10))[1]
    bad = documents.import_pdf_bytes(conn, "bad.pdf", other.pdf_bytes).doc_id

    def handler(request):
        data = json.loads(request.content)["messages"][0]["content"][0]["source"]["data"]
        if data == extraction.base64.standard_b64encode(other.pdf_bytes).decode():
            return httpx.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
        return message_response(expected.abstract)

    progress = []
    ok, failed = extraction.process_documents(
        conn, fake_client(handler), [good, bad], on_progress=lambda *a: progress.append(a))

    assert (ok, failed) == (1, 1)
    assert db.get_document(conn, good).status == db.STATUS_REVIEW
    assert db.get_document(conn, bad).status == db.STATUS_FAILED
    assert len(progress) == 2


def test_api_key_saved_privately(data_dir, monkeypatch):
    from leasedesk import config, settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert settings.get_api_key() is None
    settings.save_api_key("sk-ant-test-1234567890")
    assert settings.get_api_key() == "sk-ant-test-1234567890"
    assert oct(config.SETTINGS_PATH.stat().st_mode & 0o777) == "0o600"
    assert settings.masked_api_key() == "sk-ant-…7890"
