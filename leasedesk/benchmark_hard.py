"""A harder extraction benchmark: ten fictional leases built around what trips up real abstraction.

The standard demo leases are clean and share one layout, so they mostly catch regressions.
Each lease here has a specific difficulty taken from real retail leases:

======  ====================================================================================
H1      Rent described in prose ("increases 3% on each anniversary, rounded to the cent")
H2      Dates written in words or defined by formulas ("90 days after the Delivery Date")
H3      Option notice windows given in months, with two options in a series
H4      A lease and its first amendment in one file (abstract the terms as amended)
H5      Typed figures struck through and corrected by hand, with initials
H6      A 16-page, badly degraded scan with the key terms buried in later articles
H7      Pro-rata share given only as a formula; a cumulative cap described indirectly
H8      Modified gross with a base year; rent given only as annual totals; deposit by formula
H9      Percentage rent with an artificial breakpoint; three renewal options; no deposit
H10     Distractor figures (former tenant, lender, other tenants), amounts in words, and a
        guarantor named only on the guaranty exhibit
======  ====================================================================================

Every answer is computed by the same code that writes the lease text, so the PDF and the
answer key cannot disagree. Every tenant, person, and place is fictional.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from leasedesk.demo_data import _styles, make_scanned
from leasedesk.models import (
    CapType,
    DocumentType,
    LeaseAbstract,
    LeaseOption,
    OptionType,
    PercentageRent,
    Recoveries,
    RecoveryStructure,
    RentStep,
)

CENTER = "Cedar Point Marketplace"
ADDRESS = "2100 Cedar Point Road, Lakewood, CO 80999"
LANDLORD = "Cedar Point Retail Partners LLC"


def cents(x) -> float:
    """Round half up to the cent, the way leases say "rounded to the nearest cent"."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def money(x: float) -> str:
    return f"${x:,.2f}"


def long_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORDINAL_DAYS = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
                 "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
                 "seventeenth", "eighteenth", "nineteenth", "twentieth", "twenty-first", "twenty-second",
                 "twenty-third", "twenty-fourth", "twenty-fifth", "twenty-sixth", "twenty-seventh",
                 "twenty-eighth", "twenty-ninth", "thirtieth", "thirty-first"]


def words(n: int) -> str:
    """Whole dollars in words, as leases write them (up to the millions)."""
    if n == 0:
        return "zero"
    parts = []
    for div, name in ((1_000_000, "million"), (1_000, "thousand"), (100, "hundred")):
        if n >= div:
            parts.append(f"{words(n // div)} {name}")
            n %= div
    if n >= 20:
        parts.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def dollars_in_words(x: float) -> str:
    whole, frac = int(x), round((x - int(x)) * 100)
    return f"{words(whole).title()} and {frac:02d}/100 Dollars ({money(x)})"


def date_in_words(d: date) -> str:
    year = f"two thousand {words(d.year - 2000)}" if d.year > 2000 else "two thousand"
    return f"the {_ORDINAL_DAYS[d.day]} day of {d:%B}, {year}"


# --------------------------------------------------------------------------- document builder

BOILERPLATE = [
    ("INSURANCE", "Tenant shall, at its expense, maintain commercial general liability insurance with "
     "limits customary for similar retail uses, naming Landlord and Landlord's managing agent as additional "
     "insureds, and shall deliver certificates evidencing such coverage before entering the Premises."),
    ("INDEMNIFICATION", "Except to the extent caused by the negligence or willful misconduct of Landlord, "
     "Tenant shall indemnify, defend and hold Landlord harmless from all claims arising from the use or "
     "occupancy of the Premises or from any act or omission of Tenant or its agents, employees or invitees."),
    ("ASSIGNMENT AND SUBLETTING", "Tenant shall not assign this Lease or sublet all or any part of the "
     "Premises without Landlord's prior written consent, which shall not be unreasonably withheld. Any "
     "transfer of a controlling interest in Tenant shall be deemed an assignment."),
    ("DEFAULT", "If Tenant fails to pay any Rent within ten (10) days after written notice, or fails to "
     "perform any other obligation within thirty (30) days after written notice, Landlord may exercise all "
     "remedies available at law or in equity, including termination of this Lease."),
    ("CASUALTY", "If the Premises are damaged by fire or other casualty, Landlord shall restore the Premises "
     "to substantially their condition at delivery, unless this Lease is terminated as provided herein. "
     "Rent shall abate in proportion to the area of the Premises rendered untenantable."),
    ("CONDEMNATION", "If all of the Premises are taken by eminent domain, this Lease shall terminate as of "
     "the date of the taking. All awards shall belong to Landlord, except any separate award for Tenant's "
     "trade fixtures and moving expenses."),
    ("SUBORDINATION", "This Lease is subordinate to any mortgage or deed of trust now or hereafter "
     "encumbering the Shopping Center, provided the holder agrees not to disturb Tenant's possession so "
     "long as Tenant is not in default beyond applicable cure periods."),
    ("ESTOPPEL CERTIFICATES", "Within ten (10) business days after request, Tenant shall deliver a "
     "certificate confirming that this Lease is in full force and effect and stating any defaults known "
     "to Tenant, the dates to which Rent has been paid, and such other matters as Landlord may request."),
    ("HAZARDOUS MATERIALS", "Tenant shall not bring onto the Premises any hazardous materials except "
     "customary quantities of cleaning and office supplies used in compliance with applicable law."),
    ("SIGNS", "Tenant shall install a storefront sign conforming to the Shopping Center sign criteria "
     "within sixty (60) days after the Commencement Date, at Tenant's expense."),
    ("QUIET ENJOYMENT", "So long as Tenant is not in default, Tenant shall peaceably and quietly have, hold "
     "and enjoy the Premises during the Term without hindrance by Landlord."),
    ("NOTICES", "All notices shall be in writing and delivered by hand, by nationally recognized overnight "
     "courier, or by certified mail, return receipt requested, to the addresses set forth in the signature "
     "block, and shall be effective upon delivery or refusal."),
    ("SURRENDER", "Upon expiration or earlier termination, Tenant shall surrender the Premises broom clean "
     "and in good condition, ordinary wear and tear and casualty excepted, and shall remove its trade fixtures."),
    ("BROKERS", "Each party represents that it has dealt with no broker in connection with this Lease other "
     "than the brokers named in the Basic Lease Provisions, whose commissions Landlord shall pay."),
]


