"""Combining a tenant's lease and amendments into current terms."""

from datetime import date

from leasedesk import merge
from leasedesk.db import LeaseRecord
from leasedesk.models import (
    CapType,
    DocumentType,
    LeaseAbstract,
    LeaseOption,
    OptionType,
    Recoveries,
    RecoveryStructure,
    RentStep,
)

LEASE, AMEND, ASSIGN, GUARANTY = (DocumentType.LEASE, DocumentType.AMENDMENT, DocumentType.ASSIGNMENT,
                                  DocumentType.GUARANTY)


def record(doc_id: int, abstract: LeaseAbstract, name: str | None = None) -> LeaseRecord:
    return LeaseRecord(id=doc_id, file_name=name or f"doc{doc_id}.pdf", file_sha256=str(doc_id), stored_path="x",
                       page_count=1, is_scanned=False, status="approved", ai_abstract=abstract, abstract=abstract,
                       error=None, input_tokens=None, output_tokens=None, is_demo=False, created_at="",
                       abstracted_at=None, reviewed_at=None)


def step(start: date, end: date, rent: float) -> RentStep:
    return RentStep(start_date=start, end_date=end, monthly_rent=rent)


def renewal(deadline: date) -> LeaseOption:
    return LeaseOption(option_type=OptionType.RENEWAL, notice_deadline=deadline, term_months=60, description="")


def snowcap_chain() -> list[LeaseRecord]:
    """A 2010 lease, a 2020 extension, a 2022 suite renaming, and a 2025 extension (fictional)."""
    lease = LeaseAbstract(
        document_type=LEASE, tenant_name="Alpine Frozen Treats, LLC", trade_name="Snowcap Creamery", suite="Unit 5",
        square_feet=1281, lease_date=date(2010, 2, 28), commencement_date=date(2010, 4, 16),
        rent_commencement_date=date(2010, 10, 13), expiration_date=date(2015, 10, 31), security_deposit=8500,
        guarantor="Pat Lee",
        base_rent_schedule=[step(date(2010, 10, 13), date(2012, 4, 30), 2828.88)],
        options=[renewal(date(2015, 5, 4)), renewal(date(2020, 5, 4))],
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=7.34, admin_fee_pct=10,
                              cam_cap_type=CapType.NONE),
        notes="Original lease.")
    first = LeaseAbstract(
        document_type=AMEND, tenant_name="Alpine Frozen Treats, LLC", lease_date=date(2020, 10, 1),
        commencement_date=date(2020, 10, 1), expiration_date=date(2022, 9, 30),
        base_rent_schedule=[step(date(2020, 10, 1), date(2022, 9, 30), 3400.00)],
        options=[renewal(date(2022, 6, 2))])
    second = LeaseAbstract(document_type=AMEND, tenant_name="Alpine Frozen Treats, LLC", suite="Unit F",
                           lease_date=date(2022, 10, 19),
                           recoveries=Recoveries(structure=RecoveryStructure.UNKNOWN, cam_cap_type=CapType.UNKNOWN))
    third = LeaseAbstract(
        document_type=AMEND, tenant_name="Alpine Frozen Treats, LLC", lease_date=date(2025, 6, 30),
        commencement_date=date(2025, 10, 1), expiration_date=date(2030, 9, 30),
        base_rent_schedule=[step(date(2024, 10, 1), date(2025, 9, 30), 3625.39),
                            step(date(2025, 10, 1), date(2026, 9, 30), 3697.90)],
        options=[renewal(date(2030, 6, 2))], notes="Two months' free rent after remodel.")
    # Deliberately out of order: merging must sort by date, not by id.
    return [record(4, third, "Third Amendment.pdf"), record(1, lease, "Lease.pdf"),
            record(3, second, "Second Amendment.pdf"), record(2, first, "First Amendment.pdf")]


def test_amendments_apply_in_date_order_with_sources():
    m = merge.merge(snowcap_chain())
    a = m.abstract
    assert [r.file_name for r in m.applied] == ["Lease.pdf", "First Amendment.pdf", "Second Amendment.pdf",
                                                "Third Amendment.pdf"]
    assert a.document_type == LEASE
    # The lease's own dates come from the lease, not from an extension's start date.
    assert (a.lease_date, a.commencement_date, a.rent_commencement_date) == (
        date(2010, 2, 28), date(2010, 4, 16), date(2010, 10, 13))
    assert m.source("commencement_date").file_name == "Lease.pdf"
    # Later documents override what they state, and only that.
    assert a.expiration_date == date(2030, 9, 30) and m.source("expiration_date").file_name == "Third Amendment.pdf"
    assert a.suite == "Unit F" and m.source("suite").file_name == "Second Amendment.pdf"
    assert a.square_feet == 1281 and m.source("square_feet").file_name == "Lease.pdf"
    assert a.guarantor == "Pat Lee" and a.security_deposit == 8500
    # Every amendment restates the tenant's name; restating isn't changing, so the source stays the lease.
    assert m.source("tenant_name").file_name == "Lease.pdf"


