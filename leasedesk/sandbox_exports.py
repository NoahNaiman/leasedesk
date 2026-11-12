"""Fictional accounting-system exports for the sandbox, with known problems planted in them.

``rent_roll`` writes a rent roll laid out like a property accounting report (title lines,
a header row, one subtotal row per property) from the sandbox's lease terms. It plants one
of each problem the rent roll check looks for and returns them as an answer key, so the demo
shows every kind of finding and the tests can check each one is found.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta

from leasedesk import portfolio, rentroll
from leasedesk.db import LeaseRecord

RENT_ROLL_FILE = "Breeze Rent Roll.csv"
UNKNOWN_TENANT = ("Willow Creek Commons", "C-250", "Gladstone Optical LLC", 1650, 3712.50)


@dataclass(frozen=True)
class Planted:
    kind: str
    tenant: str


def rent_roll(approved: list[LeaseRecord], awaiting_review: list[LeaseRecord],
              as_of: date) -> tuple[bytes, list[Planted]]:
    groups = portfolio.tenant_groups(approved)
    planted: list[Planted] = []
    lines: dict[str, list[list]] = {}

    def row(g_or_abstract, prop, rent, *, end=None, sf=None):
        a = g_or_abstract.terms if hasattr(g_or_abstract, "terms") else g_or_abstract
        name = a.tenant_name.split(",")[0] if a.tenant_name else portfolio.tenant_label(a)
        lines.setdefault(prop, []).append([
            prop, a.suite, name,
            f"{a.commencement_date:%m/%d/%Y}" if a.commencement_date else "",
            f"{(end or a.expiration_date):%m/%d/%Y}" if (end or a.expiration_date) else "",
            f"{(sf or a.square_feet):,.0f}" if (sf or a.square_feet) else "",
            f"{rent:,.2f}" if rent is not None else ""])

    for g in groups:
        t, rent = g.terms, g.terms.current_monthly_rent(as_of)
        if g.name == "FreshWay Market":
            # The step that began most recently was never entered: still billing the one before it.
            steps = [s for s in t.base_rent_schedule if s.start_date and s.start_date <= as_of]
            row(g, g.property, steps[-2].monthly_rent)
            planted.append(Planted(rentroll.KIND_MISSED_ESCALATION, g.name))
        elif g.name == "Brightside Pharmacy":
            row(g, g.property, rent + 150)
            planted.append(Planted(rentroll.KIND_RENT_MISMATCH, g.name))
        elif g.name == "Copper Kettle Coffee":
            planted.append(Planted(rentroll.KIND_NOT_IN_RENT_ROLL, g.name))
        elif g.name == "Golden Dragon Kitchen":
            row(g, g.property, rent, end=t.expiration_date + relativedelta(years=1))
            planted.append(Planted(rentroll.KIND_EXPIRATION, g.name))
        elif g.name == "Paws & Claws Pet Supply":
            row(g, g.property, rent, sf=t.square_feet + 50)
            planted.append(Planted(rentroll.KIND_SF, g.name))
        else:
            row(g, g.property, rent)
    for r in awaiting_review:  # billed tenants whose leases are still in the review queue
        a = r.ai_abstract
        row(a, r.property_name or portfolio.property_label(a), a.current_monthly_rent(as_of))
        planted.append(Planted(rentroll.KIND_AWAITING_REVIEW, portfolio.tenant_label(a)))
    prop, unit, name, sf, rent = UNKNOWN_TENANT
    lines.setdefault(prop, []).append([prop, unit, name, "03/01/2024", "02/28/2029", f"{sf:,}", f"{rent:,.2f}"])
    planted.append(Planted(rentroll.KIND_NOT_IN_LEASEDESK, name))

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Rent Roll"])
    w.writerow([f"As of {as_of:%m/%d/%Y}"])
    w.writerow([])
    w.writerow(["Property", "Unit", "Tenant", "Lease From", "Lease To", "Sq Ft", "Rent"])
    for prop in sorted(lines):
        rows = sorted(lines[prop], key=lambda r: r[1] or "")
        w.writerows(rows)
        total = sum(float(r[6].replace(",", "")) for r in rows if r[6])
        w.writerow([prop, "", "Total", "", "", "", f"{total:,.2f}"])
    w.writerow(["", "", "Grand Total", "", "", "", ""])
    return buf.getvalue().encode(), planted