class Doc:
    """Builds a lease PDF from paragraphs, headings, tables, and page breaks."""

    def __init__(self, title: str):
        self.s = _styles()
        self.story = [Paragraph(title, self.s["title"]), Spacer(1, 6)]
        self._article = 0

    def h(self, text: str) -> None:
        self._article += 1
        self.story.append(Paragraph(f"ARTICLE {self._article}. {text}", self.s["h"]))

    def p(self, text: str) -> None:
        self.story.append(Paragraph(text, self.s["p"]))

    def table(self, rows: list[list[str]]) -> None:
        t = Table(rows, colWidths=[2.8 * inch, 2.0 * inch])
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Times-Roman", 10),
                               ("FONT", (0, 0), (-1, 0), "Times-Bold", 10),
                               ("GRID", (0, 0), (-1, -1), 0.5, "#555555")]))
        self.story += [Spacer(1, 4), t, Spacer(1, 6)]

    def boilerplate(self, start: int, count: int) -> None:
        for title, text in BOILERPLATE[start:start + count]:
            self.h(title)
            self.p(text)

    def page_break(self) -> None:
        self.story.append(PageBreak())

    def filler_pages(self, n: int) -> None:
        """Full pages of standard provisions, the bulk of any real lease."""
        for i in range(n):
            self.page_break()
            self.boilerplate((i * 5) % len(BOILERPLATE), 5)
            self.boilerplate((i * 5 + 5) % len(BOILERPLATE), 4)

    def signatures(self, tenant: str) -> None:
        self.story += [Spacer(1, 18),
                       Paragraph(f"LANDLORD: {LANDLORD}  By: ______________________", self.s["p"]),
                       Paragraph(f"TENANT: {tenant}  By: ______________________", self.s["p"])]

    def pdf(self) -> bytes:
        buf = io.BytesIO()
        SimpleDocTemplate(buf, pagesize=letter, leftMargin=inch, rightMargin=inch,
                          topMargin=inch, bottomMargin=inch).build(self.story)
        return buf.getvalue()


def steps_from(starts_and_rents: list[tuple[date, float]], expiration: date, sf: float) -> list[RentStep]:
    """Consecutive rent steps: each ends the day before the next begins; the last ends at expiration."""
    out = []
    for i, (start, rent) in enumerate(starts_and_rents):
        end = starts_and_rents[i + 1][0] - relativedelta(days=1) if i + 1 < len(starts_and_rents) else expiration
        out.append(RentStep(start_date=start, end_date=end, monthly_rent=rent,
                            annual_rent_psf=cents(rent * 12 / sf) if sf else None))
    return out


def renewal(deadline: date, months: int, text: str) -> LeaseOption:
    return LeaseOption(option_type=OptionType.RENEWAL, notice_deadline=deadline, term_months=months,
                       description=text)


@dataclass
class HardCase:
    code: str
    challenge: str
    abstract: LeaseAbstract
    pdf_bytes: bytes
    file_name: str = ""
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.file_name = self.file_name or f"{self.code} {self.abstract.trade_name or self.abstract.tenant_name}.pdf"


def _basic(doc: Doc, a: LeaseAbstract, lines: list[str]) -> None:
    doc.h("BASIC LEASE PROVISIONS")
    for line in lines:
        doc.p(line)


# --------------------------------------------------------------------------- the ten cases

