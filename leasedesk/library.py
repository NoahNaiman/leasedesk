"""The property library: a folder with one subfolder per property.

LeaseDesk reads the files where they are (nothing is copied). Scanning is free
and works in three steps:

1. **Scan** walks every property folder and records each file in ``file_index``
   with a decision made from its folder and file name:

   - ``lease_doc``: a current lease document (lease, amendment, extension, ...)
   - ``unclear``: a PDF whose name doesn't say what it is (e.g. ``QScan0925.pdf``)
   - ``skipped``: not a current lease document (an old tenant's folder, an
     insurance certificate, a tax bill, a photo, ...). The reason is recorded.

2. **Triage** (optional, about a tenth of a cent per file) asks a small, fast
   model to look at the first pages of each unclear PDF and decide.

3. **Add** registers the lease documents in ``lease_documents`` so they can be
   abstracted. The property comes from the folder, and the tenant from the
   tenant subfolder when there is one.

Files whose size and modification time are unchanged keep their decision on
rescan, so triage is never paid for twice and manual overrides stick.
"""

from __future__ import annotations

import base64
import io
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath

import anthropic
from pydantic import BaseModel, Field

from leasedesk import config, db, documents

DECISION_LEASE = "lease_doc"
DECISION_UNCLEAR = "unclear"
DECISION_SKIPPED = "skipped"

# Folder names (anywhere below the property folder) that never hold current lease documents.
_SKIP_FOLDER = re.compile(
    r"\b(old|former|prior|previous|archived?|insurance|coi|cam|tax|taxes|billbacks?|bill back|"
    r"partners?|partnerships?|parternship|joint venture|operating agreements?|waivers?|"
    r"photos?|pics|pictures|surveys?|title|environmental|enviro|roof\w*|plans|drawings|"
    r"ach|sales?|purchase|appraisals?|correspondence|invoices?|utilities|storm drainage|"
    r"requested items|expired|defunct|due diligence|listings?|vacated|terminated|"
    r"moved out|closed)\b",
    re.IGNORECASE,
)
# File names that point to a lease document...
_LEASE_NAME = re.compile(
    r"lease|amend|extension|extend|assign|guarant|renew|option|sublease|addendum|"
    r"modification|memorandum|commencement|rider",
    re.IGNORECASE,
)
# ...and file names that point to something else.
_OTHER_NAME = re.compile(
    r"\b(coi|acord\d*|certificate|cert|eoi|eop|invoices?|w[ -]?9|tax|taxes|estimates?|surveys?|site plan|"
    r"photos?|insurance|letters?|correspondence|ach|statements?|receipts?|proposals?|bids?|permits?|"
    r"ndas?|move ?out|inspection|abate\w*|bills?|appraisal|deed|title|check|void|summary|"
    r"contracts?|pricing|plat|flyer|brochure|marketing|loi|estoppel|snda|subordination|ltr|"
    r"reconcil\w*|50\d\d|psa|listing|right to lease|budget|om|p ?& ?l|application|drawings?|plans?|"
    r"study|measurement|storm drainage|jv|joint venture|note to file|release|termination)\b",
    re.IGNORECASE,
)
# Generic scan names: a scanned lease often gets no useful name.
_GENERIC_NAME = re.compile(r"^(qscan|scan|img|doc|document|untitled)[\s_\-\d.()]*$", re.IGNORECASE)


def _words(text: str) -> str:
    """Underscores and dashes as spaces, so "possession_of_certificate" matches word by word."""
    return re.sub(r"[_\-]+", " ", text)


@dataclass
class Classification:
    decision: str
    reason: str
    tenant_folder: str | None


def classify_path(rel_path: str) -> Classification:
    """Decide from the path alone. ``rel_path`` is relative to the property folder."""
    parts = PurePosixPath(rel_path).parts
    folders, name = parts[:-1], parts[-1]
    tenant_folder = None
    for folder in folders:
        if _SKIP_FOLDER.search(_words(folder)):
            return Classification(DECISION_SKIPPED, f"In the “{folder}” folder", None)
    if folders:
        tenant_folder = folders[0]

    if not name.lower().endswith(".pdf"):
        return Classification(DECISION_SKIPPED, "Not a PDF", tenant_folder)
    stem = _words(name.rsplit(".", 1)[0])
    lease_like, other_like = _LEASE_NAME.search(stem), _OTHER_NAME.search(stem)
    if lease_like and not other_like:
        return Classification(DECISION_LEASE, f"Name mentions “{lease_like.group(0).lower()}”",
                              tenant_folder)
    if other_like and not lease_like:
        return Classification(DECISION_SKIPPED, f"Name mentions “{other_like.group(0).lower()}”",
                              tenant_folder)
    if lease_like and other_like:
        return Classification(DECISION_UNCLEAR, "Name mentions both lease and non-lease terms", tenant_folder)
    if _GENERIC_NAME.match(stem):
        return Classification(DECISION_UNCLEAR, "Generic scan name", tenant_folder)
    return Classification(DECISION_UNCLEAR, "Name doesn't say what it is", tenant_folder)


