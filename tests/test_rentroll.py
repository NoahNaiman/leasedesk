"""Rent roll check: reading exports, matching rows to tenants, and each kind of finding."""

from datetime import date

import pytest

from leasedesk import db, rentroll, sandbox
from leasedesk.db import LeaseRecord
from leasedesk.models import LeaseAbstract, RentStep

AS_OF = date(2026, 11, 12)

EXPORT = b"""Rent Roll
As of 11/12/2026

Property,Unit,Tenant,Lease From,Lease To,Sq Ft,Rent
Elm Plaza,Suite 101,Acme Shoes LLC,01/01/2024,12/31/2028,"1,500","$3,000.00"
Elm Plaza,102,Bolt Cafe Inc.,01/01/2025,12/31/2029,"2,000","4,000.00"
Elm Plaza,,Total,,,,"7,000.00"
,,Grand Total,,,,
"""


def record(doc_id, **fields) -> LeaseRecord:
    a = LeaseAbstract(property_name="Elm Plaza", **fields)
    return LeaseRecord(id=doc_id, file_name=f"lease{doc_id}.pdf", file_sha256=str(doc_id), stored_path="x",
                       page_count=1, is_scanned=False, status="approved", ai_abstract=a, abstract=a, error=None,
                       input_tokens=None, output_tokens=None, is_demo=False, created_at="", abstracted_at=None,
                       reviewed_at=None)


def steps(*pairs):
    out = []
    for i, (start, rent) in enumerate(pairs):
        end = pairs[i + 1][0] if i + 1 < len(pairs) else date(2030, 12, 31)
        out.append(RentStep(start_date=start, end_date=end, monthly_rent=rent))
    return out


def rows_from(data=EXPORT):
    df = rentroll.read_table(data, "roll.csv")
    return rentroll.parse_rows(df, rentroll.guess_mapping(list(df.columns)))


def test_reads_report_exports_with_title_lines_and_total_rows():
    rows = rows_from()
    assert [(r.unit, r.tenant, r.monthly_rent, r.sf) for r in rows] == [
        ("Suite 101", "Acme Shoes LLC", 3000.0, 1500.0), ("102", "Bolt Cafe Inc.", 4000.0, 2000.0)]
    assert rows[0].lease_to == date(2028, 12, 31)


def test_excel_exports_and_mapping_by_synonym(tmp_path):
    import pandas as pd

    buf = tmp_path / "roll.xlsx"
    pd.DataFrame({"Suite": ["7"], "Lessee": ["Zed LLC"], "Base Rent": ["1,234.50"], "Square Feet": ["900"]}) \
        .to_excel(buf, index=False)
    df = rentroll.read_table(buf.read_bytes(), "roll.xlsx")
    mapping = rentroll.guess_mapping(list(df.columns))
    assert (mapping["unit"], mapping["tenant"], mapping["monthly_rent"], mapping["sf"]) == (
        "Suite", "Lessee", "Base Rent", "Square Feet")
    assert rentroll.parse_rows(df, mapping)[0].monthly_rent == 1234.50


@pytest.mark.parametrize("text, value", [("$3,000.00", 3000.0), ("(250.00)", -250.0), ("", None), ("-", None)])
def test_money_parsing(text, value):
    assert rentroll._money(text) == value


def test_each_kind_of_finding():
    acme = record(1, tenant_name="Acme Shoes LLC", suite="101", square_feet=1500, expiration_date=date(2028, 12, 31),
                  base_rent_schedule=steps((date(2024, 1, 1), 3000.0), (date(2026, 1, 1), 3090.0)))
    bolt = record(2, tenant_name="Bolt Cafe Inc.", suite="102", square_feet=2100, expiration_date=date(2030, 12, 31),
                  base_rent_schedule=steps((date(2025, 1, 1), 3850.0)))
    cove = record(3, tenant_name="Cove Books LLC", suite="103", expiration_date=date(2031, 1, 31),
                  base_rent_schedule=steps((date(2026, 2, 1), 2500.0)))
    report = rentroll.check(rows_from(), [acme, bolt, cove], AS_OF)
    found = {(f.kind, f.tenant) for f in report.findings}
    assert found == {
        (rentroll.KIND_MISSED_ESCALATION, "Acme Shoes LLC"),   # still billed the 2024 rent
        (rentroll.KIND_RENT_MISMATCH, "Bolt Cafe Inc."),        # $4,000 vs $3,850
        (rentroll.KIND_EXPIRATION, "Bolt Cafe Inc."),           # 2029 vs 2030
        (rentroll.KIND_SF, "Bolt Cafe Inc."),                   # 2,000 vs 2,100 SF
        (rentroll.KIND_NOT_IN_RENT_ROLL, "Cove Books LLC"),
    }
    acme_finding = next(f for f in report.findings if f.tenant == "Acme Shoes LLC")
    assert acme_finding.monthly_difference == 90.0
    assert report.monthly_underbilling == 90.0          # overbilling isn't counted as owed
    assert (report.rows, report.matched, report.clean) == (2, 2, 0)


def test_unknown_tenants_and_expired_leases():
    gone = record(1, tenant_name="Acme Shoes LLC", suite="101", expiration_date=date(2025, 12, 31),
                  base_rent_schedule=steps((date(2021, 1, 1), 3000.0)))
    report = rentroll.check(rows_from(), [gone], AS_OF)
    kinds = {(f.kind, f.tenant) for f in report.findings}
    assert (rentroll.KIND_BILLING_AFTER_EXPIRATION, "Acme Shoes LLC") in kinds
    assert (rentroll.KIND_NOT_IN_LEASEDESK, "Bolt Cafe Inc.") in kinds


def test_matches_by_tenant_name_when_units_differ():
    acme = record(1, tenant_name="Acme Shoes LLC, a Colorado limited liability company", trade_name="Acme Shoes",
                  suite="Units 101-102", expiration_date=date(2028, 12, 31),
                  base_rent_schedule=steps((date(2024, 1, 1), 3000.0)))
    pairs, leftovers, unbilled = rentroll.match(rows_from(), rentroll.portfolio.tenant_groups([acme]))
    assert [(r.tenant, g.name) for r, g in pairs] == [("Acme Shoes LLC", "Acme Shoes")]
    assert [r.tenant for r in leftovers] == ["Bolt Cafe Inc."] and unbilled == []


def test_sandbox_rent_roll_finds_exactly_the_planted_problems(tmp_path):
    sb = sandbox.build_sandbox(tmp_path, AS_OF)
    with db.connect(sb.data_dir / "leasedesk.sqlite") as conn:
        report = rentroll.check(rows_from((sb.exports_dir / "Breeze Rent Roll.csv").read_bytes()),
                                db.list_documents(conn, db.STATUS_APPROVED), AS_OF,
                                db.list_documents(conn, db.STATUS_REVIEW))
    found = sorted(f.kind for f in report.findings)
    assert found == sorted(p.kind for p in sb.rent_roll_planted)
    for p in sb.rent_roll_planted:
        assert any(f.kind == p.kind and p.tenant.split()[0] in f.tenant for f in report.findings), p
    assert report.clean == report.matched - 4   # FreshWay, Brightside, Golden Dragon, Paws & Claws