def h1_prose_escalations() -> HardCase:
    sf, psf0, comm = 2350, 31.50, date(2025, 4, 1)
    exp = date(2030, 3, 31)
    psfs, psf = [], Decimal("31.50")
    for _ in range(5):
        psfs.append(float(psf))
        psf = (psf * Decimal("1.03")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rents = [(comm + relativedelta(years=y), cents(p * sf / 12)) for y, p in enumerate(psfs)]
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Bluebird Yoga Studio LLC", trade_name="Bluebird Yoga",
        property_name=CENTER, property_address=ADDRESS, suite="B-7", square_feet=sf,
        lease_date=date(2025, 2, 14), commencement_date=comm, rent_commencement_date=comm, expiration_date=exp,
        base_rent_schedule=steps_from(rents, exp, sf), security_deposit=rents[0][1],
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=4.12, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_type=CapType.NONE, admin_fee_pct=15.0),
        permitted_use="yoga and fitness studio")
    d = Doc("SHOPPING CENTER LEASE")
    d.p(f"THIS LEASE is made as of {long_date(a.lease_date)} between {LANDLORD} (\"Landlord\") and "
        f"{a.tenant_name}, doing business as \"{a.trade_name}\" (\"Tenant\").")
    _basic(d, a, [f"Shopping Center: {CENTER}, {ADDRESS}.", f"Premises: Suite {a.suite}, approximately "
                  f"{sf:,} rentable square feet.", f"Commencement Date and Rent Commencement Date: "
                  f"{long_date(comm)}.", f"Term: five (5) Lease Years, expiring {long_date(exp)}.",
                  f"Security Deposit: an amount equal to the first month's Base Rent, being {money(rents[0][1])}.",
                  "Permitted Use: yoga and fitness studio."])
    d.h("BASE RENT")
    d.p(f"Tenant shall pay annual Base Rent at the rate of {money(psf0)} per rentable square foot for the first "
        "Lease Year. On each anniversary of the Rent Commencement Date, the annual rate per square foot shall "
        "increase to one hundred three percent (103%) of the rate in effect for the immediately preceding Lease "
        "Year, rounded to the nearest cent. Base Rent is payable in equal monthly installments of one-twelfth "
        "of the annual Base Rent, each rounded to the nearest cent, in advance on the first day of each month.")
    d.h("OPERATING COSTS")
    d.p("Tenant shall pay Tenant's Proportionate Share, being four and twelve hundredths percent (4.12%), of "
        "Common Area Maintenance Costs, Real Estate Taxes and Insurance Costs. Common Area Maintenance Costs "
        "shall include an administrative fee of fifteen percent (15%) of such costs.")
    d.boilerplate(0, 6)
    d.signatures(a.tenant_name)
    return HardCase("H1", "Rent schedule described in prose; Tenant must compute each year's rent", a, d.pdf())


def h2_dates_by_formula() -> HardCase:
    sf, delivery = 1850, date(2025, 3, 3)
    comm = delivery + relativedelta(days=90)           # June 1, 2025
    rcd = comm + relativedelta(days=60)                # July 31, 2025
    exp = (rcd + relativedelta(day=1, months=1)) + relativedelta(months=60, days=-1)  # July 31, 2030
    monthly = [4200.00, 4326.00, 4455.78, 4589.45, 4727.13]
    first_full = rcd + relativedelta(day=1, months=1)  # August 1, 2025
    starts = [rcd] + [first_full + relativedelta(years=y) for y in range(1, 5)]
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Kettle & Crumb Bakery, Inc.", trade_name="Kettle & Crumb",
        property_name=CENTER, property_address=ADDRESS, suite="104", square_feet=sf,
        lease_date=date(2025, 1, 14), commencement_date=comm, rent_commencement_date=rcd, expiration_date=exp,
        base_rent_schedule=steps_from(list(zip(starts, monthly)), exp, sf), security_deposit=8400.00,
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=3.08, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_pct=5.0,
                              cam_cap_type=CapType.NON_CUMULATIVE, admin_fee_pct=10.0),
        options=[renewal(exp - relativedelta(days=180), 60, "One renewal option of five years.")],
        permitted_use="retail bakery and cafe")
    d = Doc("RETAIL LEASE AGREEMENT")
    d.p(f"THIS RETAIL LEASE AGREEMENT is made and entered into as of {date_in_words(a.lease_date)}, by and "
        f"between {LANDLORD} (\"Landlord\") and {a.tenant_name}, d/b/a {a.trade_name} (\"Tenant\").")
    _basic(d, a, [f"Premises: Suite {a.suite} of {CENTER}, {ADDRESS}, containing {sf:,} square feet.",
                  f"Delivery Date: Landlord delivered the Premises to Tenant on {date_in_words(delivery)}.",
                  "Commencement Date: the date that is ninety (90) days after the Delivery Date.",
                  "Rent Commencement Date: the date that is sixty (60) days after the Commencement Date.",
                  "Expiration Date: the last day of the sixtieth (60th) full calendar month following the Rent "
                  "Commencement Date.",
                  f"Security Deposit: {dollars_in_words(8400.00)}.", "Permitted Use: retail bakery and cafe."])
    d.h("BASE RENT")
    d.p("Base Rent shall be payable beginning on the Rent Commencement Date in the monthly amounts below. "
        "\"Lease Month 1\" begins on the first day of the first full calendar month after the Rent Commencement "
        "Date; Base Rent for any partial month before Lease Month 1 shall be paid at the Lease Month 1 rate.")
    d.table([["Lease Months", "Monthly Base Rent"]] + [[f"{12 * i + 1} - {12 * i + 12}", money(m)]
                                                         for i, m in enumerate(monthly)])
    d.h("OPERATING EXPENSES")
    d.p("Tenant shall pay three and eight hundredths percent (3.08%) of Operating Expenses, Taxes and Insurance. "
        "Tenant's share of controllable Operating Expenses shall not increase by more than five percent (5%) "
        "over the amount payable for the prior calendar year. Operating Expenses include a ten percent (10%) "
        "administrative fee.")
    d.h("OPTION TO EXTEND")
    d.p("Tenant shall have one (1) option to extend the Term for five (5) years by written notice given at least "
        "one hundred eighty (180) days before the Expiration Date.")
    d.boilerplate(2, 6)
    d.signatures(a.tenant_name)
    return HardCase("H2", "Dates written in words or defined by formulas; rent table in lease months", a, d.pdf())


