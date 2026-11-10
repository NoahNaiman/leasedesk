"""Portfolio views built from approved lease abstracts: lease table and critical dates."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd

from leasedesk.db import LeaseRecord
from leasedesk.models import LeaseAbstract, OptionType

EVENT_EXPIRATION = "Lease expiration"
EVENT_OPTION = "Option notice deadline"
EVENT_RENT_INCREASE = "Rent increase"
EVENT_TYPES = [EVENT_OPTION, EVENT_EXPIRATION, EVENT_RENT_INCREASE]


@dataclass
class CriticalDate:
    when: date
    event: str
    tenant: str
    suite: str | None
    detail: str
    doc_id: int


def lease_events(doc_id: int, a: LeaseAbstract) -> list[CriticalDate]:
    tenant = a.trade_name or a.tenant_name or "Unknown tenant"
    events: list[CriticalDate] = []
    if a.expiration_date:
        events.append(CriticalDate(a.expiration_date, EVENT_EXPIRATION, tenant, a.suite,
                                   "Lease term ends.", doc_id))
    for opt in a.options:
        if opt.notice_deadline:
            label = opt.option_type.value.replace("_", " ")
            events.append(CriticalDate(opt.notice_deadline, EVENT_OPTION, tenant, a.suite,
                                       f"Last day to give {label} notice. {opt.description}", doc_id))
    steps = a.base_rent_schedule
    for prev, step in zip(steps, steps[1:]):
        if step.start_date and step.monthly_rent and prev.monthly_rent:
            change = (step.monthly_rent / prev.monthly_rent - 1) * 100
            events.append(CriticalDate(
                step.start_date, EVENT_RENT_INCREASE, tenant, a.suite,
                f"Monthly base rent ${prev.monthly_rent:,.2f} → ${step.monthly_rent:,.2f} ({change:+.1f}%).",
                doc_id))
    return events


def critical_dates(records: list[LeaseRecord], today: date, days_ahead: int,
                   include_past_days: int = 0) -> list[CriticalDate]:
    out = []
    for r in records:
        if r.abstract is None:
            continue
        for e in lease_events(r.id, r.abstract):
            delta = (e.when - today).days
            if -include_past_days <= delta <= days_ahead:
                out.append(e)
    return sorted(out, key=lambda e: (e.when, e.tenant))


def critical_dates_frame(events: list[CriticalDate], today: date) -> pd.DataFrame:
    return pd.DataFrame([{
        "Date": e.when,
        "Days away": (e.when - today).days,
        "Event": e.event,
        "Tenant": e.tenant,
        "Suite": e.suite,
        "Details": e.detail,
    } for e in events])


NO_PROPERTY = "No property listed"


def property_label(a: LeaseAbstract) -> str:
    """The property a lease belongs to: its center name, else its street address without the unit."""
    if a.property_name and a.property_name.strip():
        return a.property_name.strip()
    if a.property_address:
        street = a.property_address.split(",")[0]
        street = re.sub(r"\s+(unit|suite|ste\.?|#)\s*\S+.*$", "", street, flags=re.IGNORECASE).strip()
        if street:
            return street
    return NO_PROPERTY


def _short_legal_name(name: str) -> str:
    """Drop the entity description: "Harbor Nail Studio Inc., a Colorado corporation" -> "Harbor Nail Studio Inc."."""
    return re.split(r",\s+an?\s+", name, maxsplit=1)[0].strip()


def tenant_label(a: LeaseAbstract) -> str:
    if a.trade_name:
        return a.trade_name
    return _short_legal_name(a.tenant_name) if a.tenant_name else "Unknown tenant"


def _tenant_key(a: LeaseAbstract) -> str:
    """Groups a tenant's documents together: "Alpine Frozen Treats, LLC, a Colorado..." -> "alpinefrozentreatsllc"."""
    name = _short_legal_name(a.tenant_name or a.trade_name or "")
    return re.sub(r"[^a-z0-9]", "", name.lower()) or f"unknown-{id(a)}"


