"""Property-folder scanning: rules, rescans, and adding lease documents in place."""

import pytest

from leasedesk import db, demo_data, library, portfolio


@pytest.mark.parametrize("path, decision, tenant", [
    ("Saguaro Grill/Third Lease Amendment FINAL.pdf", "lease_doc", "Saguaro Grill"),
    ("88 Linden Avenue Mario's Pizza Lease .pdf", "lease_doc", None),
    ("Exercise of Option to Renew 88 Linden Avenue 1.28.21.pdf", "lease_doc", None),
    ("Garnet Nail Spa/Leases and Amendments/Second Amendment.pdf", "lease_doc", "Garnet Nail Spa"),
    ("Old Leases/Metro Wireless/Metro Wireless LLC Lease.pdf", "skipped", None),
    ("Expired Leases/Dependable Cleaners/Lease.pdf", "skipped", None),
    ("Northside Tire Shop/Insurance/COI 23-24.pdf", "skipped", None),
    ("Northside Tire Shop/CAM/2023 5004-Marco.pdf", "skipped", None),
    ("Partnership Agreement/Joint Venture Agreement 2-18-1987.pdf", "skipped", None),
    ("Willow Tea House/possession_of_property_certificate_signed.pdf", "skipped", "Willow Tea House"),
    ("Hilltop Pizza Insurance Letter.pdf", "skipped", None),
    ("Marco Letter.docx", "skipped", None),
    ("QScan09252023_134354.pdf", "unclear", None),
    ("Lease Extension Letter.pdf", "unclear", None),
])
def test_classify_path(path, decision, tenant):
    c = library.classify_path(path)
    assert (c.decision, c.tenant_folder) == (decision, tenant)


@pytest.fixture
def library_root(tmp_path):
    """Two property folders built from demo PDFs, with some non-lease files mixed in."""
    leases = demo_data.build_demo_portfolio()
    root = tmp_path / "Properties"
    (root / "100 Main Street" / "FreshWay Market").mkdir(parents=True)
    (root / "100 Main Street" / "FreshWay Market" / "FreshWay Lease.pdf").write_bytes(leases[0].pdf_bytes)
    (root / "100 Main Street" / "FreshWay Market" / "Insurance").mkdir()
    (root / "100 Main Street" / "FreshWay Market" / "Insurance" / "COI.pdf").write_bytes(b"%PDF-1.4 x")
    (root / "100 Main Street" / "Old Leases").mkdir()
    (root / "100 Main Street" / "Old Leases" / "Gone Lease.pdf").write_bytes(leases[1].pdf_bytes)
    (root / "100 Main Street" / "QScan0001.pdf").write_bytes(leases[2].pdf_bytes)
    (root / "200 Oak Avenue").mkdir()
    (root / "200 Oak Avenue" / "Oak Lease.pdf").write_bytes(leases[3].pdf_bytes)
    (root / "200 Oak Avenue" / "site photo.jpg").write_bytes(b"jpg")
    return root


def test_scan_and_add_reads_files_in_place(conn, library_root):
    summary = library.scan(conn, library_root)
    assert (summary.properties, summary.files) == (2, 6)
    counts = {c.property.folder_name: c for c in library.property_file_counts(conn)}
    main = counts["100 Main Street"]
    assert (main.lease_docs, main.unclear, main.skipped) == (1, 1, 2)

    ids = [c.property.id for c in counts.values()]
    added = library.add_lease_documents(conn, library_root, ids)
    assert added.added == 2 and not added.errors
    docs = db.list_documents(conn)
    freshway = next(d for d in docs if d.file_name == "FreshWay Lease.pdf")
    assert freshway.stored_path == str(library_root / "100 Main Street" / "FreshWay Market" / "FreshWay Lease.pdf")
    assert freshway.property_name == "100 Main Street"
    assert freshway.tenant_folder == "FreshWay Market"
    from leasedesk import config
    assert not list(config.DOCUMENTS_DIR.glob("*.pdf"))  # nothing was copied