def h3_notice_windows() -> HardCase:
    sf, comm, exp = 12600, date(2025, 1, 1), date(2034, 12, 31)
    rents = [(comm, cents(16.00 * sf / 12)), (date(2030, 1, 1), cents(17.60 * sf / 12))]
    second_exp = comm + relativedelta(years=15, days=-1)
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Northgate Hardware Co.", trade_name="Northgate Hardware",
        property_name=CENTER, property_address=ADDRESS, suite="A-2", square_feet=sf,
        lease_date=date(2024, 9, 10), commencement_date=comm, rent_commencement_date=comm, expiration_date=exp,
        base_rent_schedule=steps_from(rents, exp, sf), security_deposit=None,
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=14.65, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_type=CapType.NONE,
                              admin_fee_pct=12.0, gross_up_pct=95.0),
        options=[renewal(exp - relativedelta(months=9), 60, "First of two five-year extension options."),
                 renewal(second_exp - relativedelta(months=9), 60, "Second of two five-year extension options.")],
        permitted_use="hardware, home improvement and garden supplies")
    d = Doc("LEASE")
    d.p(f"This Lease is dated {long_date(a.lease_date)} between {LANDLORD} (\"Landlord\") and "
        f"{a.tenant_name}, which operates as \"{a.trade_name}\" (\"Tenant\").")
    _basic(d, a, [f"Premises: Space {a.suite}, {CENTER}, approximately {sf:,} square feet.",
                  f"Term: ten (10) years commencing {long_date(comm)} and ending {long_date(exp)}.",
                  "Security Deposit: None. No security deposit shall be required.",
                  "Annual Base Rent: Years 1-5, $16.00 per square foot; Years 6-10, $17.60 per square foot, "
                  "payable in equal monthly installments."])
    d.h("EXTENSION OPTIONS")
    d.p("Tenant shall have two (2) successive options to extend the Term, each for five (5) years. Each option "
        "shall be exercised, if at all, by written notice given not less than nine (9) months nor more than "
        "twelve (12) months prior to the expiration of the then-current Term. The second option may be "
        "exercised only if the first option was exercised.")
    d.h("OPERATING COSTS")
    d.p("Tenant shall pay fourteen and sixty-five hundredths percent (14.65%) of Operating Costs, Taxes and "
        "Insurance, plus an administrative fee of twelve percent (12%) of Operating Costs. Variable Operating "
        "Costs shall be adjusted to reflect ninety-five percent (95%) occupancy.")
    d.boilerplate(4, 6)
    d.signatures(a.tenant_name)
    return HardCase("H3", "Option notice windows in months; two options in a series", a, d.pdf())


def h4_lease_with_amendment() -> HardCase:
    sf, comm, orig_exp, ext_exp = 1600, date(2019, 7, 1), date(2024, 6, 30), date(2029, 6, 30)
    orig = [4000.00]
    for _ in range(4):
        orig.append(cents(orig[-1] * 1.03))
    ext = [4650.00]
    for _ in range(4):
        ext.append(cents(ext[-1] * 1.03))
    starts = [comm + relativedelta(years=y) for y in range(10)]
    a = LeaseAbstract(
        document_type=DocumentType.LEASE, landlord=LANDLORD, tenant_name="Juniper Thai Kitchen LLC",
        trade_name="Juniper Thai", property_name=CENTER, property_address=ADDRESS, suite="C-3", square_feet=sf,
        lease_date=date(2019, 5, 1), commencement_date=comm, rent_commencement_date=comm, expiration_date=ext_exp,
        base_rent_schedule=steps_from(list(zip(starts, orig + ext)), ext_exp, sf), security_deposit=4000.00,
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=2.5, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_type=CapType.NONE, admin_fee_pct=15.0),
        options=[renewal(ext_exp - relativedelta(months=6), 60, "One option to extend five years (granted by the "
                                                               "First Amendment, replacing the original option).")],
        permitted_use="restaurant")
    d = Doc("SHOPPING CENTER LEASE")
    d.p(f"THIS LEASE is made as of {long_date(a.lease_date)} between {LANDLORD} (\"Landlord\") and "
        f"{a.tenant_name}, d/b/a {a.trade_name} (\"Tenant\").")
    _basic(d, a, [f"Premises: Suite {a.suite}, {CENTER}, {ADDRESS}; {sf:,} square feet.",
                  f"Term: five (5) years from {long_date(comm)} through {long_date(orig_exp)}.",
                  f"Security Deposit: {money(4000.00)}.", "Tenant's Proportionate Share: 2.50%."])
    d.h("BASE RENT")
    d.table([["Period", "Monthly Base Rent"]] + [[f"{starts[i]:%m/%d/%Y} - {starts[i + 1] - relativedelta(days=1):%m/%d/%Y}",
                                                   money(r)] for i, r in enumerate(orig)])
    d.h("RECOVERIES")
    d.p("Tenant shall pay Tenant's Proportionate Share of CAM, Taxes and Insurance. CAM includes an administrative "
        "fee of fifteen percent (15%).")
    d.h("OPTION TO RENEW")
    d.p("Tenant may renew for one (1) term of five (5) years by notice given at least one hundred eighty (180) "
        "days before the expiration of the Term.")
    d.boilerplate(0, 5)
    d.signatures(a.tenant_name)
    d.page_break()
    d.story.append(Paragraph("FIRST AMENDMENT TO LEASE", d.s["title"]))
    d.p(f"This First Amendment is made as of {long_date(date(2024, 3, 15))} between {LANDLORD} and "
        f"{a.tenant_name}, amending the Lease dated {long_date(a.lease_date)}.")
    d.p(f"1. Extension. The Term is extended for five (5) years, from {long_date(orig_exp + relativedelta(days=1))} "
        f"through {long_date(ext_exp)} (the \"Extended Term\").")
    d.p("2. Base Rent. Base Rent during the Extended Term shall be as follows:")
    d.table([["Period", "Monthly Base Rent"]] + [[f"{starts[5 + i]:%m/%d/%Y} - "
                                                   f"{(starts[6 + i] if 6 + i < 10 else ext_exp + relativedelta(days=1)) - relativedelta(days=1):%m/%d/%Y}",
                                                   money(r)] for i, r in enumerate(ext)])
    d.p("3. Option. The option to renew in the Lease is deleted. Tenant shall have one (1) option to extend the "
        "Extended Term for five (5) years by written notice given not less than six (6) months prior to the "
        "expiration of the Extended Term.")
    d.p("4. Ratification. Except as amended hereby, the Lease remains in full force and effect.")
    d.signatures(a.tenant_name)
    return HardCase("H4", "Lease and first amendment in one file; abstract the terms as amended", a, d.pdf())


