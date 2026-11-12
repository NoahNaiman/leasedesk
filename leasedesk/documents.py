"""Importing PDF files into the document store.

Files are copied into ``data/documents`` under their SHA-256 hash, so the
same lease uploaded twice (even under a different name) is only stored and
abstracted once.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from leasedesk import config, db

# A page with fewer extractable characters than this is treated as an image (scanned).
_MIN_TEXT_CHARS_PER_PAGE = 50


@dataclass
class PdfInfo:
    page_count: int
    is_scanned: bool


@dataclass
class ImportResult:
    file_name: str
    doc_id: int | None
    duplicate: bool = False
    error: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_pdf(path: Path) -> PdfInfo:
    """Count pages and guess whether the PDF is scanned (no text layer)."""
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages
        sample = pages[: min(len(pages), 5)]
        text_pages = sum(
            1 for p in sample if len((p.extract_text() or "").strip()) >= _MIN_TEXT_CHARS_PER_PAGE
        )
        return PdfInfo(page_count=len(pages), is_scanned=text_pages < max(1, len(sample) // 2))


def shrink_pdf(data: bytes, resolution: int = 110) -> bytes:
    """A copy of the PDF as grayscale page images, small enough to send to the API.

    Used only for very large files (usually high-resolution scans). The text layer is dropped,
    which Claude doesn't need: it reads the page images.
    """
    import io

    pages = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            pages.append(page.to_image(resolution=resolution).original.convert("L"))
    out = io.BytesIO()
    pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:], resolution=resolution)
    return out.getvalue()


def pdf_for_api(path: Path) -> bytes:
    """The PDF bytes to send for abstraction: the file itself, or a shrunken copy if it's too big.
    The original file is never changed."""
    data = Path(path).read_bytes()
    if len(data) <= config.MAX_SEND_BYTES:
        return data
    for resolution in (110, 80):
        smaller = shrink_pdf(data, resolution)
        if len(smaller) <= config.MAX_SEND_BYTES:
            return smaller
    return smaller  # still too big: the API will say so, and the error is shown for this file


def import_pdf_bytes(conn, file_name: str, data: bytes, *, is_demo: bool = False) -> ImportResult:
    """Store an uploaded PDF and register it in the database."""
    if not data.startswith(b"%PDF"):
        return ImportResult(file_name, None, error="Not a PDF file.")
    if len(data) > config.MAX_PDF_BYTES:
        return ImportResult(file_name, None, error="File is larger than 300 MB. Split it into smaller PDFs.")

    config.ensure_data_dirs()
    digest = sha256_bytes(data)
    stored = config.DOCUMENTS_DIR / f"{digest}.pdf"
    if not stored.exists():
        stored.write_bytes(data)

    try:
        info = inspect_pdf(stored)
    except Exception as exc:  # malformed or encrypted PDFs
        return ImportResult(file_name, None, error=f"Could not read PDF: {exc}")

    if info.page_count > config.MAX_PDF_PAGES:
        return ImportResult(file_name, None, error=f"PDF has {info.page_count} pages; the limit is 600.")

    doc_id = db.add_document(
        conn,
        file_name=file_name,
        file_sha256=digest,
        stored_path=str(stored),
        page_count=info.page_count,
        is_scanned=info.is_scanned,
        is_demo=is_demo,
    )
    if doc_id is None:
        return ImportResult(file_name, None, duplicate=True)
    return ImportResult(file_name, doc_id)


def import_pdf_path(conn, path: Path, *, is_demo: bool = False) -> ImportResult:
    return import_pdf_bytes(conn, path.name, path.read_bytes(), is_demo=is_demo)


def import_folder(conn, folder: Path) -> list[ImportResult]:
    """Import every PDF in a folder and its subfolders."""
    return [import_pdf_path(conn, p) for p in sorted(folder.rglob("*.pdf")) if p.is_file()]


def remove_stored_file(path: str) -> None:
    p = Path(path)
    if p.exists() and config.DOCUMENTS_DIR in p.parents:
        p.unlink()


def copy_to(path: str, dest: Path) -> None:
    shutil.copy2(path, dest)
