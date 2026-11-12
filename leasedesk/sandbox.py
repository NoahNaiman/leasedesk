"""A complete, fictional LeaseDesk workspace for trying the app and for testing it.

``build_sandbox`` writes two things into one folder:

* ``Properties/``: property folders laid out the way real ones are: a subfolder per
  tenant holding the lease, amendments and guaranties, plus everything a property
  manager's folders also contain (old tenants, insurance certificates, CAM and tax
  letters, partnership papers, photos, Word letters, redacted copies, a scan with a
  meaningless name) and one property that is excluded.
* ``data/``: a LeaseDesk database built from those folders, with documents in every
  state: approved, waiting for review, waiting to be abstracted, and failed.

Every generated file records the decision the folder scanner should make about it,
so the sandbox doubles as the scanner's answer key. No API key or AI calls are
needed: abstracts come from the generator, which knows the right answers.

Every tenant, person and address is fictional.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dateutil.relativedelta import relativedelta
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from leasedesk import db, demo_data, library
from leasedesk.models import (
    Confidence,
    DocumentType,
    FieldEvidence,
    LeaseAbstract,
    RentStep,
)

WILLOW_FOLDER = "4800 Willow Creek Parkway"
MAPLE_FOLDER = "1250 Maple Ridge Road"
EXCLUDED_FOLDER = "77 Old Mill Road"
# Folder name -> name shown in the app (demonstrates renaming a property).
DISPLAY_NAMES = {WILLOW_FOLDER: "Willow Creek Commons", MAPLE_FOLDER: "Maple Ridge Plaza"}

# Review states for the documents that are added to LeaseDesk.
APPROVED, REVIEW, PENDING, FAILED = "approved", "needs_review", "pending", "failed"
FAILED_MESSAGE = "The document could not be processed: the PDF is password-protected."


@dataclass
class SandboxFile:
    rel_path: str                       # relative to the Properties folder
    data: bytes
    expected: str                       # the scanner's expected decision
    tenant_folder: str | None = None    # the tenant subfolder the scanner should report
    abstract: LeaseAbstract | None = None
    state: str | None = None            # review state once added (lease documents only)
    ai_triage: str | None = None        # simulated AI triage result for unclear files


# --------------------------------------------------------------------------- simple documents

def text_pdf(title: str, *paragraphs: str) -> bytes:
    """A short, plain PDF: enough for the scanner and the page viewer."""
    styles = demo_data._styles()
    story = [Paragraph(title, styles["title"]), Spacer(1, 12)]
    story += [Paragraph(p, styles["p"]) for p in paragraphs]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=letter, title=title).build(story)
    return buf.getvalue()


def tiny_jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (140, 170, 160)).save(buf, format="JPEG")
    return buf.getvalue()


def extension_amendment(lease: LeaseAbstract, years: int, bump_pct: float, today: date) -> LeaseAbstract:
    """An amendment that extends a lease and sets a new rent schedule."""
    start = lease.expiration_date + relativedelta(days=1)
    rent = lease.base_rent_schedule[-1].monthly_rent
    steps = []
    for y in range(years):
        rent = round(rent * (1 + bump_pct / 100), 2)
        s = start + relativedelta(years=y)
        steps.append(RentStep(start_date=s, end_date=s + relativedelta(years=1, days=-1), monthly_rent=rent,
                              annual_rent_psf=round(rent * 12 / lease.square_feet, 2)))
    end = start + relativedelta(years=years, days=-1)
    return LeaseAbstract(
        document_type=DocumentType.AMENDMENT, landlord=lease.landlord, tenant_name=lease.tenant_name,
        trade_name=lease.trade_name, property_name=lease.property_name, property_address=lease.property_address,
        suite=lease.suite, lease_date=today - relativedelta(months=2), commencement_date=start,
        expiration_date=end, base_rent_schedule=steps,
        notes=f"Extends the term {years} years, from {start:%B %d, %Y} through {end:%B %d, %Y}, with base rent "
              f"increasing {bump_pct:g}% each year. All other terms of the Lease are unchanged.",
        evidence=[FieldEvidence(field="expiration_date", page=1, quote=f"through {end:%B %d, %Y}",
                                confidence=Confidence.HIGH)],
    )


def amendment_pdf(a: LeaseAbstract) -> bytes:
    rows = "<br/>".join(f"{s.start_date:%m/%d/%Y} - {s.end_date:%m/%d/%Y}: ${s.monthly_rent:,.2f} per month"
                        for s in a.base_rent_schedule)
    return text_pdf(
        "FIRST AMENDMENT TO LEASE",
        f"THIS FIRST AMENDMENT is made as of {a.lease_date:%B %d, %Y} between {a.landlord} (\"Landlord\") "
        f"and {a.tenant_name} (\"Tenant\") for Suite {a.suite} at {a.property_name}.",
        f"1. Extension. The Term is extended from {a.commencement_date:%B %d, %Y} through "
        f"{a.expiration_date:%B %d, %Y}.",
        f"2. Base Rent. During the extended Term, monthly Base Rent shall be:<br/>{rows}",
        "3. Ratification. Except as amended here, the Lease remains in full force and effect.",
    )


def guaranty(lease: LeaseAbstract) -> tuple[LeaseAbstract, bytes]:
    a = LeaseAbstract(document_type=DocumentType.GUARANTY, landlord=lease.landlord,
                      tenant_name=lease.tenant_name, trade_name=lease.trade_name, guarantor=lease.guarantor,
                      property_name=lease.property_name, suite=lease.suite, lease_date=lease.lease_date,
                      notes=f"{lease.guarantor} personally guarantees all of Tenant's obligations under the Lease.")
    pdf = text_pdf("GUARANTY OF LEASE",
                   f"For value received, {lease.guarantor} (\"Guarantor\") guarantees to {lease.landlord} the full "
                   f"and punctual payment and performance of all obligations of {lease.tenant_name} under the "
                   f"Lease dated {lease.lease_date:%B %d, %Y} for Suite {lease.suite}.")
    return a, pdf


# --------------------------------------------------------------------------- the folders

def build_files(today: date | None = None) -> list[SandboxFile]:
    today = today or date.today()
    leases = demo_data.build_demo_portfolio(today)
    files: list[SandboxFile] = []
    folder_of = {demo_data.WILLOW_CREEK.name: WILLOW_FOLDER, demo_data.MAPLE_RIDGE.name: MAPLE_FOLDER}

    for lease in leases:
        a = lease.abstract
        prop = folder_of[a.property_name]
        tenant = a.trade_name
        name = f"{tenant} Lease{' (scan)' if 'scan' in lease.file_name else ''}.pdf"
        state = {"approved": APPROVED, "needs_review": REVIEW}[lease.review_status]
        files.append(SandboxFile(f"{prop}/{tenant}/{name}", lease.pdf_bytes, library.DECISION_LEASE,
                                 tenant, a, state))

        if tenant == "Harbor Mattress":
            # Expiring soon, but extended by an amendment: the tenant list should show the new date.
            amend = extension_amendment(a, years=3, bump_pct=3.0, today=today)
            files.append(SandboxFile(f"{prop}/{tenant}/First Amendment to Lease.pdf", amendment_pdf(amend),
                                     library.DECISION_LEASE, tenant, amend, APPROVED))
        if tenant == "Copper Kettle Coffee":
            g, pdf = guaranty(a)
            files.append(SandboxFile(f"{prop}/{tenant}/Guaranty - {a.guarantor}.pdf", pdf,
                                     library.DECISION_LEASE, tenant, g, PENDING))
        if tenant == "FreshWay Market":
            files += [
                SandboxFile(f"{prop}/{tenant}/{tenant} Lease_REDACTED.pdf", lease.pdf_bytes + b"\n% redacted",
                            library.DECISION_SKIPPED, tenant),
                SandboxFile(f"{prop}/{tenant}/Insurance/COI 2026.pdf",
                            text_pdf("CERTIFICATE OF LIABILITY INSURANCE", f"Insured: {a.tenant_name}."),
                            library.DECISION_SKIPPED),
                SandboxFile(f"{prop}/{tenant}/CAM/2026 CAM Estimate Letter.pdf",
                            text_pdf("2026 CAM ESTIMATE", "Your estimated monthly CAM payment is enclosed."),
                            library.DECISION_SKIPPED),
            ]
        if tenant == "Brightside Pharmacy":
            amend = extension_amendment(a, years=5, bump_pct=2.5, today=today)
            files.append(SandboxFile(f"{prop}/{tenant}/Second Amendment.pdf", amendment_pdf(amend),
                                     library.DECISION_LEASE, tenant, amend, FAILED))
        if tenant == "Golden Dragon Kitchen":
            # A scan with a meaningless name: the scanner can't tell, and (simulated) AI triage decides.
            files.append(SandboxFile(f"{prop}/{tenant}/QScan0001.pdf", text_pdf(
                "FIRST AMENDMENT TO LEASE", f"Golden Dragon Kitchen LLC, Suite {a.suite}. Signage rights added."),
                library.DECISION_UNCLEAR, tenant, ai_triage="amendment"))
        if tenant == "Sunrise Bagels":
            files.append(SandboxFile(f"{prop}/{tenant}/Sunrise Bagels Letter.docx", b"PK\x03\x04 fake docx",
                                     library.DECISION_SKIPPED, tenant))
            files.append(SandboxFile(f"{prop}/{tenant}/Scan_0042.pdf", text_pdf(
                "CERTIFICATE OF LIABILITY INSURANCE", "Insured: Sunrise Bagel Bakery LLC."),
                library.DECISION_UNCLEAR, tenant, ai_triage="other_not_a_lease_document"))

    files += [
        # Property-level paperwork that is never a current lease.
        SandboxFile(f"{WILLOW_FOLDER}/Old Leases/Pinecrest Video/Pinecrest Video Lease.pdf",
                    text_pdf("SHOPPING CENTER LEASE", "Pinecrest Video, Suite B-160. Term ended 2012."),
                    library.DECISION_SKIPPED),
        SandboxFile(f"{WILLOW_FOLDER}/Partnership Agreement/Willow Creek JV Agreement.pdf",
                    text_pdf("JOINT VENTURE AGREEMENT", "Willow Creek Commons Owner LLC partners."),
                    library.DECISION_SKIPPED),
        SandboxFile(f"{WILLOW_FOLDER}/Tax/2025 Property Tax Bill.pdf",
                    text_pdf("PROPERTY TAX STATEMENT 2025", "Parcel 0412-07-001."), library.DECISION_SKIPPED),
        SandboxFile(f"{WILLOW_FOLDER}/Photos/storefronts.jpg", tiny_jpeg(), library.DECISION_SKIPPED),
        SandboxFile(f"{WILLOW_FOLDER}/Willow Creek Site Plan.pdf",
                    text_pdf("SITE PLAN", "Willow Creek Commons, 86,000 SF."), library.DECISION_SKIPPED),
        SandboxFile(f"{MAPLE_FOLDER}/Expired Leases/Maple Cuts/Maple Cuts Lease.pdf",
                    text_pdf("LEASE", "Maple Cuts salon. Expired."), library.DECISION_SKIPPED),
        SandboxFile(f"{MAPLE_FOLDER}/Maple Ridge Roof Contract.pdf",
                    text_pdf("ROOF REPLACEMENT CONTRACT", "Contractor agrees to replace the roof."),
                    library.DECISION_SKIPPED),
        # A property the user excluded (a house, say). Its files are never indexed.
        SandboxFile(f"{EXCLUDED_FOLDER}/Fence Estimate.pdf", text_pdf("FENCE ESTIMATE", "Backyard fence."),
                    "excluded"),
    ]
    return files


def write_files(root: Path, files: list[SandboxFile]) -> None:
    for f in files:
        path = root / f.rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f.data)


# --------------------------------------------------------------------------- the database

@dataclass
class Sandbox:
    folder: Path
    library_root: Path
    data_dir: Path
    files: list[SandboxFile]
    exports_dir: Path | None = None
    rent_roll_planted: list = field(default_factory=list)   # answer key for the rent roll check
    coi_expected: dict = field(default_factory=dict)        # tenant -> expected insurance status


def build_sandbox(folder: Path, today: date | None = None) -> Sandbox:
    """Write the property folders and a matching LeaseDesk database into ``folder``."""
    import shutil

    today = today or date.today()

    folder = Path(folder)
    root, data_dir, exports_dir = folder / "Properties", folder / "data", folder / "exports"
    for sub in (root, data_dir, exports_dir):
        if sub.exists():
            shutil.rmtree(sub)
    data_dir.mkdir(parents=True)
    files = build_files(today)
    write_files(root, files)
    # The sandbox's own settings: its library folder, and no API key (so nothing can cost money).
    (data_dir / "settings.env").write_text(f"LIBRARY_FOLDER={root}\n")

    by_path = {f.rel_path: f for f in files}
    with db.connect(data_dir / "leasedesk.sqlite") as conn:
        db.set_property_excluded(conn, db.upsert_property(conn, EXCLUDED_FOLDER), True)
        library.scan(conn, root)
        for folder_name, display in DISPLAY_NAMES.items():
            db.rename_property(conn, db.upsert_property(conn, folder_name), display)

        # Simulated AI triage of the unclear files (what Claude Haiku would decide).
        for row in conn.execute("SELECT id, rel_path FROM file_index WHERE decision = 'unclear'").fetchall():
            kind = by_path[row["rel_path"]].ai_triage
            is_lease = kind != "other_not_a_lease_document"
            conn.execute("""UPDATE file_index SET decision = ?, doc_kind = ?, decided_by = 'ai', reason = ?
                            WHERE id = ?""",
                         (library.DECISION_LEASE if is_lease else library.DECISION_SKIPPED, kind,
                          f"AI: sandbox example ({kind.replace('_', ' ')})", row["id"]))

        property_ids = [p.id for p in db.list_properties(conn)]
        library.add_lease_documents(conn, root, property_ids)

        for doc in db.list_documents(conn):
            f = by_path.get(doc.source_rel_path)
            state = f.state if f and f.state else PENDING   # e.g. the triaged QScan waits for the AI
            if state == FAILED:
                db.mark_failed(conn, doc.id, FAILED_MESSAGE)
            elif state in (APPROVED, REVIEW) and f.abstract is not None:
                db.save_ai_abstract(conn, doc.id, f.abstract, input_tokens=30_000, output_tokens=4_000,
                                    approved=state == APPROVED)

        # A fictional accounting export with known problems planted (see sandbox_exports.py).
        from leasedesk import sandbox_exports

        exports_dir.mkdir()
        csv_bytes, planted = sandbox_exports.rent_roll(db.list_documents(conn, db.STATUS_APPROVED),
                                                       db.list_documents(conn, db.STATUS_REVIEW), today)
        (exports_dir / sandbox_exports.RENT_ROLL_FILE).write_bytes(csv_bytes)
        sandbox_exports.seed_partnerships(conn, today)
        coi_expected = sandbox_exports.seed_certificates(conn, root, today)
    return Sandbox(folder, root, data_dir, files, exports_dir, planted, coi_expected)
