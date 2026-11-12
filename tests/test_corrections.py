from datetime import date

from leasedesk import corrections, db, demo_data


def test_correction_rate_compares_ai_draft_with_approved_version(conn):
    demo_data.load_demo_into_db(conn, date(2026, 11, 11))
    approved = db.list_documents(conn, db.STATUS_APPROVED)
    # Untouched approvals: the reviewer accepted the AI draft as is.
    report = corrections.correction_report(approved)
    assert report.documents == len(approved) == 10
    assert (report.fields_corrected, report.rate, report.document_rate) == (0, 0.0, 0.0)

    # A reviewer fixes the square footage on one lease and the expiration on another.
    a, b = approved[0], approved[1]
    db.save_review(conn, a.id, a.abstract.model_copy(update={"square_feet": a.abstract.square_feet + 100}),
                   approve=True)
    db.save_review(conn, b.id, b.abstract.model_copy(update={"expiration_date": date(2040, 1, 31)}), approve=True)
    report = corrections.correction_report(db.list_documents(conn, db.STATUS_APPROVED))
    assert report.fields_corrected == 2 and report.documents_corrected == 2
    assert report.document_rate == 2 / 10
    assert report.rate == 2 / report.fields
    assert {f for f, _, _ in report.most_corrected()} == {"square_feet", "expiration_date"}


def test_documents_without_an_ai_draft_or_approval_are_ignored(conn):
    demo_data.load_demo_into_db(conn, date(2026, 11, 11))
    report = corrections.correction_report(db.list_documents(conn))  # includes 2 still in review
    assert report.documents == 10
    assert corrections.correction_report([]).rate == 0.0
