from datetime import date

from leasedesk import db, documents
from leasedesk.models import LeaseAbstract, RentStep


def test_import_detects_text_pdf_and_skips_duplicates(conn, sample_pdf):
    first = documents.import_pdf_path(conn, sample_pdf)
    assert first.doc_id is not None and first.error is None

    record = db.get_document(conn, first.doc_id)
    assert record.page_count == 2
    assert record.is_scanned is False
    assert record.status == db.STATUS_PENDING

    again = documents.import_pdf_path(conn, sample_pdf)
    assert again.duplicate and again.doc_id is None


def test_rejects_non_pdf(conn):
    result = documents.import_pdf_bytes(conn, "notes.txt", b"hello")
    assert result.error == "Not a PDF file."


def test_review_keeps_original_ai_draft(conn, sample_pdf):
    doc_id = documents.import_pdf_path(conn, sample_pdf).doc_id
    draft = LeaseAbstract(tenant_name="Acme Coffee LLC", square_feet=1200)
    db.save_ai_abstract(conn, doc_id, draft, input_tokens=1000, output_tokens=200)
    assert db.get_document(conn, doc_id).status == db.STATUS_REVIEW

    corrected = draft.model_copy(update={"square_feet": 1250})
    db.save_review(conn, doc_id, corrected, approve=True)

    record = db.get_document(conn, doc_id)
    assert record.status == db.STATUS_APPROVED
    assert record.abstract.square_feet == 1250
    assert record.ai_abstract.square_feet == 1200


def test_current_monthly_rent_picks_active_step():
    abstract = LeaseAbstract(base_rent_schedule=[
        RentStep(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31), monthly_rent=5000),
        RentStep(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), monthly_rent=5150),
    ])
    assert abstract.current_monthly_rent(date(2026, 3, 1)) == 5150
    assert abstract.current_monthly_rent(date(2030, 1, 1)) is None


def test_oversized_pdfs_are_sent_as_a_smaller_copy(tmp_path, monkeypatch):
    import io

    import pdfplumber

    from leasedesk import config, demo_data, documents

    lease = demo_data.build_demo_portfolio()[0]
    path = tmp_path / "big.pdf"
    path.write_bytes(lease.pdf_bytes)
    assert documents.pdf_for_api(path) == lease.pdf_bytes  # small files are sent as they are

    monkeypatch.setattr(config, "MAX_SEND_BYTES", len(lease.pdf_bytes) - 1)
    smaller = documents.pdf_for_api(path)
    assert smaller != lease.pdf_bytes and smaller.startswith(b"%PDF")
    with pdfplumber.open(io.BytesIO(smaller)) as pdf:
        assert len(pdf.pages) == documents.inspect_pdf(path).page_count
    assert path.read_bytes() == lease.pdf_bytes  # the original file is untouched


def test_a_pdf_whose_pages_one_reader_cant_find_is_repaired_before_shrinking(monkeypatch):
    """Some scanned files have a damaged page index: pdfplumber finds no pages, pypdf finds them all."""
    import io

    import pdfplumber

    from leasedesk import demo_data, documents

    damaged = demo_data.build_demo_portfolio()[0].pdf_bytes
    real_open = pdfplumber.open

    class NoPages:
        pages = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_(source, *args, **kwargs):
        data = source.getvalue() if isinstance(source, io.BytesIO) else None
        return NoPages() if data == damaged else real_open(source, *args, **kwargs)

    monkeypatch.setattr(documents.pdfplumber, "open", open_)
    repaired = documents.repair_pdf(damaged)
    assert repaired != damaged
    with real_open(io.BytesIO(repaired)) as pdf:
        assert len(pdf.pages) == 4
    assert documents.shrink_pdf(damaged).startswith(b"%PDF")
    monkeypatch.undo()
    assert documents.repair_pdf(damaged) == damaged  # healthy files are left alone