def _property_sort(label: str) -> tuple:
    return (label == NO_PROPERTY, label.lower())


def _months_left(expiration: date | None, today: date) -> int | None:
    if not expiration:
        return None
    return max(0, (expiration.year - today.year) * 12 + expiration.month - today.month)


def lease_table(records: list[LeaseRecord], today: date) -> pd.DataFrame:
    """One row per approved document, sorted by property and then tenant."""
    rows = []
    for r in records:
        a = r.abstract
        if a is None:
            continue
        rent = a.current_monthly_rent(today)
        next_option = min((o.notice_deadline for o in a.options
                           if o.notice_deadline and o.notice_deadline >= today), default=None)
        rows.append({
            "id": r.id,
            "Tenant": tenant_label(a),
            "Legal name": a.tenant_name,
            "Property": property_label(a),
            "Document": a.document_type.value.title(),
            "Suite": a.suite,
            "SF": a.square_feet,
            "Monthly rent": rent,
            "Rent $/SF/yr": round(rent * 12 / a.square_feet, 2) if rent and a.square_feet else None,
            "Commencement": a.commencement_date,
            "Expiration": a.expiration_date,
            "Months left": _months_left(a.expiration_date, today),
            "Recovery": a.recoveries.structure.value.replace("_", " ").upper()
            if a.recoveries.structure.value == "nnn" else a.recoveries.structure.value.replace("_", " ").title(),
            "Pro-rata %": a.recoveries.pro_rata_share_pct,
            "CAM cap %": a.recoveries.cam_cap_pct,
            "% rent": a.percentage_rent.rate_pct if a.percentage_rent else None,
            "Renewal options": sum(1 for o in a.options if o.option_type == OptionType.RENEWAL),
            "Next option deadline": next_option,
            "Guarantor": a.guarantor,
            "Source file": r.file_name,
            "_sort": (_property_sort(property_label(a)), tenant_label(a).lower(),
                      a.lease_date or a.commencement_date or date.min),
        })
    rows.sort(key=lambda row: row["_sort"])
    return pd.DataFrame(rows).drop(columns="_sort", errors="ignore")


@dataclass
class TenantGroup:
    """A tenant at one property, with every document on file for it (lease, amendments...)."""
    key: str
    property: str
    records: list[LeaseRecord]

    @property
    def primary(self) -> LeaseRecord:
        """The document whose terms represent the tenant: the newest original lease, if any."""
        def dated(r: LeaseRecord):
            return r.abstract.lease_date or r.abstract.commencement_date or date.min
        leases = [r for r in self.records if r.abstract.document_type.value == "lease"]
        return max(leases or self.records, key=dated)

    @property
    def name(self) -> str:
        return tenant_label(self.primary.abstract)

    def latest(self, value: Callable[[LeaseAbstract], object]):
        """The value from the newest document that states it.

        Amendments usually restate only what they change (a new unit number, a new
        expiration, a new rent table), so the newest stated value is the current one.
        """
        newest_first = sorted(self.records, reverse=True,
                              key=lambda r: r.abstract.lease_date or r.abstract.commencement_date or date.min)
        return next((v for r in newest_first if (v := value(r.abstract)) is not None), None)


def tenant_groups(records: list[LeaseRecord]) -> list[TenantGroup]:
    groups: dict[tuple[str, str], TenantGroup] = {}
    for r in records:
        if r.abstract is None:
            continue
        prop, key = property_label(r.abstract), _tenant_key(r.abstract)
        groups.setdefault((prop, key), TenantGroup(f"{prop}|{key}", prop, [])).records.append(r)
    return sorted(groups.values(), key=lambda g: (_property_sort(g.property), g.name.lower()))


