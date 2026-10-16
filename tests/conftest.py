import importlib
import socket

import pytest

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_real_connect = socket.socket.connect


def _guarded_connect(sock, address):
    host = address[0] if isinstance(address, tuple) else address
    if sock.family == socket.AF_UNIX or host in _LOCAL_HOSTS:
        return _real_connect(sock, address)
    raise RuntimeError(f"Tests must not use the network (tried to connect to {host}). "
                       "Mock the API with httpx MockTransport instead.")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test runs offline: any connection off this machine fails the test."""
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


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
