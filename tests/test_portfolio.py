import io
from datetime import date

import pandas as pd

from leasedesk import db, demo_data, portfolio

TODAY = date(2026, 11, 10)


def approved(conn):
    demo_data.load_demo_into_db(conn, TODAY)
    return db.list_documents(conn, db.STATUS_APPROVED)


def test_critical_dates_window_and_order(conn):
    events = portfolio.critical_dates(approved(conn), TODAY, days_ahead=90)
    assert events, "demo should have events in the next 90 days"
    assert all(0 <= (e.when - TODAY).days <= 90 for e in events)
    assert [e.when for e in events] == sorted(e.when for e in events)
    # Harbor Mattress expires within 90 days in the demo.
    assert any(e.tenant == "Harbor Mattress" and e.event == portfolio.EVENT_EXPIRATION for e in events)


def test_rent_increase_event_describes_change(conn):
    events = portfolio.critical_dates(approved(conn), TODAY, days_ahead=365)
    bumps = [e for e in events if e.event == portfolio.EVENT_RENT_INCREASE]
    assert bumps and all("→" in e.detail for e in bumps)


def test_lease_table_and_search(conn):
    df = portfolio.lease_table(approved(conn), TODAY)
    assert len(df) == 10  # two demo leases are still awaiting review
    grocer = df[df["Suite"] == "A-100"].iloc[0]
    assert grocer["Rent $/SF/yr"] > 0
    assert len(portfolio.search(df, "freshway")) == 1
    assert len(portfolio.search(df, "PAD-1")) == 1
    assert portfolio.search(df, "").equals(df)


def test_excel_export_round_trips(conn):
    df = portfolio.lease_table(approved(conn), TODAY)
    data = portfolio.to_excel({"Leases": df})
    back = pd.read_excel(io.BytesIO(data), sheet_name="Leases")
    assert list(back.columns) == list(df.columns)
    assert len(back) == len(df)


def test_calendar_export_is_valid_ics(conn):
    events = portfolio.critical_dates(approved(conn), TODAY, days_ahead=365)
    ics = portfolio.to_ics(events).decode()
    assert ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR")
    assert ics.count("BEGIN:VEVENT") == len(events)
    # Option deadlines carry a two-week reminder.
    options = sum(1 for e in events if e.event == portfolio.EVENT_OPTION)
    assert ics.count("TRIGGER:-P14D") == options
    # Commas and semicolons in descriptions are escaped per RFC 5545.
    assert "\\," in ics


def _abstract(**fields):
    from leasedesk.models import LeaseAbstract
    return LeaseAbstract(**fields)


def test_property_label_falls_back_to_street_address():
    assert portfolio.property_label(_abstract(property_name="Maple Ridge Plaza")) == "Maple Ridge Plaza"
    assert portfolio.property_label(
        _abstract(property_address="410 Juniper Street, Unit F, Fairview, Ohio")) == "410 Juniper Street"
    assert portfolio.property_label(
        _abstract(property_address="12 Main St Suite 4, Boulder, CO")) == "12 Main St"
    assert portfolio.property_label(_abstract()) == portfolio.NO_PROPERTY


def test_tables_sorted_by_property_then_tenant(conn):
    records = approved(conn)
    leases = portfolio.lease_table(records, TODAY)
    keys = [(p.lower(), t.lower()) for p, t in zip(leases["Property"], leases["Tenant"])]
    assert keys == sorted(keys)
    assert leases["Property"].iloc[0] == "Maple Ridge Plaza"

    groups = portfolio.tenant_groups(records)
    tenants = portfolio.tenant_table(groups, TODAY)
    assert list(tenants["Tenant"]) == [g.name for g in groups]
    assert len(tenants) == 10

    props = portfolio.property_table(groups, TODAY)
    assert list(props["Property"]) == ["Maple Ridge Plaza", "Willow Creek Commons"]
    assert props["Tenants"].sum() == 10
    assert (props["Avg rent $/SF/yr"] > 0).all()


