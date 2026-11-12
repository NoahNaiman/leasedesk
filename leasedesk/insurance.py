"""Tenant insurance: certificates of insurance (COIs) checked against the landlord's requirements.

Each certificate's key facts (insured, carrier, dates, general liability limits, umbrella,
whether the landlord is an additional insured) are read from the PDF by Claude or entered by
hand, and stored per tenant. Each tenant's newest certificate is checked against one set of
requirements, giving a status a property manager can act on: missing, expired, expiring
soon, limits too low, landlord not named, or current.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import anthropic
import pandas as pd
from pydantic import BaseModel, Field

from leasedesk import config, documents, portfolio

STATUS_MISSING = "Missing"
STATUS_EXPIRED = "Expired"
STATUS_EXPIRING = "Expiring soon"
STATUS_LOW_LIMITS = "Limits below requirement"
STATUS_NOT_ADDITIONAL = "Landlord not additional insured"
STATUS_CURRENT = "Current"
STATUS_ORDER = [STATUS_MISSING, STATUS_EXPIRED, STATUS_LOW_LIMITS, STATUS_NOT_ADDITIONAL, STATUS_EXPIRING,
                STATUS_CURRENT]

SCHEMA = """
CREATE TABLE IF NOT EXISTS certificates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    property        TEXT NOT NULL,
    tenant          TEXT NOT NULL,
    file_name       TEXT,
    stored_path     TEXT,
    certificate_json TEXT NOT NULL,
    source          TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS insurance_requirements (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    gl_each_occurrence       REAL NOT NULL,
    gl_aggregate             REAL NOT NULL,
    additional_insured       INTEGER NOT NULL,
    expiring_soon_days       INTEGER NOT NULL
);
"""


class Certificate(BaseModel):
    """What a certificate of liability insurance says."""
    insured_name: str | None = Field(None, description="The insured (the tenant), as written.")
    carrier: str | None = Field(None, description="The insurer providing general liability coverage.")
    policy_number: str | None = Field(None, description="General liability policy number.")
    effective_date: date | None = Field(None, description="General liability policy effective date.")
    expiration_date: date | None = Field(None, description="General liability policy expiration date.")
    gl_each_occurrence: float | None = Field(None, description="General liability limit, each occurrence, dollars.")
    gl_aggregate: float | None = Field(None, description="General liability general aggregate limit, dollars.")
    umbrella_limit: float | None = Field(None, description="Umbrella or excess liability limit, dollars, if any.")
    additional_insured: bool | None = Field(
        None, description="True if the certificate shows the certificate holder (landlord) as an additional insured.")
    certificate_holder: str | None = Field(None, description="The certificate holder named on the form.")


@dataclass
class Requirements:
    gl_each_occurrence: float = 1_000_000
    gl_aggregate: float = 2_000_000
    additional_insured: bool = True
    expiring_soon_days: int = 30


@dataclass
class StoredCertificate:
    id: int
    property: str
    tenant: str
    file_name: str | None
    stored_path: str | None
    certificate: Certificate
    source: str


@dataclass
class TenantInsurance:
    property: str
    tenant: str
    status: str
    problems: list[str]
    certificate: StoredCertificate | None


# --------------------------------------------------------------------------- status

def assess(cert: Certificate | None, req: Requirements, today: date) -> tuple[str, list[str]]:
    """(status, every problem found). The status is the most urgent problem."""
    if cert is None:
        return STATUS_MISSING, ["No certificate on file."]
    problems: list[tuple[str, str]] = []
    if cert.expiration_date is None:
        problems.append((STATUS_EXPIRED, "The expiration date couldn't be read; treat as expired until checked."))
    elif cert.expiration_date < today:
        problems.append((STATUS_EXPIRED, f"Expired {cert.expiration_date:%b %-d, %Y}."))
    elif (cert.expiration_date - today).days <= req.expiring_soon_days:
        problems.append((STATUS_EXPIRING, f"Expires {cert.expiration_date:%b %-d, %Y} "
                                          f"({(cert.expiration_date - today).days} days)."))
    # Umbrella coverage counts toward both limits, as most leases allow.
    umbrella = cert.umbrella_limit or 0
    for label, have, need in (("each-occurrence", cert.gl_each_occurrence, req.gl_each_occurrence),
                              ("aggregate", cert.gl_aggregate, req.gl_aggregate)):
        if have is None:
            problems.append((STATUS_LOW_LIMITS, f"General liability {label} limit not shown."))
        elif have + umbrella < need:
            problems.append((STATUS_LOW_LIMITS, f"General liability {label} ${have + umbrella:,.0f} "
                                                f"(with umbrella) is below the required ${need:,.0f}."))
    if req.additional_insured and cert.additional_insured is not True:
        problems.append((STATUS_NOT_ADDITIONAL, "The landlord isn't shown as an additional insured."))
    if not problems:
        return STATUS_CURRENT, []
    status = min((s for s, _ in problems), key=STATUS_ORDER.index)
    return status, [text for _, text in problems]


def tenant_insurance(groups, certificates: list[StoredCertificate], req: Requirements,
                     today: date) -> list[TenantInsurance]:
    """Each active tenant's newest certificate and status, most urgent first."""
    by_tenant: dict[tuple[str, str], list[StoredCertificate]] = {}
    for c in certificates:
        by_tenant.setdefault((c.property, c.tenant), []).append(c)
    out = []
    for g in groups:
        if portfolio.tenant_status(g.terms.expiration_date, today) == portfolio.STATUS_EXPIRED:
            continue
        certs = by_tenant.get((g.property, g.name), [])
        newest = max(certs, key=lambda c: (c.certificate.expiration_date or date.min, c.id), default=None)
        status, problems = assess(newest.certificate if newest else None, req, today)
        out.append(TenantInsurance(g.property, g.name, status, problems, newest))
    return sorted(out, key=lambda t: (STATUS_ORDER.index(t.status), t.property, t.tenant))