def test_rescan_keeps_decisions_and_manual_overrides(conn, library_root):
    library.scan(conn, library_root)
    prop = next(p for p in db.list_properties(conn) if p.folder_name == "100 Main Street")
    qscan = next(f for f in library.files_for_property(conn, prop.id) if "QScan" in f["rel_path"])
    library.set_decision(conn, qscan["id"], library.DECISION_LEASE)
    assert library.scan(conn, library_root).new_or_changed == 0
    qscan = next(f for f in library.files_for_property(conn, prop.id) if "QScan" in f["rel_path"])
    assert (qscan["decision"], qscan["decided_by"]) == ("lease_doc", "manual")


def test_excluded_and_renamed_properties(conn, library_root):
    library.scan(conn, library_root)
    oak = next(p for p in db.list_properties(conn) if p.folder_name == "200 Oak Avenue")
    main = next(p for p in db.list_properties(conn) if p.folder_name == "100 Main Street")
    library.add_lease_documents(conn, library_root, [oak.id, main.id])

    db.rename_property(conn, main.id, "Main Street Center")
    assert {d.property_name for d in db.list_documents(conn)} == {"Main Street Center", "200 Oak Avenue"}

    db.set_property_excluded(conn, oak.id, True)
    assert {d.property_name for d in db.list_documents(conn)} == {"Main Street Center"}
    assert [p.folder_name for p in db.list_properties(conn)] == ["100 Main Street"]


def test_existing_upload_is_linked_not_duplicated(conn, library_root):
    from leasedesk import documents

    pdf = (library_root / "200 Oak Avenue" / "Oak Lease.pdf").read_bytes()
    uploaded = documents.import_pdf_bytes(conn, "uploaded.pdf", pdf).doc_id
    library.scan(conn, library_root)
    oak = next(p for p in db.list_properties(conn) if p.folder_name == "200 Oak Avenue")
    result = library.add_lease_documents(conn, library_root, [oak.id])
    assert (result.added, result.already_present) == (0, 1)
    assert db.get_document(conn, uploaded).property_id == oak.id


def test_triage_cost_estimate_is_small():
    assert library.estimate_triage_cost(125) < 0.50


def test_removing_a_document_keeps_the_file_and_stays_removed(conn, library_root):
    library.scan(conn, library_root)
    oak = next(p for p in db.list_properties(conn) if p.folder_name == "200 Oak Avenue")
    library.add_lease_documents(conn, library_root, [oak.id])
    doc = db.list_documents(conn)[0]
    db.delete_document(conn, doc.id)
    assert (library_root / "200 Oak Avenue" / "Oak Lease.pdf").exists()
    library.scan(conn, library_root)
    assert library.add_lease_documents(conn, library_root, [oak.id]).added == 0


def test_tenant_folder_groups_documents_with_different_legal_names(conn, library_root):
    from leasedesk.models import LeaseAbstract

    library.scan(conn, library_root)
    main = next(p for p in db.list_properties(conn) if p.folder_name == "100 Main Street")
    prop_files = library.files_for_property(conn, main.id)
    qscan = next(f for f in prop_files if "QScan" in f["rel_path"])
    library.set_decision(conn, qscan["id"], library.DECISION_LEASE)
    library.add_lease_documents(conn, library_root, [main.id])
    # Pretend the QScan was an assignment filed in FreshWay's folder under another legal name.
    conn.execute("UPDATE lease_documents SET tenant_folder = 'FreshWay Market' WHERE file_name = 'QScan0001.pdf'")
    for d in db.list_documents(conn):
        name = "FreshWay Markets, Inc." if d.file_name.startswith("FreshWay") else "FW Holdings LLC"
        db.save_review(conn, d.id, LeaseAbstract(tenant_name=name, trade_name="FreshWay Market"), approve=True)
    groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
    assert len(groups) == 1 and len(groups[0].records) == 2
    assert groups[0].property == "100 Main Street"
