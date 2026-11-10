"""Central configuration: file locations and model settings.

All user data lives under ``data/`` (git-ignored) so real leases and the
database never end up in version control.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LEASEDESK_DATA_DIR", PROJECT_ROOT / "data"))
DOCUMENTS_DIR = DATA_DIR / "documents"
DB_PATH = DATA_DIR / "leasedesk.sqlite"
SETTINGS_PATH = DATA_DIR / "settings.env"

# Claude model used for lease abstraction.
EXTRACTION_MODEL = "claude-opus-5-5"
# Approximate list prices (USD per million tokens) used to show cost per lease.
MODEL_INPUT_PRICE_PER_MTOK = 4.00
MODEL_OUTPUT_PRICE_PER_MTOK = 20.00

# Claude accepts PDFs up to 32 MB per request and 600 pages.
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_PDF_PAGES = 600


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
