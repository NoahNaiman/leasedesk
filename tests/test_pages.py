"""Render every page of the app against the sandbox, headlessly, and click through key flows.

These catch crashes and display bugs (like a "$nan" or a missing import) that the
unit tests can't, without opening a browser.
"""

import importlib
import os
import re
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from leasedesk import db, sandbox

PAGES = {
    "views/dashboard.py": "Portfolio overview",
    "views/properties.py": "Properties",
    "views/tenants.py": "Tenants",
    "views/library.py": "Lease library",
    "views/critical_dates.py": "Critical dates",
    "views/rent_roll.py": "Rent roll check",
    "views/distributions.py": "Partner distributions",
    "views/import_leases.py": "Import leases",
    "views/review.py": "Review abstracts",
    "views/settings.py": "Settings",
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A fresh sandbox, the app pointed at it, and a function to open a page."""
    sb = sandbox.build_sandbox(tmp_path, date.today())
    monkeypatch.setenv("LEASEDESK_DATA_DIR", str(sb.data_dir))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LEASEDESK_LIBRARY_FOLDER", raising=False)
    from leasedesk import config

    importlib.reload(config)
    main = Path(__file__).resolve().parent.parent / "app" / "main.py"
    at = AppTest.from_file(str(main), default_timeout=60)
    at.run()

    def open_page(page: str) -> AppTest:
        at.switch_page(page)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    at.sandbox = sb
    at.open = open_page
    yield at
    monkeypatch.delenv("LEASEDESK_DATA_DIR")
    importlib.reload(config)


def texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [str(getattr(m, "value", "")) for m in at.metric]
    return "\n".join(parts)


@pytest.mark.parametrize("page, title", PAGES.items())
def test_every_page_renders(app, page, title):
    at = app.open(page)
    assert [t.value for t in at.title] == [title]
    assert not re.search(r"\bnan\b|\bNaT\b|\bNone\b", texts(at)), "a blank value was displayed raw"


def test_properties_page_shows_folder_properties_by_display_name(app):
    at = app.open("views/properties.py")
    assert at.metric[0].value == "2"  # two included properties; the excluded one is hidden
    table = at.dataframe[0].value
    assert list(table["Property"]) == ["Maple Ridge Plaza", "Willow Creek Commons"]


def test_tenants_search_filters_the_list(app):
    at = app.open("views/tenants.py")
    at.text_input[0].input("harbor").run()
    assert "1 of 10 tenants" in texts(at)


def test_library_search_finds_a_tenant(app):
    at = app.open("views/library.py")
    at.text_input[0].input("Tri-County").run()
    assert "1 of 11 leases" in texts(at)


def test_import_page_lists_folders_and_needs_no_key_to_scan(app):
    at = app.open("views/import_leases.py")
    assert "2 of 2 waiting documents" in texts(at).replace("**", "")
    assert any("API key" in w.value for w in at.warning)  # AI steps are off without a key
    scan = next(b for b in at.button if b.label == "Scan folders")
    scan.click().run()
    assert not at.exception
    assert any("Scanned 3 property folders" in s.value for s in at.success)


def test_approving_an_abstract_moves_it_to_the_library(app):
    at = app.open("views/review.py")
    approve = next(b for b in at.button if b.label == "Approve")
    approve.click().run()
    assert not at.exception
    with db.connect() as conn:
        assert db.status_counts(conn)[db.STATUS_APPROVED] == 12


def test_failed_document_can_be_removed_without_touching_the_file(app):
    at = app.open("views/import_leases.py")
    assert any(sandbox.FAILED_MESSAGE in c.value for c in at.caption)
    with db.connect() as conn:
        failed = db.list_documents(conn, db.STATUS_FAILED)[0]
    next(b for b in at.button if b.label == "Remove").click().run()
    with db.connect() as conn:
        assert not db.list_documents(conn, db.STATUS_FAILED)
    assert os.path.exists(failed.stored_path)


def test_tenant_page_shows_merged_terms_with_sources(app):
    from leasedesk import portfolio

    with db.connect() as conn:
        groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
    harbor = next(g for g in groups if g.name == "Harbor Mattress")
    app.session_state["tenant_key"] = harbor.key
    at = app.open("views/tenants.py")
    page = texts(at)
    assert "Current terms" in page
    assert "Base document" in page and "Applied second" in page
    assert "from Amendment" in page  # the extended expiration and rent are labelled with their source


def test_rent_roll_page_checks_the_sandbox_sample(app):
    at = app.open("views/rent_roll.py")
    next(b for b in at.button if b.label == "Use the sandbox's sample rent roll").click().run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Rent roll lines"] == "12" and metrics["Matched to a lease"] == "9"
    issues = set(at.dataframe[0].value["Issue"])
    assert {"Missed escalation", "Rent mismatch", "Not in rent roll", "Not in LeaseDesk"} <= issues


def test_distributions_page_previews_and_records_a_distribution(app):
    from leasedesk import distributions as dist

    at = app.open("views/distributions.py")
    assert at.selectbox[0].value == "Maple Ridge Plaza Partners LLC"
    at.selectbox[0].set_value("Willow Creek Commons Owner LLC").run()
    with db.connect() as conn:
        willow = next(p for p in dist.list_partnerships(conn) if p.name.startswith("Willow"))
        before = len(dist.list_distributions(conn, willow.id))
    amount = next(n for n in at.number_input if n.label == "Amount to distribute")
    amount.set_value(100_000.0).run()
    assert not at.exception
    preview = next(d.value for d in at.dataframe if "Total" in d.value.columns and "Date" not in d.value.columns)
    assert len(preview) == 2 and round(preview["Total"].sum(), 2) == 100_000.00
    # Capital was returned by the 2025 refinancing, so new cash is residual: promote plus pro rata split.
    assert {"Promote", "Residual split"} <= set(preview.columns)
    next(b for b in at.button if b.label == "Record this distribution").click().run()
    with db.connect() as conn:
        assert len(dist.list_distributions(conn, willow.id)) == before + 1
