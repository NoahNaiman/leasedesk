"""Build (or rebuild) the sandbox: fictional property folders plus a LeaseDesk database.

    .venv/bin/python scripts/build_sandbox.py [folder]

The default folder is ./sandbox. Open the app on it with "Start LeaseDesk Sandbox.command",
or run:  LEASEDESK_DATA_DIR=sandbox/data .venv/bin/streamlit run app/main.py
Your real data in ./data is never touched.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leasedesk import db, sandbox  # noqa: E402

folder = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "sandbox")
sb = sandbox.build_sandbox(folder)
with db.connect(sb.data_dir / "leasedesk.sqlite") as conn:
    counts = db.status_counts(conn)
print(f"Sandbox ready in {folder}")
print(f"  {len(sb.files)} files in {sb.library_root}")
print("  documents: " + ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in sorted(counts.items())))
