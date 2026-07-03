"""Synthetic demo portfolio: Willow Creek Commons and Maple Ridge Plaza.

Every tenant, person, and address here is fictional. The generator writes
realistic retail lease PDFs (two of them made to look scanned) together with
the correct abstract for each one. That gives us:

* a demo that works without real data or an API key, and
* an answer key for measuring how accurately the AI abstracts leases.

Dates are generated relative to "today" so the critical-dates calendar
always has upcoming events to show.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dateutil.relativedelta import relativedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from leasedesk.models import (
    CapType,
    Confidence,
    FieldEvidence,
    LeaseAbstract,
    LeaseOption,
    OptionType,
    PercentageRent,
    Recoveries,
    RecoveryStructure,
    RentStep,
)

@dataclass(frozen=True)
class DemoProperty:
    name: str
    address: str
    landlord: str
    gla: int  # gross leasable area, the pro-rata share denominator


WILLOW_CREEK = DemoProperty("Willow Creek Commons", "4800 Willow Creek Parkway, Fairview, OH 45999",
                            "Willow Creek Commons Owner LLC", 86_000)
MAPLE_RIDGE = DemoProperty("Maple Ridge Plaza", "1250 Maple Ridge Road, Fairview, OH 45999",
                           "Maple Ridge Plaza Partners LLC", 18_500)
DEMO_PROPERTIES = [WILLOW_CREEK, MAPLE_RIDGE]

# Page layout of every generated lease, used for evidence citations.
PAGE_BASIC_TERMS = 1
PAGE_RENT = 2
PAGE_RECOVERIES = 3
PAGE_OPTIONS_USE = 4


@dataclass
class DemoTenant:
    tenant_name: str
    trade_name: str
    suite: str
    square_feet: int
    term_years: int
    # Months from today that the lease commenced (negative = in the past).
    start_offset_months: int
    starting_rent_psf: float
    escalation_pct: float = 3.0
    recovery: RecoveryStructure = RecoveryStructure.NNN
    cam_cap_pct: float | None = None
    cam_cap_type: CapType = CapType.NONE
    base_year: int | None = None
    admin_fee_pct: float = 15.0
    percentage_rent_pct: float | None = None
    options: list[tuple[OptionType, int, str]] = field(default_factory=list)
    permitted_use: str = "general retail sales"
    exclusive_use: str | None = None
    co_tenancy: str | None = None
    guarantor: str | None = None
    deposit_months: float = 1.0
    scanned: bool = False
    review_status: str = "approved"  # or "needs_review" to demo the review queue
    property: DemoProperty = WILLOW_CREEK


TENANTS: list[DemoTenant] = [
    DemoTenant("FreshWay Markets, Inc.", "FreshWay Market", "A-100", 45_000, 20, -110, 14.00,
               escalation_pct=0, percentage_rent_pct=1.0, admin_fee_pct=10.0,
               options=[(OptionType.RENEWAL, 60, "Four renewal options of five years each at fair market rent.")],
               permitted_use="full-service grocery supermarket",
               exclusive_use="Sale of fresh meat, produce, and groceries for off-premises consumption."),
    DemoTenant("Brightside Drug Stores LLC", "Brightside Pharmacy", "A-110", 12_000, 15, -70, 21.50,
               escalation_pct=0, cam_cap_pct=5.0, cam_cap_type=CapType.CUMULATIVE,
               options=[(OptionType.RENEWAL, 60, "Three renewal options of five years each; rent increases 10% per option.")],
               permitted_use="retail pharmacy and convenience goods",
               exclusive_use="Operation of a pharmacy filling prescriptions."),
    DemoTenant("Copper Kettle Coffee Co.", "Copper Kettle Coffee", "B-120", 1_800, 10, -40, 38.00,
               cam_cap_pct=5.0, cam_cap_type=CapType.NON_CUMULATIVE, guarantor="Dana Whitfield",
               options=[(OptionType.RENEWAL, 60, "One renewal option of five years at 103% of the prior year's rent.")],
               permitted_use="coffee house with drive-through",
               exclusive_use="Sale of espresso-based beverages as a primary use.",
               co_tenancy="If FreshWay Market ceases operating for more than 180 days, tenant may pay 50% of "
                          "base rent in lieu of rent until a replacement grocer opens, or terminate after 12 months."),
    DemoTenant("Summit Fitness Partners LLC", "Summit Fitness", "B-130", 6_500, 10, -34, 19.00,
               options=[(OptionType.TERMINATION, 0, "Tenant may terminate effective at the end of lease year 7 "
                         "with 9 months' notice and payment of unamortized tenant improvements.")],
               permitted_use="fitness center and personal training"),
    DemoTenant("Lotus Nail & Spa Inc.", "Lotus Nail Spa", "B-140", 1_400, 5, -50, 32.00, guarantor="Minh Tran",
               permitted_use="nail salon and day spa", scanned=True, review_status="needs_review"),
    DemoTenant("Golden Dragon Kitchen LLC", "Golden Dragon Kitchen", "B-150", 2_400, 10, -62, 30.00,
               percentage_rent_pct=6.0, permitted_use="full-service Chinese restaurant",
               options=[(OptionType.RENEWAL, 60, "Two renewal options of five years at fair market rent.")]),
    DemoTenant("Paws & Claws Retail Corp.", "Paws & Claws Pet Supply", "C-200", 4_200, 10, -23, 22.00,
               cam_cap_pct=4.0, cam_cap_type=CapType.NON_CUMULATIVE, permitted_use="pet supplies and grooming",
               co_tenancy="Tenant's rent reduces to 4% of gross sales if FreshWay Market and Brightside "
                          "Pharmacy are both closed for more than 120 consecutive days."),
    DemoTenant("Willow Dental Group PC", "Willow Dental Group", "C-210", 2_000, 7, -20, 27.00,
               recovery=RecoveryStructure.MODIFIED_GROSS, base_year=date.today().year - 1,
               permitted_use="general dentistry offices", review_status="needs_review"),
    DemoTenant("QuickShip Centers LLC", "QuickShip Mailing", "101", 1_200, 5, -54, 29.00,
               permitted_use="shipping, mailbox rental, and printing services", property=MAPLE_RIDGE),
    DemoTenant("Harbor Sleep Co.", "Harbor Mattress", "104", 3_800, 7, -82, 20.00,
               options=[(OptionType.RENEWAL, 36, "One renewal option of three years at fair market rent.")],
               permitted_use="retail sale of mattresses and bedding", property=MAPLE_RIDGE),
    DemoTenant("Sunrise Bagel Bakery LLC", "Sunrise Bagels", "108", 1_500, 5, -52, 31.00, guarantor="Paul Ostrowski",
               options=[(OptionType.RENEWAL, 60, "One renewal option of five years at 105% of prior rent.")],
               permitted_use="bagel bakery and café", scanned=True, property=MAPLE_RIDGE),
    DemoTenant("Tri-County Savings Bank", "Tri-County Bank", "PAD-1", 3_500, 15, -90, 42.00,
               escalation_pct=0, deposit_months=0,
               options=[(OptionType.RENEWAL, 60, "Four renewal options of five years each, 10% increase per option.")],
               permitted_use="bank branch with drive-through lanes"),
]


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _rent_schedule(t: DemoTenant, commencement: date) -> list[RentStep]:
    """Annual bumps when escalation_pct > 0; otherwise 10% bumps every 5 years."""
    steps: list[RentStep] = []
    psf = t.starting_rent_psf
    for year in range(t.term_years):
        if year > 0:
            if t.escalation_pct:
                psf *= 1 + t.escalation_pct / 100
            elif year % 5 == 0:
                psf *= 1.10
        start = commencement + relativedelta(years=year)
        end = start + relativedelta(years=1, days=-1)
        annual_psf = round(psf, 2)
        steps.append(RentStep(start_date=start, end_date=end, annual_rent_psf=annual_psf,
                              monthly_rent=round(annual_psf * t.square_feet / 12, 2)))
    # Collapse consecutive identical years into one step for readability.
    merged: list[RentStep] = []
    for s in steps:
        if merged and merged[-1].annual_rent_psf == s.annual_rent_psf:
            merged[-1].end_date = s.end_date
        else:
            merged.append(s)
    return merged


def build_abstract(t: DemoTenant, today: date) -> LeaseAbstract:
    commencement = _first_of_month(today + relativedelta(months=t.start_offset_months))
    expiration = commencement + relativedelta(years=t.term_years, days=-1)
    schedule = _rent_schedule(t, commencement)
    share = round(t.square_feet / t.property.gla * 100, 2)

    options = []
    for opt_type, months, text in t.options:
        if opt_type == OptionType.TERMINATION:
            deadline = commencement + relativedelta(years=7) - relativedelta(months=9)
        else:
            deadline = expiration - relativedelta(days=180)
        options.append(LeaseOption(option_type=opt_type, notice_deadline=deadline,
                                   term_months=months or None, description=text))

    pct_rent = None
    if t.percentage_rent_pct:
        first_year_rent = schedule[0].monthly_rent * 12
        pct_rent = PercentageRent(
            rate_pct=t.percentage_rent_pct,
            breakpoint_type="natural",
            breakpoint_amount=round(first_year_rent / (t.percentage_rent_pct / 100), 2),
            sales_reporting="Monthly sales reports within 20 days after month end; annual certified "
                            "statement within 60 days after each lease year.",
        )

    gross = t.recovery == RecoveryStructure.MODIFIED_GROSS
    recoveries = Recoveries(
        structure=t.recovery,
        pro_rata_share_pct=share,
        pro_rata_denominator_notes=f"Tenant's floor area divided by the gross leasable area of the "
                                   f"Shopping Center, currently {t.property.gla:,} square feet.",
        pays_cam=True,
        pays_taxes=True,
        pays_insurance=True,
        cam_cap_pct=t.cam_cap_pct,
        cam_cap_type=t.cam_cap_type,
        base_year=t.base_year,
        admin_fee_pct=None if gross else t.admin_fee_pct,
        gross_up_pct=95.0,
        excluded_expenses=["capital improvements", "leasing commissions", "landlord's income taxes",
                           "costs reimbursed by insurance"],
    )

    evidence = [
        FieldEvidence(field="tenant_name", page=PAGE_BASIC_TERMS, quote=f"TENANT: {t.tenant_name}",
                      confidence=Confidence.HIGH),
        FieldEvidence(field="square_feet", page=PAGE_BASIC_TERMS,
                      quote=f"approximately {t.square_feet:,} square feet", confidence=Confidence.HIGH),
        FieldEvidence(field="expiration_date", page=PAGE_BASIC_TERMS,
                      quote=f"EXPIRATION DATE: {expiration:%B %d, %Y}", confidence=Confidence.HIGH),
        FieldEvidence(field="base_rent_schedule", page=PAGE_RENT, quote="Schedule of Base Rent",
                      confidence=Confidence.HIGH),
        FieldEvidence(field="recoveries.pro_rata_share_pct", page=PAGE_RECOVERIES,
                      quote=f"Tenant's Proportionate Share is {share}%", confidence=Confidence.HIGH),
    ]
    if t.cam_cap_pct:
        evidence.append(FieldEvidence(field="recoveries.cam_cap_pct", page=PAGE_RECOVERIES,
                                      quote=f"shall not increase by more than {t.cam_cap_pct:g}%",
                                      confidence=Confidence.MEDIUM))

    return LeaseAbstract(
        landlord=t.property.landlord,
        tenant_name=t.tenant_name,
        trade_name=t.trade_name,
        guarantor=t.guarantor,
        property_name=t.property.name,
        property_address=t.property.address,
        suite=t.suite,
        square_feet=t.square_feet,
        lease_date=commencement - relativedelta(months=4),
        commencement_date=commencement,
        rent_commencement_date=commencement,
        expiration_date=expiration,
        base_rent_schedule=schedule,
        security_deposit=round(schedule[0].monthly_rent * t.deposit_months, 2) or None,
        recoveries=recoveries,
        percentage_rent=pct_rent,
        options=options,
        permitted_use=t.permitted_use,
        exclusive_use=t.exclusive_use,
        co_tenancy=t.co_tenancy,
        radius_restriction="Tenant shall not operate a similar business within three (3) miles of the "
                           "Shopping Center during the Term." if t.percentage_rent_pct else None,
        evidence=evidence,
    )


# --------------------------------------------------------------------------- PDF rendering

def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Times-Bold", fontSize=15),
        "h": ParagraphStyle("h", parent=base["Heading3"], fontName="Times-Bold", fontSize=11, spaceBefore=8),
        "p": ParagraphStyle("p", parent=base["BodyText"], fontName="Times-Roman", fontSize=10.5, leading=14,
                            alignment=4),
    }


def _money(x: float) -> str:
    return f"${x:,.2f}"


def render_lease_pdf(a: LeaseAbstract, t: DemoTenant) -> bytes:
    s = _styles()
    P = lambda text, style="p": Paragraph(text, s[style])  # noqa: E731
    story = []

    # Page 1: basic lease provisions
    story += [
        P("SHOPPING CENTER LEASE", "title"),
        P(f"THIS LEASE is made as of {a.lease_date:%B %d, %Y}, between <b>{a.landlord}</b>, a Delaware "
          f"limited liability company (\"Landlord\"), and the Tenant named below."),
        P("ARTICLE 1. BASIC LEASE PROVISIONS", "h"),
        P(f"1.1 SHOPPING CENTER: {a.property_name}, {a.property_address}."),
        P(f"1.2 TENANT: {a.tenant_name}, doing business as \"{a.trade_name}\"."),
        P(f"1.3 PREMISES: Suite {a.suite}, containing approximately {a.square_feet:,.0f} square feet "
          f"of floor area, as shown on Exhibit A."),
        P(f"1.4 TERM: {t.term_years} years. COMMENCEMENT DATE: {a.commencement_date:%B %d, %Y}. "
          f"RENT COMMENCEMENT DATE: {a.rent_commencement_date:%B %d, %Y}. "
          f"EXPIRATION DATE: {a.expiration_date:%B %d, %Y}."),
        P(f"1.5 PERMITTED USE: {a.permitted_use[0].upper() + a.permitted_use[1:]}, and for no other purpose."),
        P(f"1.6 SECURITY DEPOSIT: {_money(a.security_deposit) if a.security_deposit else 'None'}."),
        P(f"1.7 GUARANTOR: {a.guarantor or 'None'}."),
        P("1.8 NOTICE ADDRESSES: As set forth in Article 22."),
        P("The Basic Lease Provisions are incorporated into and made a part of this Lease. If there is any "
          "conflict between the Basic Lease Provisions and the remainder of this Lease, the remainder of this "
          "Lease shall control."),
        PageBreak(),
    ]

    # Page 2: rent
    rows = [["Period", "Annual Rent / SF", "Monthly Base Rent"]]
    for step in a.base_rent_schedule:
        rows.append([f"{step.start_date:%m/%d/%Y} - {step.end_date:%m/%d/%Y}",
                     _money(step.annual_rent_psf), _money(step.monthly_rent)])
    table = Table(rows, colWidths=[2.6 * inch, 1.6 * inch, 1.8 * inch])
    table.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Times-Roman", 10),
                               ("FONT", (0, 0), (-1, 0), "Times-Bold", 10),
                               ("GRID", (0, 0), (-1, -1), 0.5, "#555555")]))
    story += [
        P("ARTICLE 4. RENT", "h"),
        P("4.1 Base Rent. Beginning on the Rent Commencement Date, Tenant shall pay Base Rent in equal monthly "
          "installments, in advance, on the first day of each calendar month, without demand, deduction, or "
          "setoff, in accordance with the following Schedule of Base Rent:"),
        Spacer(1, 6), table, Spacer(1, 6),
        P("4.2 Late Charge. Any installment not received within five (5) days after its due date shall bear a "
          "late charge of five percent (5%) of the amount due."),
    ]
    if a.percentage_rent:
        pr = a.percentage_rent
        story += [P(f"4.3 Percentage Rent. In addition to Base Rent, Tenant shall pay as Percentage Rent "
                    f"{pr.rate_pct:g}% of Gross Sales in each Lease Year in excess of {_money(pr.breakpoint_amount)} "
                    f"(the \"Breakpoint\"). {pr.sales_reporting}")]
    story.append(PageBreak())

    # Page 3: recoveries
    r = a.recoveries
    story += [P("ARTICLE 6. OPERATING COSTS, TAXES AND INSURANCE", "h")]
    if r.structure == RecoveryStructure.MODIFIED_GROSS:
        story.append(P(f"6.1 Expense Stop. Tenant shall pay Tenant's Proportionate Share of the amount by which "
                       f"Operating Costs, Taxes, and Insurance Costs for each calendar year exceed those for the "
                       f"calendar year {r.base_year} (the \"Base Year\")."))
    else:
        story.append(P("6.1 Tenant shall pay, as Additional Rent, Tenant's Proportionate Share of Common Area "
                       "Maintenance Costs, Real Estate Taxes, and Insurance Costs for the Shopping Center, in "
                       "monthly estimated installments, subject to annual reconciliation."))
    story.append(P(f"6.2 Proportionate Share. Tenant's Proportionate Share is {r.pro_rata_share_pct}%, being the "
                   f"floor area of the Premises divided by the gross leasable area of the Shopping Center, "
                   f"currently {t.property.gla:,} square feet."))
    if r.admin_fee_pct:
        story.append(P(f"6.3 Administrative Fee. Common Area Maintenance Costs shall include an administrative "
                       f"fee equal to {r.admin_fee_pct:g}% of such costs, excluding Taxes and Insurance."))
    if r.cam_cap_pct:
        kind = "on a cumulative, compounding basis" if r.cam_cap_type == CapType.CUMULATIVE \
            else "over the prior calendar year, on a non-cumulative basis"
        story.append(P(f"6.4 Cap. Controllable Common Area Maintenance Costs payable by Tenant shall not increase "
                       f"by more than {r.cam_cap_pct:g}% per year {kind}. Taxes, insurance, utilities, and snow "
                       f"removal are not controllable costs."))
    story.append(P(f"6.5 Gross-Up. If the Shopping Center is less than {r.gross_up_pct:g}% occupied during any "
                   f"year, variable Operating Costs shall be adjusted to reflect {r.gross_up_pct:g}% occupancy."))
    story.append(P("6.6 Exclusions. Operating Costs shall not include: " + "; ".join(r.excluded_expenses) + "."))
    story.append(PageBreak())

    # Page 4: options, use, co-tenancy
    story += [P("ARTICLE 8. OPTIONS", "h")]
    if a.options:
        for i, opt in enumerate(a.options, 1):
            story.append(P(f"8.{i} {opt.option_type.value.replace('_', ' ').title()} Option. {opt.description} "
                           f"Tenant must deliver written notice of exercise no later than "
                           f"{opt.notice_deadline:%B %d, %Y}, time being of the essence."))
    else:
        story.append(P("8.1 Tenant has no options to extend or terminate this Lease."))
    story += [P("ARTICLE 9. USE AND OPERATION", "h"),
              P(f"9.1 Tenant shall use the Premises solely for {a.permitted_use}.")]
    if a.exclusive_use:
        story.append(P(f"9.2 Exclusive. Landlord shall not lease space in the Shopping Center to another tenant "
                       f"for the following use: {a.exclusive_use}"))
    if a.co_tenancy:
        story.append(P(f"9.3 Co-Tenancy. {a.co_tenancy}"))
    if a.radius_restriction:
        story.append(P(f"9.4 Radius. {a.radius_restriction}"))
    story += [Spacer(1, 30), P("IN WITNESS WHEREOF, the parties have executed this Lease as of the date first "
                               "written above."),
              Spacer(1, 20), P(f"LANDLORD: {a.landlord}  By: ______________________"),
              P(f"TENANT: {a.tenant_name}  By: ______________________")]

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=letter, title=f"Lease - {a.trade_name}",
                      leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch).build(story)
    return buf.getvalue()


def make_scanned(pdf_bytes: bytes, seed: int) -> bytes:
    """Turn a text PDF into an image-only PDF that looks like a photocopy."""
    import pdfplumber
    from PIL import Image, ImageFilter, ImageOps

    rng = random.Random(seed)
    pages: list[Image.Image] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            img = page.to_image(resolution=110).original.convert("L")
            img = img.rotate(rng.uniform(-1.2, 1.2), expand=False, fillcolor=255, resample=Image.BICUBIC)
            img = img.filter(ImageFilter.GaussianBlur(0.5))
            img = ImageOps.autocontrast(img, cutoff=2)
            pages.append(img.convert("RGB"))
    out = io.BytesIO()
    pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:], resolution=110)
    return out.getvalue()


@dataclass
class DemoLease:
    file_name: str
    pdf_bytes: bytes
    abstract: LeaseAbstract
    review_status: str


def build_demo_portfolio(today: date | None = None) -> list[DemoLease]:
    today = today or date.today()
    leases = []
    for i, t in enumerate(TENANTS):
        abstract = build_abstract(t, today)
        pdf = render_lease_pdf(abstract, t)
        if t.scanned:
            pdf = make_scanned(pdf, seed=i)
        name = f"{t.suite} {t.trade_name} - Lease{' (scan)' if t.scanned else ''}.pdf"
        leases.append(DemoLease(name, pdf, abstract, t.review_status))
    return leases


def write_demo_files(folder: Path, today: date | None = None) -> list[Path]:
    """Write the demo PDFs and their answer-key JSON to a folder (for evaluation or inspection)."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for lease in build_demo_portfolio(today):
        pdf_path = folder / lease.file_name
        pdf_path.write_bytes(lease.pdf_bytes)
        pdf_path.with_suffix(".answer.json").write_text(lease.abstract.model_dump_json(indent=2))
        paths.append(pdf_path)
    return paths


def load_demo_into_db(conn, today: date | None = None) -> int:
    """Import the demo portfolio as if it had been uploaded and abstracted. Returns rows added."""
    from leasedesk import db, documents

    added = 0
    for lease in build_demo_portfolio(today):
        result = documents.import_pdf_bytes(conn, lease.file_name, lease.pdf_bytes, is_demo=True)
        if result.doc_id is None:
            continue
        db.save_ai_abstract(conn, result.doc_id, lease.abstract,
                            approved=lease.review_status == "approved")
        added += 1
    return added


def remove_demo_from_db(conn) -> int:
    from leasedesk import db, documents

    paths = db.delete_demo_documents(conn)
    for path in paths:
        documents.remove_stored_file(path)
    return len(paths)


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_output")
    for p in write_demo_files(out):
        print(p)
