"""SQLite storage for lease documents and their abstracts.

One row per uploaded PDF. The AI's first draft is kept in ``ai_abstract_json``
and never overwritten, while ``abstract_json`` holds the reviewed version.
Keeping both lets us measure how often the AI needed correcting.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from leasedesk import config
from leasedesk.models import LeaseAbstract

STATUS_PENDING = "pending"      # uploaded, not yet abstracted
STATUS_REVIEW = "needs_review"  # abstracted by AI, waiting for a person
STATUS_APPROVED = "approved"    # reviewed and approved
STATUS_FAILED = "failed"        # abstraction failed; see error column

SCHEMA = """
CREATE TABLE IF NOT EXISTS lease_documents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name        TEXT NOT NULL,
    file_sha256      TEXT NOT NULL UNIQUE,
    stored_path      TEXT NOT NULL,
    page_count       INTEGER,
    is_scanned       INTEGER,
    status           TEXT NOT NULL,
    ai_abstract_json TEXT,
    abstract_json    TEXT,
    error            TEXT,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    is_demo          INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    abstracted_at    TEXT,
    reviewed_at      TEXT
);
"""


@dataclass
class LeaseRecord:
    id: int
    file_name: str
    file_sha256: str
    stored_path: str
    page_count: int | None
    is_scanned: bool | None
    status: str
    ai_abstract: LeaseAbstract | None
    abstract: LeaseAbstract | None
    error: str | None
    input_tokens: int | None
    output_tokens: int | None
    is_demo: bool
    created_at: str
    abstracted_at: str | None
    reviewed_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "LeaseRecord":
        def parse(text: str | None) -> LeaseAbstract | None:
            return LeaseAbstract.model_validate_json(text) if text else None

        return cls(
            id=row["id"],
            file_name=row["file_name"],
            file_sha256=row["file_sha256"],
            stored_path=row["stored_path"],
            page_count=row["page_count"],
            is_scanned=None if row["is_scanned"] is None else bool(row["is_scanned"]),
            status=row["status"],
            ai_abstract=parse(row["ai_abstract_json"]),
            abstract=parse(row["abstract_json"]),
            error=row["error"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            is_demo=bool(row["is_demo"]),
            created_at=row["created_at"],
            abstracted_at=row["abstracted_at"],
            reviewed_at=row["reviewed_at"],
        )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_document(
    conn: sqlite3.Connection,
    *,
    file_name: str,
    file_sha256: str,
    stored_path: str,
    page_count: int | None,
    is_scanned: bool | None,
    is_demo: bool = False,
) -> int | None:
    """Insert a new document. Returns its id, or None if it was already imported."""
    try:
        cur = conn.execute(
            """INSERT INTO lease_documents
               (file_name, file_sha256, stored_path, page_count, is_scanned, status, is_demo, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_name, file_sha256, stored_path, page_count,
             None if is_scanned is None else int(is_scanned), STATUS_PENDING, int(is_demo), _now()),
        )
    except sqlite3.IntegrityError:
        return None
    return cur.lastrowid


def save_ai_abstract(
    conn: sqlite3.Connection,
    doc_id: int,
    abstract: LeaseAbstract,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    approved: bool = False,
) -> None:
    payload = abstract.model_dump_json()
    conn.execute(
        """UPDATE lease_documents
           SET ai_abstract_json = ?, abstract_json = ?, status = ?, error = NULL,
               input_tokens = ?, output_tokens = ?, abstracted_at = ?, reviewed_at = ?
           WHERE id = ?""",
        (payload, payload, STATUS_APPROVED if approved else STATUS_REVIEW,
         input_tokens, output_tokens, _now(), _now() if approved else None, doc_id),
    )


def save_review(conn: sqlite3.Connection, doc_id: int, abstract: LeaseAbstract, *, approve: bool) -> None:
    """Save a person's edits. Approving moves the lease into the live library."""
    conn.execute(
        """UPDATE lease_documents SET abstract_json = ?, status = ?, reviewed_at = ? WHERE id = ?""",
        (abstract.model_dump_json(), STATUS_APPROVED if approve else STATUS_REVIEW,
         _now() if approve else None, doc_id),
    )


def mark_failed(conn: sqlite3.Connection, doc_id: int, error: str) -> None:
    conn.execute(
        "UPDATE lease_documents SET status = ?, error = ? WHERE id = ?",
        (STATUS_FAILED, error, doc_id),
    )


def reset_to_pending(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute(
        "UPDATE lease_documents SET status = ?, error = NULL WHERE id = ?",
        (STATUS_PENDING, doc_id),
    )


def delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute("DELETE FROM lease_documents WHERE id = ?", (doc_id,))


def get_document(conn: sqlite3.Connection, doc_id: int) -> LeaseRecord | None:
    row = conn.execute("SELECT * FROM lease_documents WHERE id = ?", (doc_id,)).fetchone()
    return LeaseRecord.from_row(row) if row else None


def list_documents(conn: sqlite3.Connection, status: str | None = None) -> list[LeaseRecord]:
    if status:
        rows = conn.execute(
            "SELECT * FROM lease_documents WHERE status = ? ORDER BY id", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM lease_documents ORDER BY id").fetchall()
    return [LeaseRecord.from_row(r) for r in rows]


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM lease_documents GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def delete_demo_documents(conn: sqlite3.Connection) -> list[str]:
    """Remove demo rows and return their stored file paths so the caller can delete them."""
    paths = [r["stored_path"] for r in conn.execute(
        "SELECT stored_path FROM lease_documents WHERE is_demo = 1").fetchall()]
    conn.execute("DELETE FROM lease_documents WHERE is_demo = 1")
    return paths
