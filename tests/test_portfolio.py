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
