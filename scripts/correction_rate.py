"""Report how often reviewers corrected the AI on approved abstracts.

    .venv/bin/python scripts/correction_rate.py

Reads the LeaseDesk database (LEASEDESK_DATA_DIR, default ./data). Free: no API calls.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leasedesk import corrections, db  # noqa: E402

with db.connect() as conn:
    report = corrections.correction_report(db.list_documents(conn, db.STATUS_APPROVED))

if not report.documents:
    print("No approved documents with an AI draft yet.")
    raise SystemExit(0)
print(f"Approved documents compared: {report.documents}")
print(f"Fields corrected: {report.fields_corrected} of {report.fields} ({report.rate:.1%})")
print(f"Documents with at least one correction: {report.documents_corrected} ({report.document_rate:.0%})")
for name, n, total in report.most_corrected(10):
    print(f"  {name}: {n} of {total}")