def h5_handwritten_corrections() -> HardCase:
    sf, comm, exp = 2240, date(2025, 10, 1), date(2030, 9, 30)
    r1, r2 = cents(25.00 * sf / 12), cents(26.50 * sf / 12)
    rents = [(comm, r1), (date(2027, 10, 1), r2)]
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Pinyon Pet Grooming LLC", trade_name="Pinyon Pet Spa",
        property_name=CENTER, property_address=ADDRESS, suite="112", square_feet=sf,
        lease_date=date(2025, 8, 1), commencement_date=comm, rent_commencement_date=comm, expiration_date=exp,
        base_rent_schedule=steps_from(rents, exp, sf), security_deposit=9400.00,
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=3.49, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_type=CapType.NONE),
        permitted_use="pet grooming and pet supply retail")
    fix = lambda old, new: f"<strike>{old}</strike> <i>{new}</i> <font size=8>(RN / KT)</font>"  # noqa: E731
    d = Doc("LEASE AGREEMENT")
    d.p(f"THIS LEASE AGREEMENT is made {long_date(a.lease_date)} by {LANDLORD} (\"Landlord\") and "
        f"{a.tenant_name}, doing business as {a.trade_name} (\"Tenant\").")
    _basic(d, a, [f"Premises: Suite {a.suite}, {CENTER}, containing approximately {fix('2,180', '2,240')} "
                  "square feet.",
                  f"Term: commencing {long_date(comm)} and expiring September 30, {fix('2029', '2030')}.",
                  f"Tenant's Proportionate Share: {fix('3.40%', '3.49%')}.",
                  f"Security Deposit: {fix('$9,000.00', '$9,400.00')}.",
                  "Changes made by hand and initialed by both parties are part of this Lease."])
    d.h("BASE RENT")
    d.table([["Period", "Monthly Base Rent"],
             [f"{comm:%m/%d/%Y} - 09/30/2027", money(r1)], ["10/01/2027 - 09/30/2030", money(r2)]])
    d.h("RECOVERIES")
    d.p("Tenant shall pay its Proportionate Share of Common Area Maintenance Costs, Real Estate Taxes and "
        "Insurance Costs as Additional Rent.")
    d.boilerplate(6, 5)
    d.signatures(a.tenant_name)
    return HardCase("H5", "Typed figures struck through and corrected by hand, initialed", a,
                    make_scanned(d.pdf(), seed=5))


