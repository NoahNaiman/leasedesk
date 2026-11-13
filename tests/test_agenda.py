from datetime import date, timedelta

from leasedesk import agenda, db, demo_data, insurance, portfolio, sandbox

TODAY = date(2026, 11, 10)


def setup(conn):
    demo_data.load_demo_into_db(conn, TODAY)
    groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
    return groups


def cert(expires):
    return insurance.Certificate(carrier="Prairie Casualty", expiration_date=expires, gl_each_occurrence=1_000_000,
                                 gl_aggregate=2_000_000, additional_insured=True)


def test_each_lease_event_appears_on_its_own_day(conn):
    groups = setup(conn)
    req = insurance.Requirements()
    events = portfolio.critical_dates(db.list_documents(conn, db.STATUS_APPROVED), TODAY, days_ahead=365)
    assert events
    for e in events:
        on_day = agenda.items(groups, [], req, e.when)
        assert any(i.tenant == e.tenant and i.when == e.when and i.detail == e.detail for i in on_day), e
        assert all(i.when == e.when for i in on_day)


def test_option_deadlines_get_90_and_30_day_reminders(conn):
    groups = setup(conn)
    req = insurance.Requirements()
    deadline = next(e for e in portfolio.critical_dates(db.list_documents(conn, db.STATUS_APPROVED), TODAY, 3650)
                    if e.event == portfolio.EVENT_OPTION)
    for days in agenda.OPTION_REMINDER_DAYS:
        day = deadline.when - timedelta(days=days)
        reminders = [i for i in agenda.items(groups, [], req, day) if i.kind == agenda.KIND_OPTION_REMINDER]
        assert any(i.tenant == deadline.tenant and f"in {days} days" in i.detail for i in reminders)


def test_certificate_expiry_and_advance_reminder(conn):
    groups = setup(conn)
    g = next(g for g in groups if g.name == "Sunrise Bagels")
    expires = TODAY + timedelta(days=45)
    insurance.save_certificate(conn, g.property, g.name, cert(expires), source="manual")
    certs = insurance.list_certificates(conn)
    req = insurance.Requirements(expiring_soon_days=30)

    on_expiry = agenda.items(groups, certs, req, expires)
    assert [i.kind for i in on_expiry if i.tenant == g.name] == [agenda.KIND_COI_EXPIRES]
    warning = agenda.items(groups, certs, req, expires - timedelta(days=30))
    assert any(i.kind == agenda.KIND_COI_REMINDER and i.tenant == g.name for i in warning)
    assert not any(i.tenant == g.name and i.kind.startswith("Insurance")
                   for i in agenda.items(groups, certs, req, expires - timedelta(days=1)))


def test_rent_commencement_shown_only_when_it_differs(conn):
    groups = setup(conn)
    req = insurance.Requirements()
    for g in groups:
        t = g.terms
        if t.rent_commencement_date and t.rent_commencement_date != t.commencement_date:
            kinds = {i.kind for i in agenda.items(groups, [], req, t.rent_commencement_date) if i.tenant == g.name}
            assert agenda.KIND_RENT_COMMENCEMENT in kinds
        if t.commencement_date:
            kinds = [i.kind for i in agenda.items(groups, [], req, t.commencement_date) if i.tenant == g.name]
            assert agenda.KIND_COMMENCEMENT in kinds
            if t.rent_commencement_date == t.commencement_date:
                assert agenda.KIND_RENT_COMMENCEMENT not in kinds


def test_range_is_sorted_by_date_then_urgency(conn):
    groups = setup(conn)
    rows = agenda.items(groups, [], insurance.Requirements(), TODAY, TODAY + timedelta(days=365))
    assert rows and all(TODAY <= i.when <= TODAY + timedelta(days=365) for i in rows)
    keys = [(i.when, agenda.KIND_ORDER.index(i.kind)) for i in rows]
    assert keys == sorted(keys)
    assert list(agenda.frame(rows).columns) == ["Date", "Event", "Property", "Tenant", "Suite", "Details"]


def test_expired_tenants_drop_out(conn):
    groups = setup(conn)
    after_all = max(g.terms.expiration_date for g in groups if g.terms.expiration_date) + timedelta(days=1)
    assert agenda.items(groups, [], insurance.Requirements(), after_all, after_all + timedelta(days=3650)) == []


def test_sandbox_has_an_item_today(tmp_path):
    sb = sandbox.build_sandbox(tmp_path, TODAY)
    with db.connect(sb.data_dir / "leasedesk.sqlite") as conn:
        groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
        rows = agenda.items(groups, insurance.list_certificates(conn), insurance.get_requirements(conn), TODAY)
    assert any(i.kind == agenda.KIND_COI_EXPIRES and i.tenant == "Copper Kettle Coffee" for i in rows)