def tenant_table(groups: list[TenantGroup], today: date) -> pd.DataFrame:
    """One row per tenant per property, sorted by property and then tenant."""
    rows = []
    for g in groups:
        expiration = g.latest(lambda a: a.expiration_date)
        rows.append({
            "key": g.key,
            "Property": g.property,
            "Tenant": g.name,
            "Legal name": g.primary.abstract.tenant_name,
            "Suite": g.latest(lambda a: a.suite),
            "SF": g.latest(lambda a: a.square_feet),
            "Monthly rent": g.latest(lambda a: a.current_monthly_rent(today)),
            "Expiration": expiration,
            "Months left": _months_left(expiration, today),
            "Guarantor": g.latest(lambda a: a.guarantor),
            "Documents": len(g.records),
        })
    return pd.DataFrame(rows)


def property_table(groups: list[TenantGroup], today: date) -> pd.DataFrame:
    """One row per property, summarizing its tenants."""
    by_property: dict[str, list[TenantGroup]] = {}
    for g in groups:
        by_property.setdefault(g.property, []).append(g)
    rows = []
    for prop, members in by_property.items():
        sf = sum(g.latest(lambda a: a.square_feet) or 0 for g in members)
        rent = sum(g.latest(lambda a: a.current_monthly_rent(today)) or 0 for g in members)
        expirations = [g.latest(lambda a: a.expiration_date) for g in members]
        upcoming = [d for d in expirations if d and d >= today]
        address = next((a for g in members if (a := g.latest(lambda a: a.property_address))), None)
        rows.append({
            "Property": prop,
            "Address": address,
            "Tenants": len(members),
            "Leased SF": sf or None,
            "Monthly rent": rent or None,
            "Annual rent": rent * 12 or None,
            "Avg rent $/SF/yr": round(rent * 12 / sf, 2) if rent and sf else None,
            "Next expiration": min(upcoming, default=None),
            "Expiring within 12 months": sum(1 for d in upcoming if (d - today).days <= 365),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("Property", key=lambda col: col.map(_property_sort)).reset_index(drop=True)


def rename_property(conn, records: list[LeaseRecord], old_label: str, new_name: str) -> int:
    """Set the property name on every approved document grouped under ``old_label``.

    Used to merge properties that were recorded under different names (for example a
    center name on the original lease and a street address on its amendments).
    """
    from leasedesk import db

    count = 0
    for r in records:
        if r.abstract is not None and property_label(r.abstract) == old_label:
            db.save_review(conn, r.id, r.abstract.model_copy(update={"property_name": new_name.strip()}),
                           approve=True)
            count += 1
    return count


SEARCH_COLUMNS = ["Tenant", "Legal name", "Property", "Suite", "Guarantor", "Source file"]


def search(df: pd.DataFrame, query: str) -> pd.DataFrame:
    if not query or df.empty:
        return df
    q = query.strip().lower()
    text = df[[c for c in SEARCH_COLUMNS if c in df.columns]] \
        .astype(str).apply(lambda col: col.str.lower())
    return df[text.apply(lambda col: col.str.contains(q, regex=False)).any(axis=1)]


def to_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Write one or more tables to an Excel workbook with readable column widths."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            for i, col in enumerate(df.columns, 1):
                width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)])
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(60, width + 2)
            ws.freeze_panes = "A2"
    return buf.getvalue()


def to_ics(events: list[CriticalDate], calendar_name: str = "LeaseDesk critical dates") -> bytes:
    """All-day calendar events that open in Apple Calendar, Outlook, or Google Calendar."""
    from datetime import datetime, timedelta

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LeaseDesk//EN", f"X-WR-CALNAME:{esc(calendar_name)}"]
    for i, e in enumerate(events):
        title = f"{e.event}: {e.tenant}" + (f" ({e.suite})" if e.suite else "")
        lines += [
            "BEGIN:VEVENT",
            f"UID:leasedesk-{e.doc_id}-{e.when:%Y%m%d}-{i}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{e.when:%Y%m%d}",
            f"DTEND;VALUE=DATE:{e.when + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{esc(title)}",
            f"DESCRIPTION:{esc(e.detail)}",
        ]
        if e.event == EVENT_OPTION:  # remind two weeks ahead of notice deadlines
            lines += ["BEGIN:VALARM", "TRIGGER:-P14D", "ACTION:DISPLAY", f"DESCRIPTION:{esc(title)}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode()
