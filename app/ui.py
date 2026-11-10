"""Small helpers shared by the Streamlit pages."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from leasedesk import db


def missing(x) -> bool:
    """True for None and for pandas' NaN/NaT blanks."""
    return x is None or bool(pd.isna(x))


def number(x: float | None) -> str:
    return "—" if missing(x) else f"{x:,.0f}"


def money(x: float | None, cents: bool = False) -> str:
    if missing(x):
        return "—"
    return f"${x:,.2f}" if cents else f"${x:,.0f}"


def money_short(x: float | None) -> str:
    """Compact dollars for headline numbers: $142.3K, $1.25M."""
    if missing(x):
        return "—"
    if abs(x) >= 1_000_000:
        return f"${x / 1_000_000:,.2f}M"
    if abs(x) >= 10_000:
        return f"${x / 1_000:,.1f}K"
    return f"${x:,.0f}"


def pct(x: float | None) -> str:
    return "—" if missing(x) else f"{x:g}%"


def fmt_date(d: date | None) -> str:
    return "—" if missing(d) else d.strftime("%b %d, %Y")


def records(status: str | None = None) -> list[db.LeaseRecord]:
    with db.connect() as conn:
        return db.list_documents(conn, status)


@st.cache_data(show_spinner=False, max_entries=64)
def page_image(pdf_path: str, page_number: int, resolution: int = 160):
    """Render one PDF page (1-based) for on-screen reading, with blank margins trimmed."""
    import pdfplumber
    from PIL import ImageOps

    with pdfplumber.open(pdf_path) as pdf:
        img = pdf.pages[page_number - 1].to_image(resolution=resolution).original.convert("RGB")
    # Find the printed area (anything noticeably darker than paper) and crop to it.
    ink = ImageOps.invert(img.convert("L")).point(lambda v: 255 if v > 40 else 0)
    box = ink.getbbox()
    if box:
        pad = resolution // 4
        img = img.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                        min(img.width, box[2] + pad), min(img.height, box[3] + pad)))
    return img


def open_in_review(doc_id: int) -> None:
    st.session_state["review_doc_id"] = doc_id
    st.switch_page("views/review.py")
