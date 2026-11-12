"""Score an AI-produced abstract against a known-correct one.

Used to measure extraction accuracy on the demo leases (where the right
answer is known) and to compare prompt or model changes before shipping them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from leasedesk.models import LeaseAbstract

SCALAR_FIELDS = [
    "tenant_name", "trade_name", "guarantor", "suite", "square_feet", "lease_date",
    "commencement_date", "rent_commencement_date", "expiration_date", "security_deposit",
]
RECOVERY_FIELDS = [
    "structure", "pro_rata_share_pct", "cam_cap_pct", "cam_cap_type", "base_year",
    "admin_fee_pct", "gross_up_pct",
]


@dataclass
class FieldResult:
    field: str
    expected: object
    actual: object
    correct: bool


def _norm_text(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", v.lower())


def _norm_name(v: str) -> str:
    """A party's name without its entity description: "Acme, LLC, a Colorado limited liability
    company" and "Acme LLC" are the same answer."""
    return _norm_text(re.split(r",\s+an?\s+", v, maxsplit=1)[0])


def _norm_suite(v: str) -> str:
    """ "Suite 104", "Unit 104" and "104" are the same answer."""
    return _norm_text(re.sub(r"^\s*(suite|ste\.?|unit|space|premises)\s+", "", v, flags=re.IGNORECASE))


NAME_FIELDS = {"tenant_name", "trade_name", "guarantor"}


def values_match(expected, actual, rel_tol: float = 0.005) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(expected - actual) <= max(0.01, abs(expected) * rel_tol)
    if isinstance(expected, date):
        return expected == actual
    if hasattr(expected, "value"):  # enums
        return expected == actual
    return _norm_text(str(expected)) == _norm_text(str(actual))


def compare(expected: LeaseAbstract, actual: LeaseAbstract, rel_tol: float = 0.005) -> list[FieldResult]:
    """Field-by-field comparison. ``rel_tol`` is the numeric tolerance: the benchmark allows
    0.5% for rounding differences; measuring human corrections uses 0 (exact to the cent)."""
    results: list[FieldResult] = []

    def check(name, e, a):
        if isinstance(e, str) and isinstance(a, str) and name in NAME_FIELDS:
            ok = _norm_name(e) == _norm_name(a)
        elif isinstance(e, str) and isinstance(a, str) and name == "suite":
            ok = _norm_suite(e) == _norm_suite(a)
        else:
            ok = values_match(e, a, rel_tol)
        results.append(FieldResult(name, e, a, ok))

    for f in SCALAR_FIELDS:
        check(f, getattr(expected, f), getattr(actual, f))
    for f in RECOVERY_FIELDS:
        check(f"recoveries.{f}", getattr(expected.recoveries, f), getattr(actual.recoveries, f))

    e_pr, a_pr = expected.percentage_rent, actual.percentage_rent
    check("percentage_rent.rate_pct", e_pr and e_pr.rate_pct, a_pr and a_pr.rate_pct)
    if e_pr:
        check("percentage_rent.breakpoint_amount", e_pr.breakpoint_amount, a_pr and a_pr.breakpoint_amount)

    # Rent schedule: right number of periods, and the right rent on each period's start date.
    check("base_rent_schedule.periods", len(expected.base_rent_schedule), len(actual.base_rent_schedule))
    for step in expected.base_rent_schedule:
        got = actual.current_monthly_rent(step.start_date)
        check(f"base_rent_schedule[{step.start_date}]", step.monthly_rent, got)

    # Options: same count, and each expected notice deadline found.
    check("options.count", len(expected.options), len(actual.options))
    actual_deadlines = {o.notice_deadline for o in actual.options}
    for o in expected.options:
        check(f"options[{o.option_type.value}].notice_deadline", o.notice_deadline,
              o.notice_deadline if o.notice_deadline in actual_deadlines else None)
    return results


def accuracy(results: list[FieldResult]) -> float:
    return sum(r.correct for r in results) / len(results) if results else 0.0


CITABLE_FIELDS = SCALAR_FIELDS + [f"recoveries.{f}" for f in RECOVERY_FIELDS] + [
    "base_rent_schedule", "options", "percentage_rent"]


def _has_value(a: LeaseAbstract, name: str) -> bool:
    value = getattr(a.recoveries, name.split(".", 1)[1]) if name.startswith("recoveries.") else getattr(a, name)
    return value not in (None, [], "") and getattr(value, "value", None) != "unknown"


def citation_coverage(a: LeaseAbstract) -> tuple[int, int]:
    """(cited, total): how many of the scored fields that have a value also have an evidence entry.

    Evidence fields are matched by name, ignoring list indexes and sub-fields
    ("options[0].notice_deadline" counts for "options").
    """
    cited = set()
    for e in a.evidence:
        name = re.sub(r"\[.*?\]", "", e.field)
        cited.add(".".join(name.split(".")[:2]) if name.startswith("recoveries.") else name.split(".")[0])
    filled = [f for f in CITABLE_FIELDS if _has_value(a, f)]
    return sum(f in cited for f in filled), len(filled)