# --------------------------------------------------------------------------- copies

# Words that mark another copy of the same document rather than a different document.
_COPY_WORDS = re.compile(r"\b(redacted|copy|signed|executed|fully|final|\w*laptop)\b|\(\d+\)", re.IGNORECASE)


def _copy_key(rel_path: str) -> str:
    p = PurePosixPath(rel_path)
    stem = _COPY_WORDS.sub(" ", _words(p.stem))
    return f"{p.parent}/" + re.sub(r"[^a-z0-9]", "", stem.lower())


def _copy_preference(rel_path: str) -> tuple:
    """Lower sorts first: signed or executed copies, then unredacted, then plain names."""
    name = PurePosixPath(rel_path).stem.lower()
    return ("signed" not in name and "executed" not in name, "redacted" in name, "copy" in name
            or "laptop" in name, len(name))


def _mark_copies(conn, property_id: int) -> None:
    """When a folder has several copies of one document, keep the best one and skip the rest."""
    rows = conn.execute("""SELECT id, rel_path FROM file_index WHERE property_id = ? AND decision = 'lease_doc'
                           AND decided_by = 'rules' AND document_id IS NULL""", (property_id,)).fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(_copy_key(r["rel_path"]), []).append(r)
    for members in groups.values():
        if len(members) < 2:
            continue
        keep, *copies = sorted(members, key=lambda r: _copy_preference(r["rel_path"]))
        for r in copies:
            conn.execute("UPDATE file_index SET decision = 'skipped', reason = ? WHERE id = ?",
                         (f"Another copy of \u201c{PurePosixPath(keep['rel_path']).name}\u201d", r["id"]))


# --------------------------------------------------------------------------- scan

@dataclass
class ScanSummary:
    properties: int = 0
    files: int = 0
    new_or_changed: int = 0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def scan(conn, root: Path, on_progress: Callable[[int, int, str], None] | None = None) -> ScanSummary:
    """Index every file under each property folder. Free: nothing is sent anywhere."""
    root = Path(root)
    summary = ScanSummary()
    folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    excluded = {p.folder_name for p in db.list_properties(conn, include_excluded=True) if p.excluded}
    for n, folder in enumerate(folders, 1):
        if on_progress:
            on_progress(n, len(folders), folder.name)
        property_id = db.upsert_property(conn, folder.name)
        summary.properties += 1
        if folder.name in excluded:
            continue
        seen = set()
        for path in folder.rglob("*"):
            if not path.is_file() or path.name.startswith(".") or path.suffix.lower() == ".lnk":
                continue
            rel = path.relative_to(root).as_posix()
            seen.add(rel)
            summary.files += 1
            stat = path.stat()
            row = conn.execute("SELECT size, mtime FROM file_index WHERE rel_path = ?", (rel,)).fetchone()
            if row and row["size"] == stat.st_size and abs(row["mtime"] - stat.st_mtime) < 1:
                continue  # unchanged: keep the earlier decision (including AI triage and manual choices)
            c = classify_path(path.relative_to(folder).as_posix())
            summary.new_or_changed += 1
            conn.execute(
                """INSERT INTO file_index (property_id, rel_path, size, mtime, tenant_folder, decision, reason,
                                           decided_by, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'rules', ?)
                   ON CONFLICT(rel_path) DO UPDATE SET
                     size = excluded.size, mtime = excluded.mtime, tenant_folder = excluded.tenant_folder,
                     decision = excluded.decision, reason = excluded.reason, decided_by = 'rules',
                     doc_kind = NULL, tenant_hint = NULL, updated_at = excluded.updated_at""",
                (property_id, rel, stat.st_size, stat.st_mtime, c.tenant_folder, c.decision, c.reason, _now()),
            )
        _mark_copies(conn, property_id)
        # Forget files that were deleted or moved out of this property folder.
        for row in conn.execute("SELECT rel_path FROM file_index WHERE property_id = ? AND document_id IS NULL",
                                (property_id,)).fetchall():
            if row["rel_path"] not in seen:
                conn.execute("DELETE FROM file_index WHERE rel_path = ?", (row["rel_path"],))
        conn.commit()
    return summary


