"""The sandbox is a fictional workspace whose every file says what the scanner should decide.

These tests run the real scanner, grouping, and portfolio code over it end to end.
"""

from datetime import date

import pytest

from leasedesk import db, library, portfolio, sandbox

TODAY = date(2026, 11, 11)


@pytest.fixture(scope="module")
def sb(tmp_path_factory):
    return sandbox.build_sandbox(tmp_path_factory.mktemp("sandbox"), TODAY)


@pytest.fixture
def sbconn(sb):
    with db.connect(sb.data_dir / "leasedesk.sqlite") as conn:
        yield conn


def test_scanner_matches_the_answer_key(sb, sbconn):
    rows = {r["rel_path"]: r for r in sbconn.execute("SELECT * FROM file_index")}
    for f in sb.files:
        row = rows.get(f.rel_path)
        if f.expected == "excluded":
            assert row is None, f.rel_path
            continue
        # Unclear files were then triaged, so compare the scanner's own call via the reason.
        decision = "unclear" if row["decided_by"] == "ai" else row["decision"]
        assert decision == f.expected, f"{f.rel_path}: {row['reason']}"
        if f.expected == library.DECISION_LEASE:
            assert row["tenant_folder"] == f.tenant_folder, f.rel_path


def test_every_review_state_is_present(sbconn):
    assert db.status_counts(sbconn) == {"approved": 11, "needs_review": 2, "pending": 2, "failed": 1}
    failed = db.list_documents(sbconn, db.STATUS_FAILED)[0]
    assert failed.error == sandbox.FAILED_MESSAGE


def test_properties_use_display_names_and_hide_excluded(sbconn):
    names = {p.display_name for p in db.list_properties(sbconn)}
    assert names == {"Willow Creek Commons", "Maple Ridge Plaza"}
    folders = {d.property_name for d in db.list_documents(sbconn)}
    assert folders == names


def test_amendment_extends_tenant_in_portfolio_views(sbconn):
    groups = portfolio.tenant_groups(db.list_documents(sbconn, db.STATUS_APPROVED))
    harbor = next(g for g in groups if g.name == "Harbor Mattress")
    assert len(harbor.records) == 2
    lease = next(r for r in harbor.records if r.abstract.document_type.value == "lease")
    amendment = next(r for r in harbor.records if r.abstract.document_type.value == "amendment")
    assert harbor.primary.id == lease.id
    row = portfolio.tenant_table([harbor], TODAY).iloc[0]
    assert row["Expiration"] == amendment.abstract.expiration_date > lease.abstract.expiration_date
    assert row["Status"] == portfolio.STATUS_ACTIVE


def test_tables_are_grouped_by_property(sbconn):
    approved = db.list_documents(sbconn, db.STATUS_APPROVED)
    groups = portfolio.tenant_groups(approved)
    props = portfolio.property_table(groups, TODAY)
    assert list(props["Property"]) == ["Maple Ridge Plaza", "Willow Creek Commons"]
    assert props["Tenants"].sum() == len(groups) == 10
    leases = portfolio.lease_table(approved, TODAY)
    assert list(leases["Property"]) == sorted(leases["Property"])


def test_rebuilding_starts_fresh(tmp_path):
    first = sandbox.build_sandbox(tmp_path, TODAY)
    with db.connect(first.data_dir / "leasedesk.sqlite") as conn:
        db.delete_document(conn, db.list_documents(conn)[0].id)
    second = sandbox.build_sandbox(tmp_path, TODAY)
    with db.connect(second.data_dir / "leasedesk.sqlite") as conn:
        assert sum(db.status_counts(conn).values()) == 16


def test_sandbox_never_has_an_api_key(sb):
    assert "ANTHROPIC_API_KEY" not in (sb.data_dir / "settings.env").read_text()