def h6_degraded_long_scan() -> HardCase:
    sf, comm, exp = 4100, date(2024, 2, 1), date(2034, 1, 31)
    rents = [(comm, cents(24 * sf / 12)), (date(2027, 2, 1), cents(26 * sf / 12)),
             (date(2030, 2, 1), cents(28 * sf / 12))]
    breakpoint_ = cents(24 * sf / 0.05)
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Red Mesa Cantina, Inc.", trade_name="Red Mesa Cantina",
        guarantor="Luis Ortega", property_name=CENTER, property_address=ADDRESS, suite="D-1", square_feet=sf,
        lease_date=date(2023, 11, 20), commencement_date=comm, rent_commencement_date=comm, expiration_date=exp,
        base_rent_schedule=steps_from(rents, exp, sf), security_deposit=16400.00,
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=6.21, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_pct=5.0,
                              cam_cap_type=CapType.CUMULATIVE, admin_fee_pct=15.0, gross_up_pct=95.0),
        percentage_rent=PercentageRent(rate_pct=5.0, breakpoint_type="natural", breakpoint_amount=breakpoint_),
        options=[renewal(exp - relativedelta(days=270), 60, "One renewal option of five years.")],
        permitted_use="full-service Mexican restaurant with bar")
    d = Doc("SHOPPING CENTER LEASE")
    d.p(f"THIS LEASE is made as of {long_date(a.lease_date)} between {LANDLORD} (\"Landlord\") and "
        f"{a.tenant_name}, d/b/a {a.trade_name} (\"Tenant\").")
    _basic(d, a, [f"Premises: Suite {a.suite}, {CENTER}, {ADDRESS}; {sf:,} square feet.",
                  f"Term: ten (10) years, from {long_date(comm)} through {long_date(exp)}.",
                  "Base Rent: see Article 5.  Percentage Rent: see Article 6.  Options: see Article 12.",
                  f"Security Deposit: {money(16400.00)}.  Guarantor: {a.guarantor}."])
    d.filler_pages(3)
    d.page_break()
    d.h("BASE RENT")
    d.p("Tenant shall pay annual Base Rent per square foot as follows, in equal monthly installments: Lease "
        "Years 1 through 3, $24.00; Lease Years 4 through 6, $26.00; Lease Years 7 through 10, $28.00.")
    d.h("PERCENTAGE RENT")
    d.p(f"Tenant shall pay as Percentage Rent five percent (5%) of Gross Sales in each Lease Year in excess of "
        f"the natural breakpoint, which for the first Lease Year is {money(breakpoint_)}.")
    d.filler_pages(3)
    d.page_break()
    d.h("COMMON AREA AND OPERATING COSTS")
    d.p("Tenant shall pay Tenant's Proportionate Share, six and twenty-one hundredths percent (6.21%), of Common "
        "Area Costs, Real Estate Taxes and Insurance Costs. Controllable Common Area "
        "Costs shall not increase by more than five percent (5%) per year on a cumulative, compounding basis. "
        "An administrative fee of fifteen percent (15%) applies. Variable costs shall be grossed up to "
        "ninety-five percent (95%) occupancy.")
    d.filler_pages(3)
    d.page_break()
    d.h("OPTION TO RENEW")
    d.p("Tenant shall have one (1) option to renew for five (5) years, exercisable by written notice delivered "
        "no later than two hundred seventy (270) days prior to the Expiration Date, time being of the essence.")
    d.filler_pages(2)
    d.signatures(a.tenant_name)
    d.page_break()
    d.p("EXHIBIT A - LEGAL DESCRIPTION. Lot 4, Block 2, Cedar Point Subdivision Filing No. 3, County of "
        "Jefferson, State of Colorado.")
    return HardCase("H6", "16-page degraded scan; key terms buried in later articles", a,
                    make_scanned(d.pdf(), seed=6, resolution=100, blur=0.6, max_rotate=2.0, speckle=0.0015))


def h7_formula_share_indirect_cap() -> HardCase:
    sf, gla, comm, exp = 2760, 64200, date(2025, 5, 1), date(2032, 4, 30)
    rents = [(comm, cents(28.75 * sf / 12)), (date(2029, 5, 1), cents(31.00 * sf / 12))]
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Summit Optical Group PC", trade_name="Summit Eyecare",
        property_name=CENTER, property_address=ADDRESS, suite="E-5", square_feet=sf,
        lease_date=date(2025, 3, 3), commencement_date=comm, rent_commencement_date=comm, expiration_date=exp,
        base_rent_schedule=steps_from(rents, exp, sf), security_deposit=rents[0][1],
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=round(sf / gla * 100, 2),
                              pays_cam=True, pays_taxes=True, pays_insurance=True, cam_cap_pct=4.0,
                              cam_cap_type=CapType.CUMULATIVE, admin_fee_pct=10.0, gross_up_pct=95.0),
        options=[renewal(exp - relativedelta(days=270), 60, "One renewal option of five years.")],
        permitted_use="optometry practice and optical retail")
    d = Doc("LEASE")
    d.p(f"THIS LEASE dated {long_date(a.lease_date)} is between {LANDLORD} (\"Landlord\") and {a.tenant_name}, "
        f"d/b/a {a.trade_name} (\"Tenant\").")
    _basic(d, a, [f"Premises: Unit {a.suite}, {CENTER}, {ADDRESS}; {sf:,} square feet of floor area.",
                  f"Term: seven (7) years beginning {long_date(comm)}.",
                  "Annual Base Rent: Lease Years 1-4, $28.75 per square foot; Lease Years 5-7, $31.00 per "
                  "square foot, payable monthly.",
                  "Security Deposit: an amount equal to one month of Base Rent at the initial rate."])
    d.h("TENANT'S SHARE OF OPERATING COSTS")
    d.p(f"\"Tenant's Share\" means a fraction, the numerator of which is the floor area of the Premises and the "
        f"denominator of which is the floor area of all leasable buildings in the Shopping Center, currently "
        f"{gla:,} square feet. Tenant shall pay Tenant's Share of Operating Costs, Taxes and Insurance.")
    d.p("Tenant's Share of Controllable Operating Costs for any calendar year shall not exceed Tenant's Share of "
        "Controllable Operating Costs for the first full calendar year of the Term, increased by four percent "
        "(4%) per year, compounded annually. Operating Costs include a management fee equal to ten percent "
        "(10%) of Operating Costs, excluding Taxes and Insurance. If the Shopping Center is less than fully "
        "occupied, variable Operating Costs shall be computed as if it were ninety-five percent (95%) occupied.")
    d.h("RENEWAL")
    d.p("Tenant may renew this Lease for one (1) period of five (5) years by written notice given at least two "
        "hundred seventy (270) days but not more than three hundred sixty-five (365) days before the end of the "
        "Term.")
    d.boilerplate(1, 6)
    d.signatures(a.tenant_name)
    return HardCase("H7", "Pro-rata share given only as a formula; cumulative cap described indirectly", a, d.pdf())