@dataclass
class PropertyFiles:
    property: db.Property
    files: int
    lease_docs: int
    unclear: int
    skipped: int
    added: int


def property_file_counts(conn) -> list[PropertyFiles]:
    rows = {r["property_id"]: r for r in conn.execute(
        """SELECT property_id, COUNT(*) AS files,
                  SUM(decision = 'lease_doc') AS lease_docs, SUM(decision = 'unclear') AS unclear,
                  SUM(decision = 'skipped') AS skipped, SUM(document_id IS NOT NULL) AS added
           FROM file_index GROUP BY property_id""").fetchall()}
    out = []
    for p in db.list_properties(conn, include_excluded=True):
        r = rows.get(p.id)
        out.append(PropertyFiles(p, *(int(r[k] or 0) if r else 0
                                      for k in ("files", "lease_docs", "unclear", "skipped", "added"))))
    return out


def files_for_property(conn, property_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, rel_path, tenant_folder, decision, reason, doc_kind, tenant_hint, decided_by, document_id
           FROM file_index WHERE property_id = ? ORDER BY rel_path COLLATE NOCASE""", (property_id,)).fetchall()
    return [dict(r) for r in rows]


def set_decision(conn, file_id: int, decision: str, reason: str = "Set by hand") -> None:
    conn.execute("UPDATE file_index SET decision = ?, reason = ?, decided_by = 'manual', updated_at = ? "
                 "WHERE id = ?", (decision, reason, _now(), file_id))


# --------------------------------------------------------------------------- AI triage

TRIAGE_MODEL = "claude-haiku-5-5"
TRIAGE_PAGES = 3
TRIAGE_INPUT_PRICE_PER_MTOK = 0.10
TRIAGE_OUTPUT_PRICE_PER_MTOK = 0.50
# Rough tokens for triaging one file (three pages as text and images, plus the answer).
TRIAGE_TOKENS_ESTIMATE = (12_000, 300)


class DocKind(str, Enum):
    LEASE = "lease"
    AMENDMENT = "amendment"
    EXTENSION_OR_RENEWAL = "extension_or_renewal"
    OPTION_EXERCISE = "option_exercise"
    ASSIGNMENT = "assignment"
    GUARANTY = "guaranty"
    SUBLEASE = "sublease"
    TERMINATION = "termination"
    PARTIAL_PAGES = "partial_pages"
    OTHER = "other_not_a_lease_document"


# Kinds that are abstracted. A termination ends a lease, and loose pages of a lease are
# usually duplicates of a complete copy, so neither becomes its own lease record.
LEASE_KINDS = {k for k in DocKind if k not in (DocKind.OTHER, DocKind.TERMINATION, DocKind.PARTIAL_PAGES)}


class TriageResult(BaseModel):
    kind: DocKind = Field(description="What this document is.")
    tenant_name: str = Field(description="The tenant it concerns, or an empty string if none is named.")
    summary: str = Field(description="One short sentence describing the document.")


TRIAGE_PROMPT = """These are the first pages of a file from a commercial property manager's records.
Classify the document. Lease documents are: the lease itself, an amendment, an extension or renewal,
a tenant's exercise of an option, an assignment of the lease, a guaranty of the lease, or a sublease.
Use "termination" for an agreement that ends a lease, and "partial_pages" when the file is only a page
or a few loose pages taken from the middle or end of a lease rather than a complete document.
Everything else (insurance certificates, tax bills, CAM statements, invoices, general letters, surveys,
estoppels, partnership papers, ...) is "other_not_a_lease_document"."""


def estimate_triage_cost(n_files: int) -> float:
    i, o = TRIAGE_TOKENS_ESTIMATE
    return n_files * (i * TRIAGE_INPUT_PRICE_PER_MTOK + o * TRIAGE_OUTPUT_PRICE_PER_MTOK) / 1_000_000


def first_pages_pdf(path: Path, pages: int = TRIAGE_PAGES) -> bytes:
    """A small PDF of just the first few pages, so triage doesn't pay to read the whole file."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(path))
    writer = PdfWriter()
    for page in reader.pages[:pages]:
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def triage_file(client: anthropic.Anthropic, path: Path) -> tuple[TriageResult, int, int]:
    pdf = base64.standard_b64encode(first_pages_pdf(path)).decode("ascii")
    response = client.messages.create(
        model=TRIAGE_MODEL,
        max_tokens=1024,
        thinking={"type": "disabled"},
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": anthropic.transform_schema(TriageResult)}},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf}},
            {"type": "text", "text": TRIAGE_PROMPT},
        ]}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"stopped early ({response.stop_reason})")
    text = "".join(b.text for b in response.content if b.type == "text")
    return TriageResult.model_validate(json.loads(text)), response.usage.input_tokens, response.usage.output_tokens


