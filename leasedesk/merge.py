"""Combine a tenant's lease and its amendments into one set of current terms.

Each document is abstracted on its own, and an amendment's abstract holds only what that
amendment establishes or changes. To know a tenant's terms today, the documents are applied
in order:

1. **Base.** The newest original lease. An older lease, and anything dated before the newest
   lease, has been superseded and is set aside. A tenant with no lease on file (only
   amendments) starts from its earliest document.
2. **Later documents**, oldest first: amendments, assignments, guaranties, option exercises.
   Undated documents go first, so any dated document overrides them.

How each part combines:

- **The lease's own dates** (lease date, commencement, rent commencement) come from the base
  only. An amendment's "commencement" is usually the start of an extension, not of the lease.
- **Other values** (expiration, suite, square feet, tenant name after an assignment, deposit,
  guarantor...): a later document's value replaces the earlier one. Blank values never do, and a
  value that is only restated keeps its original source.
- **The rent schedule** is spliced: an amendment's schedule replaces the rent from its first
  step onward, and the earlier steps are kept as history.
- **Options**: a later document that lists options replaces the earlier options; amendments
  that grant options almost always delete the old ones.
- **Recoveries**: each item (share, cap, admin fee...) is replaced only when the later document
  states it; "unknown" never replaces a known value.

Every merged value records which document it came from, so a reviewer can check the source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from leasedesk.db import LeaseRecord
from leasedesk.models import CapType, DocumentType, LeaseAbstract, RecoveryStructure

BASE_ONLY = ("lease_date", "commencement_date", "rent_commencement_date")
OVERRIDE = ("landlord", "tenant_name", "trade_name", "guarantor", "property_name", "property_address", "suite",
            "square_feet", "expiration_date", "security_deposit", "percentage_rent", "permitted_use",
            "exclusive_use", "co_tenancy", "radius_restriction")
RECOVERY_UNKNOWN = {"structure": RecoveryStructure.UNKNOWN, "cam_cap_type": CapType.UNKNOWN}


@dataclass(frozen=True)
class Source:
    """The document a merged value came from."""
    doc_id: int
    file_name: str
    kind: str
    dated: date | None

    @property
    def label(self) -> str:
        when = f" ({self.dated:%b %-d, %Y})" if self.dated else ""
        return f"{self.kind.replace('_', ' ').title()}{when}"


@dataclass
class MergedTerms:
    abstract: LeaseAbstract
    sources: dict[str, Source] = field(default_factory=dict)   # field name -> where it came from
    applied: list[LeaseRecord] = field(default_factory=list)   # documents used, in order applied
    superseded: list[LeaseRecord] = field(default_factory=list)  # set aside (replaced by a newer lease)

    def source(self, name: str) -> Source | None:
        return self.sources.get(name)


def _dated(r: LeaseRecord) -> date | None:
    return r.abstract.lease_date or r.abstract.commencement_date


def _source(r: LeaseRecord) -> Source:
    return Source(r.id, r.file_name, r.abstract.document_type.value, _dated(r))


def _filled(value) -> bool:
    return value not in (None, "", [])


def order_documents(records: list[LeaseRecord]) -> tuple[list[LeaseRecord], list[LeaseRecord]]:
    """(documents to apply in order, superseded documents)."""
    records = [r for r in records if r.abstract is not None]
    if not records:
        return [], []
    leases = [r for r in records if r.abstract.document_type == DocumentType.LEASE]
    if leases:
        base = max(leases, key=lambda r: (_dated(r) or date.min, r.id))
    else:
        base = min(records, key=lambda r: (_dated(r) or date.max, r.id))
    base_date = _dated(base)
    later, superseded = [], []
    for r in records:
        if r is base:
            continue
        older_lease = r.abstract.document_type == DocumentType.LEASE
        before_base = base_date is not None and _dated(r) is not None and _dated(r) < base_date
        (superseded if older_lease or before_base else later).append(r)
    later.sort(key=lambda r: (_dated(r) is not None, _dated(r) or date.min, r.id))
    return [base, *later], superseded


def merge(records: list[LeaseRecord]) -> MergedTerms | None:
    """The tenant's current terms, with the source of each value. None if nothing to merge."""
    ordered, superseded = order_documents(records)
    if not ordered:
        return None
    base = ordered[0]
    terms = base.abstract.model_copy(deep=True, update={"document_type": DocumentType.LEASE, "evidence": []})
    sources: dict[str, Source] = {}

    def claim(name: str, r: LeaseRecord) -> None:
        sources[name] = _source(r)

    for name in (*BASE_ONLY, *OVERRIDE):
        if _filled(getattr(terms, name)):
            claim(name, base)
    if terms.base_rent_schedule:
        claim("base_rent_schedule", base)
    if terms.options:
        claim("options", base)
    for name, value in terms.recoveries:
        if _filled(value) and value != RECOVERY_UNKNOWN.get(name):
            claim(f"recoveries.{name}", base)
    notes = [f"{_source(base).label}: {terms.notes}"] if terms.notes else []

    for r in ordered[1:]:
        a = r.abstract
        for name in OVERRIDE:
            value = getattr(a, name)
            if _filled(value) and value != getattr(terms, name):  # restating a value doesn't change it
                setattr(terms, name, value)
                claim(name, r)
        if a.base_rent_schedule:
            starts = [s.start_date for s in a.base_rent_schedule if s.start_date]
            cutoff = min(starts) if starts else None
            kept = [s for s in terms.base_rent_schedule
                    if cutoff is not None and s.start_date is not None and s.start_date < cutoff]
            terms.base_rent_schedule = kept + list(a.base_rent_schedule)
            claim("base_rent_schedule", r)
        if a.options:
            terms.options = list(a.options)
            claim("options", r)
        for name, value in a.recoveries:
            if _filled(value) and value != RECOVERY_UNKNOWN.get(name) and value != getattr(terms.recoveries, name):
                setattr(terms.recoveries, name, value)
                claim(f"recoveries.{name}", r)
        if a.notes:
            notes.append(f"{_source(r).label}: {a.notes}")

    terms.notes = "\n\n".join(notes) or None
    return MergedTerms(terms, sources, ordered, superseded)
