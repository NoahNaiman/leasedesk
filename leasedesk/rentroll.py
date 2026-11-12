"""Check an accounting system's rent roll against the leases.

The rent roll (for example, a Yardi Breeze export) says what each tenant is *billed*. The
approved lease abstracts, merged with their amendments, say what each tenant *owes*. This
module lines the two up and reports every difference a property manager would want to fix:

- **Missed escalation:** billed rent equals an earlier step of the rent schedule, so a
  scheduled increase was never entered.
- **Rent mismatch:** billed rent differs from the lease's current rent for another reason.
- **Expiration or square-footage mismatch** between the two systems.
- **Not in the rent roll:** an active lease that isn't being billed.
- **Not in LeaseDesk:** a billed tenant with no approved lease (or one still awaiting review).

Rent roll exports vary, so columns are found by common header names (``guess_mapping``)
and can be mapped by hand. Title rows above the header and subtotal rows are skipped.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from leasedesk import portfolio
from leasedesk.db import STATUS_REVIEW, LeaseRecord
from leasedesk.portfolio import TenantGroup

FIELDS = ["property", "unit", "tenant", "lease_from", "lease_to", "sf", "monthly_rent"]
FIELD_LABELS = {"property": "Property", "unit": "Unit / suite", "tenant": "Tenant", "lease_from": "Lease start",
                "lease_to": "Lease end", "sf": "Square feet", "monthly_rent": "Monthly base rent"}
REQUIRED = {"tenant", "monthly_rent"}
SYNONYMS = {
    "property": ["property", "property name", "prop", "building", "property code"],
    "unit": ["unit", "units", "unit(s)", "suite", "space", "unit number"],
    "tenant": ["tenant", "tenant name", "lessee", "resident", "name", "occupant"],
    "lease_from": ["lease from", "lease start", "start date", "commencement", "move in", "move-in", "from"],
    "lease_to": ["lease to", "lease end", "end date", "expiration", "lease expiration", "move out", "to"],
    "sf": ["sq ft", "sqft", "sq. ft.", "square feet", "area", "rentable sf", "unit sqft", "sf", "size"],
    "monthly_rent": ["rent", "base rent", "monthly rent", "current rent", "rnt", "monthly base rent",
                     "scheduled rent", "rent amount"],
}

RENT_TOLERANCE = 1.00          # dollars per month
SF_TOLERANCE = 0.01            # 1%

KIND_MISSED_ESCALATION = "Missed escalation"
KIND_RENT_MISMATCH = "Rent mismatch"
KIND_EXPIRATION = "Expiration mismatch"
KIND_SF = "Square footage mismatch"
KIND_NOT_IN_RENT_ROLL = "Not in rent roll"
KIND_NOT_IN_LEASEDESK = "Not in LeaseDesk"
KIND_AWAITING_REVIEW = "Lease awaiting review"
KIND_NO_LEASE_RENT = "No current rent in lease"
KIND_BILLING_AFTER_EXPIRATION = "Billing after expiration"
SEVERITY = {KIND_MISSED_ESCALATION: "High", KIND_RENT_MISMATCH: "High", KIND_NOT_IN_RENT_ROLL: "High",
            KIND_NOT_IN_LEASEDESK: "Medium", KIND_EXPIRATION: "Medium", KIND_BILLING_AFTER_EXPIRATION: "Medium",
            KIND_SF: "Low", KIND_AWAITING_REVIEW: "Low", KIND_NO_LEASE_RENT: "Low"}


# --------------------------------------------------------------------------- reading the export

def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def read_table(data: bytes, file_name: str) -> pd.DataFrame:
    """Read a CSV or Excel export, finding the header row below any report title lines."""
    is_excel = file_name.lower().endswith((".xlsx", ".xls"))
    if is_excel:
        raw = pd.read_excel(io.BytesIO(data), header=None, dtype=str)
    else:
        # Read row by row: report exports put short title lines above a wider table, which a
        # plain CSV reader takes as the table's width.
        text = data.decode("utf-8-sig", errors="replace")
        lines = list(csv.reader(io.StringIO(text)))
        width = max((len(r) for r in lines), default=0)
        raw = pd.DataFrame([r + [None] * (width - len(r)) for r in lines], dtype=object)
        raw = raw.where(raw.notna() & (raw != ""), None)
    known = {_norm(s) for names in SYNONYMS.values() for s in names}
    header_row = 0
    for i, row in raw.head(30).iterrows():
        if sum(_norm(v) in known for v in row if isinstance(v, str)) >= 3:
            header_row = i
            break
    df = raw.iloc[header_row + 1:].copy()
    df.columns = [str(c).strip() if isinstance(c, str) else f"Column {j + 1}"
                  for j, c in enumerate(raw.iloc[header_row])]
    return df.dropna(how="all").reset_index(drop=True)


def guess_mapping(columns: list[str]) -> dict[str, str | None]:
    """Each field's best-matching column: exact synonym first, then a synonym inside the name."""
    normalized = {c: _norm(c) for c in columns}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for f in FIELDS:
        options = [_norm(s) for s in SYNONYMS[f]]
        exact = next((c for c in columns if c not in used and normalized[c] in options), None)
        partial = next((c for c in columns if c not in used and any(o and o in normalized[c] for o in options
                                                                     if len(o) > 3)), None)
        mapping[f] = exact or partial
        if mapping[f]:
            used.add(mapping[f])
    return mapping