def triage(conn, client: anthropic.Anthropic, root: Path, property_ids: list[int], *,
           max_workers: int = 8, on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    """Classify every unclear PDF in the given properties. Returns counts and the actual cost."""
    marks = ",".join("?" * len(property_ids)) or "NULL"
    rows = conn.execute(f"SELECT id, rel_path FROM file_index WHERE decision = 'unclear' "
                        f"AND property_id IN ({marks})", property_ids).fetchall()
    stats = {"lease_docs": 0, "other": 0, "errors": 0, "cost": 0.0}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(triage_file, client, Path(root) / r["rel_path"]): r for r in rows}
        for n, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            try:
                result, tin, tout = future.result()
                stats["cost"] += (tin * TRIAGE_INPUT_PRICE_PER_MTOK + tout * TRIAGE_OUTPUT_PRICE_PER_MTOK) / 1e6
                is_lease = result.kind in LEASE_KINDS
                stats["lease_docs" if is_lease else "other"] += 1
                conn.execute(
                    """UPDATE file_index SET decision = ?, reason = ?, doc_kind = ?, tenant_hint = ?,
                       decided_by = 'ai', updated_at = ? WHERE id = ?""",
                    (DECISION_LEASE if is_lease else DECISION_SKIPPED, f"AI: {result.summary}",
                     result.kind.value, result.tenant_name or None, _now(), row["id"]))
            except Exception as exc:  # keep going; the file stays "unclear" with the error noted
                stats["errors"] += 1
                conn.execute("UPDATE file_index SET reason = ? WHERE id = ?",
                             (f"AI check failed: {str(exc)[:200]}", row["id"]))
            conn.commit()
            if on_progress:
                on_progress(n, len(rows), row["rel_path"])
    return stats


# --------------------------------------------------------------------------- add to LeaseDesk

@dataclass
class AddSummary:
    added: int = 0
    already_present: int = 0
    errors: list[tuple[str, str]] = None

    def __post_init__(self):
        self.errors = self.errors or []


def add_lease_documents(conn, root: Path, property_ids: list[int],
                        on_progress: Callable[[int, int, str], None] | None = None) -> AddSummary:
    """Register every lease document found in these properties, reading the files in place."""
    marks = ",".join("?" * len(property_ids)) or "NULL"
    rows = conn.execute(f"SELECT * FROM file_index WHERE decision = 'lease_doc' AND document_id IS NULL "
                        f"AND property_id IN ({marks}) ORDER BY rel_path", property_ids).fetchall()
    out = AddSummary()
    for n, row in enumerate(rows, 1):
        path = Path(root) / row["rel_path"]
        if on_progress:
            on_progress(n, len(rows), row["rel_path"])
        try:
            data = path.read_bytes()
            if not data.startswith(b"%PDF"):
                raise ValueError("Not a readable PDF.")
            if len(data) > config.MAX_PDF_BYTES:
                raise ValueError("Larger than 300 MB; split it into smaller PDFs.")
            info = documents.inspect_pdf(path)
            if info.page_count > config.MAX_PDF_PAGES:
                raise ValueError(f"{info.page_count} pages; the limit is {config.MAX_PDF_PAGES}.")
        except Exception as exc:
            out.errors.append((row["rel_path"], str(exc)))
            conn.execute("UPDATE file_index SET reason = ? WHERE id = ?", (f"Could not add: {exc}", row["id"]))
            continue
        sha = documents.sha256_bytes(data)
        existing = db.find_document_by_sha(conn, sha)
        if existing:
            # Same file already in LeaseDesk (uploaded earlier, or a copy in another folder).
            if existing.property_id is None:
                db.attach_document(conn, existing.id, property_id=row["property_id"],
                                   tenant_folder=row["tenant_folder"], source_rel_path=row["rel_path"])
            doc_id = existing.id
            out.already_present += 1
        else:
            doc_id = db.add_document(conn, file_name=path.name, file_sha256=sha, stored_path=str(path),
                                     page_count=info.page_count, is_scanned=info.is_scanned,
                                     property_id=row["property_id"], tenant_folder=row["tenant_folder"],
                                     source_rel_path=row["rel_path"])
            out.added += 1
        conn.execute("UPDATE file_index SET document_id = ? WHERE id = ?", (doc_id, row["id"]))
        conn.commit()
    return out
