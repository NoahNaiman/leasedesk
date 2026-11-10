import importlib

import pytest


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point all storage at a temporary folder so tests never touch real data."""
    monkeypatch.setenv("LEASEDESK_DATA_DIR", str(tmp_path))
    from leasedesk import config

    importlib.reload(config)
    yield tmp_path
    monkeypatch.delenv("LEASEDESK_DATA_DIR")
    importlib.reload(config)


@pytest.fixture
def conn(data_dir):
    from leasedesk import config, db

    with db.connect(config.DB_PATH) as c:
        yield c


@pytest.fixture
def sample_pdf(tmp_path):
    """A small text PDF built on the fly."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    path = tmp_path / "sample lease.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    for page in range(2):
        c.drawString(72, 720, f"RETAIL LEASE page {page + 1}. This lease is made between Landlord and Tenant.")
        c.drawString(72, 700, "Tenant shall pay Base Rent monthly in advance on the first day of each month.")
        c.showPage()
    c.save()
    return path