def test_amendments_group_under_their_tenant(conn):
    from leasedesk.models import DocumentType

    lease = _abstract(tenant_name="Alpine Frozen Treats, LLC, a Colorado limited liability company",
                      trade_name="Snowcap Creamery", property_address="410 Juniper Street, Unit 5, Fairview",
                      suite="Unit 5", square_feet=1281, lease_date=date(2010, 2, 28),
                      expiration_date=date(2015, 10, 31))
    amendment = _abstract(document_type=DocumentType.AMENDMENT, tenant_name="Alpine Frozen Treats, LLC",
                          property_address="410 Juniper Street, Unit F, Fairview", suite="Unit F",
                          lease_date=date(2025, 6, 30), expiration_date=date(2030, 9, 30))
    for name, a in (("lease.pdf", lease), ("amendment.pdf", amendment)):
        doc_id = db.add_document(conn, file_name=name, file_sha256=name, stored_path=name,
                                 page_count=1, is_scanned=False)
        db.save_review(conn, doc_id, a, approve=True)

    groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
    assert len(groups) == 1
    assert len(groups[0].records) == 2
    assert groups[0].primary.abstract.document_type == DocumentType.LEASE
    assert groups[0].name == "Snowcap Creamery"
    # The tenant row shows the newest stated values: the amendment's unit, the lease's square feet.
    row = portfolio.tenant_table(groups, TODAY).iloc[0]
    assert row["Suite"] == "Unit F"
    assert row["SF"] == 1281
    assert row["Expiration"] == date(2030, 9, 30)


def test_rename_property_merges_groups(conn):
    records = approved(conn)
    moved = portfolio.rename_property(conn, records, "Maple Ridge Plaza", "Willow Creek Commons")
    assert moved == 3
    groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
    assert {g.property for g in groups} == {"Willow Creek Commons"}


def test_tenant_label_drops_entity_description():
    assert portfolio.tenant_label(_abstract(tenant_name="HARBOR NAIL STUDIO INC., a Colorado corporation")) \
        == "HARBOR NAIL STUDIO INC."
    assert portfolio.tenant_label(_abstract(tenant_name="Acme, LLC, an Ohio limited liability company")) \
        == "Acme, LLC"
    assert portfolio.tenant_label(_abstract(tenant_name="X Corp", trade_name="X Store")) == "X Store"


def test_tenant_status_flags_documents_that_have_run_out():
    assert portfolio.tenant_status(date(2030, 1, 1), TODAY) == portfolio.STATUS_ACTIVE
    assert portfolio.tenant_status(date(2019, 8, 31), TODAY) == portfolio.STATUS_EXPIRED
    assert portfolio.tenant_status(None, TODAY) == portfolio.STATUS_UNKNOWN


def test_documents_in_one_folder_can_be_split_into_tenants(conn):
    from leasedesk.models import LeaseAbstract

    ids = []
    for name in ("Billboard #214 Lease.pdf", "Billboard #2352 Lease.pdf"):
        doc_id = db.add_document(conn, file_name=name, file_sha256=name, stored_path=name, page_count=1,
                                 is_scanned=False, tenant_folder="Billboard Leases")
        db.save_review(conn, doc_id, LeaseAbstract(tenant_name="Skyline Billboards LLC"), approve=True)
        ids.append(doc_id)
    assert len(portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))) == 1

    db.set_tenant_override(conn, ids[0], "Billboard B (#214)")
    db.set_tenant_override(conn, ids[1], "Billboard A (#2352)")
    groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
    assert sorted(g.name for g in groups) == ["Billboard A (#2352)", "Billboard B (#214)"]

    db.set_tenant_override(conn, ids[0], "")  # clearing returns it to the folder's tenant
    assert db.get_document(conn, ids[0]).tenant_override is None