def test_rent_schedule_is_spliced_and_current_rent_comes_from_the_latest_amendment():
    a = merge.merge(snowcap_chain()).abstract
    starts = [s.start_date for s in a.base_rent_schedule]
    # The first amendment's 2020 step is replaced by the third amendment's schedule from 2024 on.
    assert starts == [date(2010, 10, 13), date(2020, 10, 1), date(2024, 10, 1), date(2025, 10, 1)]
    assert a.current_monthly_rent(date(2026, 1, 15)) == 3697.90
    assert a.current_monthly_rent(date(2011, 1, 1)) == 2828.88


def test_new_options_replace_old_ones_and_unknowns_never_override():
    m = merge.merge(snowcap_chain())
    assert [o.notice_deadline for o in m.abstract.options] == [date(2030, 6, 2)]
    r = m.abstract.recoveries
    assert r.structure == RecoveryStructure.NNN and r.cam_cap_type == CapType.NONE  # "unknown" didn't override
    assert r.pro_rata_share_pct == 7.34 and m.source("recoveries.pro_rata_share_pct").file_name == "Lease.pdf"


def test_notes_are_kept_from_every_document_with_their_source():
    notes = merge.merge(snowcap_chain()).abstract.notes
    assert "Lease (Feb 28, 2010): Original lease." in notes
    assert "Amendment (Jun 30, 2025): Two months' free rent" in notes


def test_assignment_changes_the_tenant_and_guaranty_adds_a_guarantor():
    lease = LeaseAbstract(document_type=LEASE, tenant_name="R & F Corporation", trade_name="Hilltop Pizza",
                          lease_date=date(2010, 4, 9), expiration_date=date(2025, 11, 30))
    assignment = LeaseAbstract(document_type=ASSIGN, tenant_name="Hilltop 550 LLC", lease_date=date(2024, 12, 30),
                               expiration_date=date(2030, 11, 30))
    guaranty = LeaseAbstract(document_type=GUARANTY, tenant_name="Hilltop 550 LLC", guarantor="Dana Cruz",
                             lease_date=date(2024, 12, 30))
    a = merge.merge([record(1, lease), record(2, assignment), record(3, guaranty)]).abstract
    assert (a.tenant_name, a.trade_name, a.guarantor) == ("Hilltop 550 LLC", "Hilltop Pizza", "Dana Cruz")
    assert a.expiration_date == date(2030, 11, 30)


def test_a_newer_lease_supersedes_the_old_lease_and_its_amendments():
    old = LeaseAbstract(document_type=LEASE, tenant_name="Snappy Nails", lease_date=date(2015, 1, 12),
                        expiration_date=date(2024, 12, 31), suite="Units 1-3")
    old_amendment = LeaseAbstract(document_type=AMEND, lease_date=date(2022, 2, 18), suite="Units 1, 2 and 3")
    new = LeaseAbstract(document_type=LEASE, tenant_name="Harbor Nail Studio Inc.", lease_date=date(2024, 12, 30),
                        expiration_date=date(2029, 12, 31), suite="Units H & I")
    new_guaranty = LeaseAbstract(document_type=GUARANTY, guarantor="Mai Tran", lease_date=date(2024, 12, 30))
    m = merge.merge([record(1, old), record(2, old_amendment), record(3, new), record(4, new_guaranty)])
    assert [r.id for r in m.applied] == [3, 4] and sorted(r.id for r in m.superseded) == [1, 2]
    assert (m.abstract.suite, m.abstract.guarantor) == ("Units H & I", "Mai Tran")


def test_tenant_with_only_amendments_starts_from_the_earliest():
    first = LeaseAbstract(document_type=AMEND, tenant_name="Saguaro Grill Inc.", lease_date=date(2014, 10, 17),
                          square_feet=2451, expiration_date=date(2020, 9, 30))
    later = LeaseAbstract(document_type=AMEND, lease_date=date(2020, 6, 19), expiration_date=date(2030, 9, 30),
                          base_rent_schedule=[step(date(2020, 10, 1), date(2030, 9, 30), 9041.31)])
    a = merge.merge([record(2, later), record(1, first)]).abstract
    assert (a.square_feet, a.expiration_date) == (2451, date(2030, 9, 30))
    assert a.current_monthly_rent(date(2026, 11, 11)) == 9041.31


def test_undated_documents_apply_before_dated_ones_and_empty_input_is_none():
    lease = LeaseAbstract(document_type=LEASE, lease_date=date(2020, 1, 1), suite="A")
    undated = LeaseAbstract(document_type=AMEND, suite="B")
    dated = LeaseAbstract(document_type=AMEND, lease_date=date(2021, 1, 1), suite="C")
    assert merge.merge([record(1, lease), record(3, dated), record(2, undated)]).abstract.suite == "C"
    assert merge.merge([]) is None


def test_merging_does_not_change_the_stored_abstracts():
    chain = snowcap_chain()
    before = [r.abstract.model_dump() for r in chain]
    merge.merge(chain)
    assert [r.abstract.model_dump() for r in chain] == before
