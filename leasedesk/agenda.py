"""The day's agenda: everything in the portfolio that falls on a given day.

Draws on each tenant's merged current terms (option deadlines, expirations, rent increases,
lease and rent commencements) and on the newest insurance certificate on file per tenant.
Two kinds of item are advance reminders rather than the event itself, so there is time to act:
an option notice deadline 90 and 30 days out, and a certificate entering the "expiring soon"
window set in the insurance requirements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from leasedesk import insurance, portfolio

KIND_OPTION_DEADLINE = "Option notice deadline"
KIND_OPTION_REMINDER = "Option deadline coming up"
KIND_EXPIRATION = "Lease expires"
KIND_RENT_INCREASE = "Rent increase"
KIND_COMMENCEMENT = "Lease begins"
KIND_RENT_COMMENCEMENT = "Rent begins"
KIND_COI_EXPIRES = "Insurance certificate expires"
KIND_COI_REMINDER = "Insurance certificate expiring soon"

# Most urgent first: deadlines that can be missed, then money changes, then reminders.
KIND_ORDER = [KIND_OPTION_DEADLINE, KIND_COI_EXPIRES, KIND_EXPIRATION, KIND_RENT_INCREASE,
              KIND_RENT_COMMENCEMENT, KIND_COMMENCEMENT, KIND_OPTION_REMINDER, KIND_COI_REMINDER]
OPTION_REMINDER_DAYS = (90, 30)

_FROM_CRITICAL = {portfolio.EVENT_OPTION: KIND_OPTION_DEADLINE, portfolio.EVENT_EXPIRATION: KIND_EXPIRATION,
                  portfolio.EVENT_RENT_INCREASE: KIND_RENT_INCREASE}


@dataclass(frozen=True)
class Item:
    when: date
    kind: str
    property: str
    tenant: str
    suite: str | None
    detail: str


def _lease_items(g: portfolio.TenantGroup) -> list[Item]:
    t = g.terms
    out = []
    for e in portfolio.lease_events(g.primary.id, t):
        out.append(Item(e.when, _FROM_CRITICAL[e.event], g.property, g.name, t.suite, e.detail))
        if e.event == portfolio.EVENT_OPTION:
            for days in OPTION_REMINDER_DAYS:
                out.append(Item(e.when - timedelta(days=days), KIND_OPTION_REMINDER, g.property, g.name, t.suite,
                                f"Notice deadline in {days} days ({e.when:%b %-d, %Y}). {e.detail}"))
    if t.commencement_date:
        out.append(Item(t.commencement_date, KIND_COMMENCEMENT, g.property, g.name, t.suite, "The lease term begins."))
    if t.rent_commencement_date and t.rent_commencement_date != t.commencement_date:
        rent = t.current_monthly_rent(t.rent_commencement_date)
        out.append(Item(t.rent_commencement_date, KIND_RENT_COMMENCEMENT, g.property, g.name, t.suite,
                        "Base rent starts" + (f" at ${rent:,.2f}/mo." if rent else ".")))
    return out


def _certificate_items(rows: list[insurance.TenantInsurance], req: insurance.Requirements,
                       suites: dict[tuple[str, str], str | None]) -> list[Item]:
    out = []
    for r in rows:
        cert = r.certificate.certificate if r.certificate else None
        if cert is None or cert.expiration_date is None:
            continue
        suite = suites.get((r.property, r.tenant))
        carrier = f" ({cert.carrier})" if cert.carrier else ""
        out.append(Item(cert.expiration_date, KIND_COI_EXPIRES, r.property, r.tenant, suite,
                        f"General liability policy{carrier} ends. Ask the tenant for a renewal certificate."))
        days = req.expiring_soon_days
        out.append(Item(cert.expiration_date - timedelta(days=days), KIND_COI_REMINDER, r.property, r.tenant, suite,
                        f"Policy{carrier} expires in {days} days ({cert.expiration_date:%b %-d, %Y})."))
    return out


def items(groups: list[portfolio.TenantGroup], certificates: list[insurance.StoredCertificate],
          req: insurance.Requirements, start: date, end: date | None = None) -> list[Item]:
    """Every item dated from ``start`` to ``end`` (inclusive; one day if ``end`` is omitted),
    by date, then urgency, then property and tenant."""
    end = end or start
    # Tenants whose leases ended before the window have nothing left to do.
    live = [g for g in groups if not (g.terms.expiration_date and g.terms.expiration_date < start)]
    every = [i for g in live for i in _lease_items(g)]
    suites = {(g.property, g.name): g.terms.suite for g in live}
    every += _certificate_items(insurance.tenant_insurance(live, certificates, req, start), req, suites)
    return sorted((i for i in every if start <= i.when <= end),
                  key=lambda i: (i.when, KIND_ORDER.index(i.kind), i.property, i.tenant))


def frame(rows: list[Item]) -> pd.DataFrame:
    return pd.DataFrame([{"Date": i.when, "Event": i.kind, "Property": i.property, "Tenant": i.tenant,
                          "Suite": i.suite, "Details": i.detail} for i in rows])