def _money(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text or text in {"-", "."}:
        return None
    number = float(text)
    return -number if negative else number


def _date(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or not str(value).strip():
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%Y-%m-%d %H:%M:%S", "%b %d, %Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class RentRollRow:
    line: int
    property: str | None
    unit: str | None
    tenant: str
    lease_from: date | None
    lease_to: date | None
    sf: float | None
    monthly_rent: float | None


def parse_rows(df: pd.DataFrame, mapping: dict[str, str | None]) -> list[RentRollRow]:
    """Rows with a tenant and a rent amount. Subtotal, total and vacant lines are skipped."""
    def get(row, f):
        col = mapping.get(f)
        value = row.get(col) if col else None
        return None if value is None or (isinstance(value, float) and pd.isna(value)) else value

    rows = []
    for i, row in df.iterrows():
        tenant = str(get(row, "tenant") or "").strip()
        if not tenant or re.match(r"^(sub)?total|^vacant\b|^grand total", tenant, re.IGNORECASE):
            continue
        rows.append(RentRollRow(
            line=int(i) + 1, property=(str(get(row, "property")).strip() if get(row, "property") else None),
            unit=(str(get(row, "unit")).strip() if get(row, "unit") else None), tenant=tenant,
            lease_from=_date(get(row, "lease_from")), lease_to=_date(get(row, "lease_to")),
            sf=_money(get(row, "sf")), monthly_rent=_money(get(row, "monthly_rent"))))
    return rows


# --------------------------------------------------------------------------- matching

def _unit_key(unit: str | None) -> str:
    return _norm(re.sub(r"^\s*(suite|ste\.?|unit|units|space|#)\s*", "", unit or "", flags=re.IGNORECASE))


def _names(g_or_abstract) -> set[str]:
    a = g_or_abstract.terms if isinstance(g_or_abstract, TenantGroup) else g_or_abstract
    names = {portfolio.tenant_label(a), a.trade_name, a.tenant_name}
    if isinstance(g_or_abstract, TenantGroup):
        names.add(g_or_abstract.name)
    return {_norm(portfolio._short_legal_name(n)) for n in names if n}


def _name_match(tenant: str, names: set[str]) -> bool:
    t = _norm(tenant)
    return bool(t) and any(n and (t == n or (len(t) > 4 and (t in n or n in t))) for n in names)


def _same_property(row: RentRollRow, g: TenantGroup) -> bool:
    return row.property is None or _norm(row.property) in {_norm(g.property), _norm(g.terms.property_name or "")}


def match(rows: list[RentRollRow], groups: list[TenantGroup]) -> tuple[list[tuple[RentRollRow, TenantGroup]],
                                                                        list[RentRollRow], list[TenantGroup]]:
    """(matched pairs, rows with no lease, tenants with no row). Unit first, then tenant name."""
    unmatched_groups = list(groups)
    pairs, leftovers = [], []
    for row in rows:
        candidates = [g for g in unmatched_groups if _same_property(row, g)]
        hit = next((g for g in candidates if row.unit and _unit_key(row.unit) == _unit_key(g.terms.suite)
                    and _unit_key(row.unit)), None)
        hit = hit or next((g for g in candidates if _name_match(row.tenant, _names(g))), None)
        if hit:
            pairs.append((row, hit))
            unmatched_groups.remove(hit)
        else:
            leftovers.append(row)
    return pairs, leftovers, unmatched_groups


# --------------------------------------------------------------------------- findings

@dataclass
class Finding:
    kind: str
    property: str | None
    unit: str | None
    tenant: str
    detail: str
    billed: float | None = None
    expected: float | None = None
    doc_id: int | None = None

    @property
    def severity(self) -> str:
        return SEVERITY[self.kind]

    @property
    def monthly_difference(self) -> float | None:
        if self.billed is None or self.expected is None:
            return None
        return round(self.expected - self.billed, 2)


@dataclass
class RentRollReport:
    as_of: date
    rows: int
    matched: int
    clean: int = 0                  # matched tenants with no findings
    findings: list[Finding] = field(default_factory=list)

    @property
    def monthly_underbilling(self) -> float:
        """Rent owed under the leases but not billed (positive differences only)."""
        return round(sum(max(0.0, f.monthly_difference or 0) for f in self.findings
                         if f.kind in (KIND_MISSED_ESCALATION, KIND_RENT_MISMATCH)), 2)

    def frame(self) -> pd.DataFrame:
        order = {"High": 0, "Medium": 1, "Low": 2}
        rows = [{"Severity": f.severity, "Issue": f.kind, "Property": f.property, "Unit": f.unit,
                 "Tenant": f.tenant, "Billed": f.billed, "Per lease": f.expected,
                 "Monthly difference": f.monthly_difference,
                 "Annual impact": None if f.monthly_difference is None else round(f.monthly_difference * 12, 2),
                 "Details": f.detail} for f in self.findings]
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values(["Severity", "Property", "Tenant"], key=lambda c: c.map(order)
                              if c.name == "Severity" else c.astype(str).str.lower()).reset_index(drop=True)


def _missed_step(g: TenantGroup, billed: float, as_of: date):
    """The earlier rent step whose amount equals what's billed, if the current step is later."""
    for s in g.terms.base_rent_schedule:
        if (s.start_date and s.start_date <= as_of and s.monthly_rent is not None
                and abs(s.monthly_rent - billed) <= RENT_TOLERANCE):
            current = g.terms.current_monthly_rent(as_of)
            if current is not None and abs(current - billed) > RENT_TOLERANCE:
                return s
    return None


def check(rows: list[RentRollRow], approved: list[LeaseRecord], as_of: date,
          awaiting_review: list[LeaseRecord] = ()) -> RentRollReport:
    """Compare billed rent with each tenant's merged lease terms as of ``as_of``."""
    groups = portfolio.tenant_groups(approved)
    pairs, no_lease, not_billed = match(rows, groups)
    report = RentRollReport(as_of=as_of, rows=len(rows), matched=len(pairs))
    add = report.findings.append

    for row, g in pairs:
        t = g.terms
        before = len(report.findings)
        expected = t.current_monthly_rent(as_of)
        where = dict(property=g.property, unit=row.unit or t.suite, tenant=g.name, doc_id=g.primary.id)
        if t.expiration_date and t.expiration_date < as_of and (row.monthly_rent or 0) > 0:
            add(Finding(KIND_BILLING_AFTER_EXPIRATION, **where, billed=row.monthly_rent,
                        detail=f"The lease documents end {t.expiration_date:%b %-d, %Y}; rent is still billed. "
                               "Check for a renewal not on file, or holdover."))
        elif expected is None:
            add(Finding(KIND_NO_LEASE_RENT, **where, billed=row.monthly_rent,
                        detail="The lease documents don't state rent for this date, so billing can't be checked."))
        elif row.monthly_rent is not None and abs(row.monthly_rent - expected) > RENT_TOLERANCE:
            step = _missed_step(g, row.monthly_rent, as_of)
            current_start = next((s.start_date for s in t.base_rent_schedule
                                  if s.monthly_rent == expected and s.start_date and s.start_date <= as_of), None)
            if step is not None:
                add(Finding(KIND_MISSED_ESCALATION, **where, billed=row.monthly_rent, expected=expected,
                            detail=f"Billed at the rent that began {step.start_date:%b %-d, %Y}. The increase to "
                                   f"${expected:,.2f}" + (f" on {current_start:%b %-d, %Y}" if current_start else "")
                                   + " was not entered."))
            else:
                add(Finding(KIND_RENT_MISMATCH, **where, billed=row.monthly_rent, expected=expected,
                            detail=f"Billed ${row.monthly_rent:,.2f}; the lease sets ${expected:,.2f} for "
                                   f"{as_of:%b %Y}."))
        if row.lease_to and t.expiration_date and row.lease_to != t.expiration_date:
            add(Finding(KIND_EXPIRATION, **where,
                        detail=f"Rent roll says the lease ends {row.lease_to:%b %-d, %Y}; the lease documents say "
                               f"{t.expiration_date:%b %-d, %Y}."))
        if row.sf and t.square_feet and abs(row.sf - t.square_feet) > t.square_feet * SF_TOLERANCE:
            add(Finding(KIND_SF, **where,
                        detail=f"Rent roll: {row.sf:,.0f} SF; lease: {t.square_feet:,.0f} SF. Pro-rata shares "
                               "and per-foot rent depend on this."))
        report.clean += len(report.findings) == before

    pending_by_unit = {}
    for r in awaiting_review:
        if r.status == STATUS_REVIEW and r.ai_abstract is not None:
            pending_by_unit[(_norm(portfolio.record_property(r) if r.abstract else r.property_name or ""),
                             _unit_key(r.ai_abstract.suite))] = r
    for row in no_lease:
        pending = pending_by_unit.get((_norm(row.property or ""), _unit_key(row.unit)))
        if pending is None and row.property is None:
            pending = next((r for (p, u), r in pending_by_unit.items() if u == _unit_key(row.unit)), None)
        if pending is not None:
            add(Finding(KIND_AWAITING_REVIEW, row.property, row.unit, row.tenant, billed=row.monthly_rent,
                        doc_id=pending.id, detail=f"A lease for this unit ({pending.file_name}) is waiting for review "
                                                  "and can't be checked until it's approved."))
        else:
            add(Finding(KIND_NOT_IN_LEASEDESK, row.property, row.unit, row.tenant, billed=row.monthly_rent,
                        detail="Billed in the rent roll, but no approved lease matches this unit or tenant."))
    for g in not_billed:
        if portfolio.tenant_status(g.terms.expiration_date, as_of) == portfolio.STATUS_EXPIRED:
            continue  # an expired lease that isn't billed is expected
        add(Finding(KIND_NOT_IN_RENT_ROLL, g.property, g.terms.suite, g.name, doc_id=g.primary.id,
                    expected=g.terms.current_monthly_rent(as_of),
                    detail="An active lease in LeaseDesk with no line in the rent roll: is this tenant being billed?"))
    return report
