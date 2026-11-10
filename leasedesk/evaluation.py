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


def compare(expected: LeaseAbstract, actual: LeaseAbstract) -> list[FieldResult]:
    results: list[FieldResult] = []

    def check(name, e, a):
        results.append(FieldResult(name, e, a, values_match(e, a)))

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
