from datetime import date

from leasedesk import db, demo_data
from leasedesk.models import OptionType

TODAY = date(2026, 11, 10)


def test_every_demo_lease_has_consistent_terms():
    for lease in demo_data.build_demo_portfolio(TODAY):
        a = lease.abstract
        assert a.commencement_date < a.expiration_date
        # Rent schedule covers the whole term with no gaps.
        steps = a.base_rent_schedule
        assert steps[0].start_date == a.commencement_date
        assert steps[-1].end_date == a.expiration_date
        for prev, nxt in zip(steps, steps[1:]):
            assert (nxt.start_date - prev.end_date).days == 1
        # Monthly rent matches $/SF x SF.
        for s in steps:
            assert abs(s.monthly_rent - s.annual_rent_psf * a.square_feet / 12) < 0.01


def test_natural_breakpoint_equals_base_rent_over_rate():
    golden = next(l for l in demo_data.build_demo_portfolio(TODAY) if l.abstract.suite == "B-150")
    pr = golden.abstract.percentage_rent
    annual = golden.abstract.base_rent_schedule[0].monthly_rent * 12
    assert pr.breakpoint_amount == round(annual / (pr.rate_pct / 100), 2)


def test_demo_has_upcoming_deadlines_to_show():
    abstracts = [l.abstract for l in demo_data.build_demo_portfolio(TODAY)]
    soon = [o for a in abstracts for o in a.options
            if o.option_type == OptionType.RENEWAL and 0 <= (o.notice_deadline - TODAY).days <= 90]
    assert soon, "demo should include a renewal notice due within 90 days"


def test_scanned_demo_leases_have_no_text_layer(conn):
    demo_data.load_demo_into_db(conn, TODAY)
    records = db.list_documents(conn)
    assert len(records) == len(demo_data.TENANTS)
    scanned = {r.file_name for r in records if r.is_scanned}
    assert scanned == {r.file_name for r in records if "(scan)" in r.file_name}
    assert db.status_counts(conn)[db.STATUS_REVIEW] == 2


def test_demo_can_be_removed(conn, data_dir):
    demo_data.load_demo_into_db(conn, TODAY)
    assert demo_data.remove_demo_from_db(conn) == len(demo_data.TENANTS)
    assert db.list_documents(conn) == []
    assert list((data_dir / "documents").glob("*.pdf")) == []


def test_each_option_in_a_series_has_its_own_deadline():
    from datetime import date

    from dateutil.relativedelta import relativedelta

    from leasedesk import demo_data

    leases = {l.abstract.trade_name: l.abstract for l in demo_data.build_demo_portfolio(date(2026, 11, 11))}
    brightside = leases["Brightside Pharmacy"]
    deadlines = [o.notice_deadline for o in brightside.options]
    assert len(deadlines) == 3
    assert deadlines[0] == brightside.expiration_date - relativedelta(days=180)
    assert deadlines[1] == deadlines[0] + relativedelta(months=60)
    assert len(leases["Copper Kettle Coffee"].options) == 1