def h8_modified_gross_annual_totals() -> HardCase:
    sf, comm, exp = 1950, date(2025, 9, 1), date(2030, 8, 31)
    annual = [54600.00, 56238.00, 57925.14, 59662.89, 61452.78]
    rents = [(comm + relativedelta(years=y), cents(x / 12)) for y, x in enumerate(annual)]
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Clearwater Family Dental, P.C.", trade_name=None,
        property_name=CENTER, property_address=ADDRESS, suite="210", square_feet=sf,
        lease_date=date(2025, 6, 12), commencement_date=comm, rent_commencement_date=comm, expiration_date=exp,
        base_rent_schedule=steps_from(rents, exp, sf), security_deposit=rents[0][1],
        recoveries=Recoveries(structure=RecoveryStructure.MODIFIED_GROSS, pro_rata_share_pct=4.41, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_type=CapType.NONE, base_year=2025,
                              gross_up_pct=95.0),
        permitted_use="general dentistry")
    d = Doc("OFFICE / RETAIL LEASE")
    d.p(f"This Lease is entered into on {long_date(a.lease_date)} between {LANDLORD} (\"Landlord\") and "
        f"{a.tenant_name} (\"Tenant\").")
    _basic(d, a, [f"Premises: Suite {a.suite}, second floor of the professional building at {CENTER}, "
                  f"{ADDRESS}; {sf:,} rentable square feet.",
                  f"Term: five (5) years, from {long_date(comm)} to {long_date(exp)}.",
                  "Security Deposit: one (1) month's Base Rent at the rate in effect for the first Lease Year."])
    d.h("BASE RENT")
    d.p("Tenant shall pay the following Annual Base Rent, in twelve equal monthly installments:")
    d.table([["Lease Year", "Annual Base Rent"]] + [[str(i + 1), money(x)] for i, x in enumerate(annual)])
    d.h("OPERATING EXPENSES")
    d.p("Base Rent includes Tenant's share of Operating Expenses, Taxes and Insurance for calendar year 2025 "
        "(the \"Base Year\"). For each later calendar year, Tenant shall pay Tenant's Share, 4.41%, of the "
        "amount by which Operating Expenses, Taxes and Insurance exceed those for the Base Year. Operating "
        "Expenses for the Base Year and each later year shall be grossed up to ninety-five percent (95%) "
        "occupancy.")
    d.boilerplate(3, 6)
    d.signatures(a.tenant_name)
    return HardCase("H8", "Modified gross with base year; rent only as annual totals; deposit by formula", a, d.pdf())


def h9_percentage_rent_options() -> HardCase:
    sf, comm, exp = 8000, date(2025, 3, 1), date(2035, 2, 28)
    rents = [(comm, cents(18.00 * sf / 12)), (date(2030, 3, 1), cents(19.80 * sf / 12))]
    # Each later term ends 5 years further out, counted from commencement so leap days land
    # correctly: the second option term ends February 29, 2040.
    options = [renewal(comm + relativedelta(years=10 + 5 * k, days=-1) - relativedelta(days=180), 60,
                       f"Renewal option {k + 1} of 3, five years.") for k in range(3)]
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Aspen Ridge Outfitters LLC", trade_name="Aspen Ridge Outfitters",
        property_name=CENTER, property_address=ADDRESS, suite="F-1", square_feet=sf,
        lease_date=date(2024, 12, 1), commencement_date=comm, rent_commencement_date=comm, expiration_date=exp,
        base_rent_schedule=steps_from(rents, exp, sf), security_deposit=None,
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=9.88, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_pct=6.0,
                              cam_cap_type=CapType.NON_CUMULATIVE, admin_fee_pct=15.0),
        percentage_rent=PercentageRent(rate_pct=6.0, breakpoint_type="artificial", breakpoint_amount=2500000.0),
        options=options, radius_restriction="5 miles", permitted_use="outdoor apparel and equipment")
    d = Doc("SHOPPING CENTER LEASE")
    d.p(f"THIS LEASE is made as of {long_date(a.lease_date)} between {LANDLORD} (\"Landlord\") and {a.tenant_name} "
        "(\"Tenant\"), which will operate the Premises under the name \"Aspen Ridge Outfitters\".")
    _basic(d, a, [f"Premises: Space {a.suite}, {CENTER}; {sf:,} square feet.",
                  f"Term: ten (10) years commencing {long_date(comm)} and expiring {long_date(exp)}.",
                  "Annual Minimum Rent: $18.00 per square foot for Lease Years 1-5 and $19.80 per square foot for "
                  "Lease Years 6-10, payable monthly.",
                  "Security Deposit: Landlord waives any security deposit."])
    d.h("PERCENTAGE RENT")
    d.p("In addition to Minimum Rent, Tenant shall pay six percent (6%) of Gross Sales in each Lease Year in excess "
        "of Two Million Five Hundred Thousand and No/100 Dollars ($2,500,000.00) (the \"Breakpoint\"). The "
        "Breakpoint is fixed and shall not be adjusted when Minimum Rent changes. Tenant shall report Gross "
        "Sales monthly within fifteen (15) days after the end of each month.")
    d.h("RADIUS")
    d.p("During the Term, Tenant shall not operate a similar store within five (5) miles of the Shopping Center.")
    d.h("COMMON AREA COSTS")
    d.p("Tenant shall pay 9.88% of Common Area Costs, Taxes and Insurance. An administrative charge of fifteen "
        "percent (15%) of Common Area Costs applies. Controllable Common Area Costs payable by Tenant shall not "
        "increase by more than six percent (6%) over the prior year, on a non-cumulative basis.")
    d.h("RENEWAL OPTIONS")
    d.p("Tenant shall have three (3) consecutive options to renew the Term for five (5) years each, each "
        "exercisable by written notice given at least one hundred eighty (180) days before the end of the "
        "then-current Term.")
    d.boilerplate(5, 6)
    d.signatures(a.tenant_name)
    return HardCase("H9", "Artificial breakpoint; three renewal options; deposit waived", a, d.pdf())


