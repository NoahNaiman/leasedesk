"""Portfolio views built from approved lease abstracts: lease table and critical dates."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

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


def lease_table(records: list[LeaseRecord], today: date) -> pd.DataFrame:
    """One row per approved lease, for the library and exports."""
    rows = []
    for r in records:
        a = r.abstract
        if a is None:
            continue
        rent = a.current_monthly_rent(today)
        months_left = None
        if a.expiration_date:
            months_left = max(0, (a.expiration_date.year - today.year) * 12
                              + a.expiration_date.month - today.month)
        next_option = min((o.notice_deadline for o in a.options
                           if o.notice_deadline and o.notice_deadline >= today), default=None)
        rows.append({
            "id": r.id,
            "Tenant": a.trade_name or a.tenant_name,
            "Legal name": a.tenant_name,
            "Property": a.property_name,
            "Suite": a.suite,
            "SF": a.square_feet,
            "Monthly rent": rent,
            "Rent $/SF/yr": round(rent * 12 / a.square_feet, 2) if rent and a.square_feet else None,
            "Commencement": a.commencement_date,
            "Expiration": a.expiration_date,
            "Months left": months_left,
            "Recovery": a.recoveries.structure.value.replace("_", " ").upper()
            if a.recoveries.structure.value == "nnn" else a.recoveries.structure.value.replace("_", " ").title(),
            "Pro-rata %": a.recoveries.pro_rata_share_pct,
            "CAM cap %": a.recoveries.cam_cap_pct,
            "% rent": a.percentage_rent.rate_pct if a.percentage_rent else None,
            "Renewal options": sum(1 for o in a.options if o.option_type == OptionType.RENEWAL),
            "Next option deadline": next_option,
            "Guarantor": a.guarantor,
            "Source file": r.file_name,
        })
    return pd.DataFrame(rows)


def search(df: pd.DataFrame, query: str) -> pd.DataFrame:
    if not query or df.empty:
        return df
    q = query.strip().lower()
    text = df[["Tenant", "Legal name", "Property", "Suite", "Guarantor", "Source file"]] \
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