def frame(rows: list[TenantInsurance]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Status": r.status, "Property": r.property, "Tenant": r.tenant,
        "Carrier": r.certificate.certificate.carrier if r.certificate else None,
        "Expires": r.certificate.certificate.expiration_date if r.certificate else None,
        "GL each occurrence": r.certificate.certificate.gl_each_occurrence if r.certificate else None,
        "GL aggregate": r.certificate.certificate.gl_aggregate if r.certificate else None,
        "Umbrella": r.certificate.certificate.umbrella_limit if r.certificate else None,
        "Additional insured": (None if not r.certificate or r.certificate.certificate.additional_insured is None
                               else ("Yes" if r.certificate.certificate.additional_insured else "No")),
        "Problems": " ".join(r.problems),
    } for r in rows])


# --------------------------------------------------------------------------- storage

def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def get_requirements(conn) -> Requirements:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM insurance_requirements WHERE id = 1").fetchone()
    if r is None:
        return Requirements()
    return Requirements(r["gl_each_occurrence"], r["gl_aggregate"], bool(r["additional_insured"]),
                        r["expiring_soon_days"])


def save_requirements(conn, req: Requirements) -> None:
    ensure_schema(conn)
    conn.execute("""INSERT INTO insurance_requirements (id, gl_each_occurrence, gl_aggregate, additional_insured,
                    expiring_soon_days) VALUES (1, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET gl_each_occurrence = excluded.gl_each_occurrence,
                    gl_aggregate = excluded.gl_aggregate, additional_insured = excluded.additional_insured,
                    expiring_soon_days = excluded.expiring_soon_days""",
                 (req.gl_each_occurrence, req.gl_aggregate, int(req.additional_insured), req.expiring_soon_days))


def save_certificate(conn, property_name: str, tenant: str, cert: Certificate, *, source: str,
                     file_name: str | None = None, stored_path: str | None = None) -> int:
    ensure_schema(conn)
    return conn.execute(
        """INSERT INTO certificates (property, tenant, file_name, stored_path, certificate_json, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (property_name, tenant, file_name, stored_path, cert.model_dump_json(), source,
         datetime.now().isoformat(timespec="seconds"))).lastrowid


def delete_certificate(conn, certificate_id: int) -> None:
    conn.execute("DELETE FROM certificates WHERE id = ?", (certificate_id,))


def list_certificates(conn) -> list[StoredCertificate]:
    ensure_schema(conn)
    return [StoredCertificate(r["id"], r["property"], r["tenant"], r["file_name"], r["stored_path"],
                              Certificate.model_validate_json(r["certificate_json"]), r["source"])
            for r in conn.execute("SELECT * FROM certificates ORDER BY id").fetchall()]


# --------------------------------------------------------------------------- reading a certificate with Claude

COI_PROMPT = """This is a certificate of liability insurance for a commercial tenant. Read the general
liability section and fill in the schema. Use null for anything the certificate doesn't show. Limits are
whole dollars. additional_insured is true only if the form indicates the certificate holder is an
additional insured (for example, the "ADDL INSD" box is marked for general liability, or the description
says so)."""


def extract_certificate(client: anthropic.Anthropic, path: Path) -> tuple[Certificate, int, int]:
    """Read a COI PDF. Returns (certificate, input tokens, output tokens)."""
    pdf = base64.standard_b64encode(documents.pdf_for_api(Path(path))).decode("ascii")
    response = client.messages.create(
        model=config.EXTRACTION_MODEL,
        max_tokens=2000,
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": anthropic.transform_schema(Certificate)}},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf}},
            {"type": "text", "text": COI_PROMPT},
        ]}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"The certificate couldn't be read ({response.stop_reason}).")
    text = "".join(b.text for b in response.content if b.type == "text")
    return Certificate.model_validate(json.loads(text)), response.usage.input_tokens, response.usage.output_tokens