def h10_distractors_and_exhibits() -> HardCase:
    sf, comm, rcd = 3050, date(2025, 8, 1), date(2025, 11, 1)
    exp = rcd + relativedelta(months=60, days=-1)                    # October 31, 2030
    rent = cents(22.00 * sf / 12)
    deposit = cents(rent * 2)
    a = LeaseAbstract(
        landlord=LANDLORD, tenant_name="Lantern Lane Books, LLC", trade_name="Lantern Lane Books",
        guarantor="Margaret Ellison", property_name=CENTER, property_address=ADDRESS, suite="G-4", square_feet=sf,
        lease_date=date(2025, 7, 7), commencement_date=comm, rent_commencement_date=rcd, expiration_date=exp,
        base_rent_schedule=steps_from([(rcd, rent)], exp, sf), security_deposit=deposit,
        recoveries=Recoveries(structure=RecoveryStructure.NNN, pro_rata_share_pct=4.75, pays_cam=True,
                              pays_taxes=True, pays_insurance=True, cam_cap_type=CapType.NONE, admin_fee_pct=12.0),
        options=[renewal(exp - relativedelta(days=180), 60, "One renewal option of five years.")],
        permitted_use="bookstore and cafe")
    d = Doc("RETAIL LEASE")
    d.p(f"THIS RETAIL LEASE is made as of {long_date(a.lease_date)} between {LANDLORD} (\"Landlord\") and "
        f"{a.tenant_name}, doing business as {a.trade_name} (\"Tenant\").")
    d.p("RECITALS. The Premises were formerly occupied by Sunset Video Rentals, which leased 3,600 square feet at "
        "$19.50 per square foot under a lease that expired December 31, 2019. Landlord's lender, Front Range "
        "Savings, holds a deed of trust securing a loan of $4,250,000.00.")
    _basic(d, a, [f"Premises: Suite {a.suite}, {CENTER}, {ADDRESS}; {sf:,} square feet (after demising).",
                  f"Commencement Date: {long_date(comm)}.  Rent Commencement Date: {long_date(rcd)}.",
                  "Term: ending on the last day of the sixtieth (60th) month after the Rent Commencement Date.",
                  f"Base Rent: $22.00 per square foot per year for the entire Term, payable monthly.",
                  f"Security Deposit: {dollars_in_words(deposit)}.",
                  "Tenant pays its Proportionate Share, 4.75%, of CAM, Real Estate Taxes and Insurance. "
                  "Administrative Fee: twelve percent (12%) of CAM.",
                  "Guarantor: see Exhibit D."])
    d.h("RENEWAL")
    d.p("Tenant may extend the Term once, for five (5) years, by notice given at least one hundred eighty (180) "
        "days before the Expiration Date.")
    d.boilerplate(0, 7)
    d.signatures(a.tenant_name)
    d.page_break()
    d.story.append(Paragraph("EXHIBIT D - GUARANTY OF LEASE", d.s["title"]))
    d.p(f"For value received, the undersigned, Margaret Ellison, an individual residing in Lakewood, Colorado "
        f"(\"Guarantor\"), unconditionally guarantees to Landlord the payment and performance of all obligations "
        f"of {a.tenant_name} under the foregoing Lease.")
    d.p("GUARANTOR: ______________________  Margaret Ellison")
    d.page_break()
    d.story.append(Paragraph("EXHIBIT F - CENTER DIRECTORY (for information only)", d.s["title"]))
    d.table([["Tenant", "Square Feet"], ["Cedar Point Grocery", "38,500"], ["Mountain Mattress", "5,200"],
             ["Prairie Wireless", "1,400"], [a.trade_name, f"{sf:,}"], ["Vacant (Suite G-6)", "2,250"]])
    return HardCase("H10", "Distractor figures, amounts in words, guarantor named only in an exhibit", a, d.pdf())


CASES = [h1_prose_escalations, h2_dates_by_formula, h3_notice_windows, h4_lease_with_amendment,
         h5_handwritten_corrections, h6_degraded_long_scan, h7_formula_share_indirect_cap,
         h8_modified_gross_annual_totals, h9_percentage_rent_options, h10_distractors_and_exhibits]


def build_hard_cases() -> list[HardCase]:
    return [make() for make in CASES]
