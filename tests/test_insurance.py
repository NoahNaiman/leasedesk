"""Insurance certificates: status rules, storage, reading a certificate, and the sandbox."""

import json
from datetime import date

import anthropic
import httpx2 as httpx
import pytest

from leasedesk import db, insurance, portfolio, sandbox
from leasedesk.insurance import Certificate, Requirements

TODAY = date(2026, 11, 12)
REQ = Requirements()


def cert(**overrides) -> Certificate:
    base = dict(expiration_date=date(2027, 6, 1), gl_each_occurrence=1_000_000, gl_aggregate=2_000_000,
                additional_insured=True)
    return Certificate(**(base | overrides))


@pytest.mark.parametrize("certificate, status", [
    (None, insurance.STATUS_MISSING),
    (cert(), insurance.STATUS_CURRENT),
    (cert(expiration_date=date(2026, 11, 1)), insurance.STATUS_EXPIRED),
    (cert(expiration_date=None), insurance.STATUS_EXPIRED),
    (cert(expiration_date=date(2026, 12, 1)), insurance.STATUS_EXPIRING),          # 19 days out
    (cert(gl_each_occurrence=500_000), insurance.STATUS_LOW_LIMITS),
    (cert(gl_each_occurrence=500_000, gl_aggregate=1_000_000, umbrella_limit=1_000_000),
     insurance.STATUS_CURRENT),                                                    # umbrella makes up the gap
    (cert(gl_aggregate=None), insurance.STATUS_LOW_LIMITS),
    (cert(additional_insured=False), insurance.STATUS_NOT_ADDITIONAL),
    (cert(additional_insured=None), insurance.STATUS_NOT_ADDITIONAL),
])
def test_status_rules(certificate, status):
    assert insurance.assess(certificate, REQ, TODAY)[0] == status


def test_the_most_urgent_problem_wins_and_all_are_listed():
    status, problems = insurance.assess(cert(expiration_date=date(2026, 1, 1), gl_each_occurrence=100_000,
                                             additional_insured=False), REQ, TODAY)
    assert status == insurance.STATUS_EXPIRED and len(problems) == 3


def test_requirements_can_relax_the_rules():
    relaxed = Requirements(gl_each_occurrence=500_000, gl_aggregate=500_000, additional_insured=False,
                           expiring_soon_days=7)
    assert insurance.assess(cert(gl_each_occurrence=500_000, additional_insured=False,
                                 expiration_date=date(2026, 12, 1)), relaxed, TODAY)[0] == insurance.STATUS_CURRENT


def test_storage_round_trip(conn):
    assert insurance.get_requirements(conn) == Requirements()
    insurance.save_requirements(conn, Requirements(2_000_000, 3_000_000, False, 45))
    assert insurance.get_requirements(conn) == Requirements(2_000_000, 3_000_000, False, 45)
    cid = insurance.save_certificate(conn, "Elm Plaza", "Acme Shoes", cert(carrier="Prairie Casualty"),
                                     source="manual")
    stored = insurance.list_certificates(conn)
    assert [(c.id, c.tenant, c.certificate.carrier, c.source) for c in stored] == [
        (cid, "Acme Shoes", "Prairie Casualty", "manual")]
    insurance.delete_certificate(conn, cid)
    assert insurance.list_certificates(conn) == []


def test_reading_a_certificate_with_a_mocked_api(tmp_path):
    expected = cert(insured_name="Acme Shoes LLC", carrier="Prairie Casualty", umbrella_limit=5_000_000)
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": expected.model_dump_json()}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 3000, "output_tokens": 200}})

    client = anthropic.Anthropic(api_key="test", max_retries=0,
                                 http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    pdf = tmp_path / "coi.pdf"
    pdf.write_bytes(sandbox.text_pdf("CERTIFICATE OF LIABILITY INSURANCE", "Insured: Acme Shoes LLC"))
    got, tokens_in, tokens_out = insurance.extract_certificate(client, pdf)
    assert got == expected and (tokens_in, tokens_out) == (3000, 200)
    assert seen["body"]["output_config"]["format"]["type"] == "json_schema"
    assert seen["body"]["messages"][0]["content"][0]["type"] == "document"


def test_sandbox_has_one_tenant_in_every_status(tmp_path):
    sb = sandbox.build_sandbox(tmp_path, TODAY)
    with db.connect(sb.data_dir / "leasedesk.sqlite") as conn:
        groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
        rows = insurance.tenant_insurance(groups, insurance.list_certificates(conn),
                                          insurance.get_requirements(conn), TODAY)
    assert {r.tenant: r.status for r in rows} == {
        "QuickShip Mailing": insurance.STATUS_MISSING,
        "Summit Fitness": insurance.STATUS_EXPIRED,
        "Golden Dragon Kitchen": insurance.STATUS_LOW_LIMITS,
        "Paws & Claws Pet Supply": insurance.STATUS_NOT_ADDITIONAL,
        "Copper Kettle Coffee": insurance.STATUS_EXPIRING,
        "Harbor Mattress": insurance.STATUS_CURRENT,          # under the limits alone; the umbrella covers it
        "Sunrise Bagels": insurance.STATUS_CURRENT,
        "Brightside Pharmacy": insurance.STATUS_CURRENT,
        "FreshWay Market": insurance.STATUS_CURRENT,
        "Tri-County Bank": insurance.STATUS_CURRENT,
    }
    assert [r.status for r in rows][0] == insurance.STATUS_MISSING     # most urgent first
    harbor = next(r for r in rows if r.tenant == "Harbor Mattress")
    assert harbor.certificate.stored_path.endswith("Insurance/Certificate of Liability Insurance.pdf")
