"""Lease abstract schema.

This is the single source of truth for what a retail lease abstract contains.
The same Pydantic models are used to:

* tell Claude exactly what to extract (structured output schema),
* validate and store abstracts in the database,
* drive the review screen and the critical-dates calendar.

Every field is optional because real leases are inconsistent: a missing
value should be ``None`` rather than a guess.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    LEASE = "lease"
    AMENDMENT = "amendment"
    ASSIGNMENT = "assignment"
    GUARANTY = "guaranty"
    OTHER = "other"


class RecoveryStructure(str, Enum):
    NNN = "nnn"
    MODIFIED_GROSS = "modified_gross"
    GROSS = "gross"
    UNKNOWN = "unknown"


class CapType(str, Enum):
    NONE = "none"
    CUMULATIVE = "cumulative"
    NON_CUMULATIVE = "non_cumulative"
    UNKNOWN = "unknown"


class OptionType(str, Enum):
    RENEWAL = "renewal"
    TERMINATION = "termination"
    EXPANSION = "expansion"
    RIGHT_OF_FIRST_REFUSAL = "right_of_first_refusal"
    RELOCATION = "relocation"
    OTHER = "other"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RentStep(BaseModel):
    start_date: date | None = Field(None, description="First day this rent applies.")
    end_date: date | None = Field(None, description="Last day this rent applies.")
    monthly_rent: float | None = Field(None, description="Base rent per month in dollars.")
    annual_rent_psf: float | None = Field(None, description="Annual base rent per square foot.")


class LeaseOption(BaseModel):
    """One option right with its own notice deadline. A lease granting "three renewal options of
    five years each" has three entries. Remedies that only arise if a co-tenancy requirement
    fails belong in ``co_tenancy``, not here."""

    option_type: OptionType
    notice_deadline: date | None = Field(
        None,
        description="Last date the tenant must give notice to exercise. Compute it from "
        "the notice window if the lease states one (e.g. 'no later than 180 days before expiration'). "
        "For a later option in a series, compute it as if the earlier options were exercised.",
    )
    term_months: int | None = Field(None, description="Length of the option term in months, if any.")
    description: str = Field(description="Plain-English summary, including rent terms for the option period.")


class Recoveries(BaseModel):
    structure: RecoveryStructure = RecoveryStructure.UNKNOWN
    pro_rata_share_pct: float | None = Field(
        None, description="Tenant's share of the shopping center as a percentage, e.g. 4.25 for 4.25%."
    )
    pro_rata_denominator_notes: str | None = Field(
        None,
        description="How the share is calculated, e.g. 'GLA of the center excluding anchor tenants'.",
    )
    pays_cam: bool | None = None
    pays_taxes: bool | None = None
    pays_insurance: bool | None = None
    cam_cap_pct: float | None = Field(None, description="Annual cap on controllable CAM increases, as a percentage.")
    cam_cap_type: CapType = Field(
        CapType.UNKNOWN,
        description="'none' when the lease does not cap CAM increases (including when it never mentions "
        "a cap); 'cumulative' or 'non_cumulative' for a cap; 'unknown' only when there is a cap whose "
        "type can't be determined or the text can't be read.",
    )
    base_year: int | None = Field(None, description="Base year for expense stops, if any.")
    admin_fee_pct: float | None = Field(None, description="Landlord admin/management fee on CAM, as a percentage.")
    gross_up_pct: float | None = Field(
        None, description="Occupancy level variable expenses are grossed up to, e.g. 95 for 95%."
    )
    excluded_expenses: list[str] = Field(
        default_factory=list, description="Expense categories the lease excludes from CAM."
    )


class PercentageRent(BaseModel):
    rate_pct: float | None = Field(None, description="Percentage of gross sales over the breakpoint.")
    breakpoint_type: str | None = Field(None, description="'natural' or 'artificial'.")
    breakpoint_amount: float | None = Field(None, description="Annual sales breakpoint in dollars.")
    sales_reporting: str | None = Field(None, description="How and when the tenant must report sales.")


class FieldEvidence(BaseModel):
    """Where in the document a value came from, so a person can verify it."""

    field: str = Field(description="Name of the abstract field, e.g. 'expiration_date' or 'recoveries.cam_cap_pct'.")
    page: int | None = Field(None, description="1-based page number in the PDF.")
    quote: str = Field(description="Short verbatim quote from the lease supporting the value.")
    confidence: Confidence


class LeaseAbstract(BaseModel):
    document_type: DocumentType = DocumentType.LEASE
    landlord: str | None = None
    tenant_name: str | None = Field(None, description="Legal name of the tenant entity.")
    trade_name: str | None = Field(None, description="Name the store operates under (d/b/a).")
    guarantor: str | None = None
    property_name: str | None = None
    property_address: str | None = None
    suite: str | None = None
    square_feet: float | None = Field(None, description="Rentable square feet of the premises.")

    lease_date: date | None = Field(None, description="Date the document was signed or is dated.")
    commencement_date: date | None = None
    rent_commencement_date: date | None = None
    expiration_date: date | None = None

    base_rent_schedule: list[RentStep] = Field(default_factory=list)
    security_deposit: float | None = Field(
        None, description="Security deposit in dollars. Leave empty when the lease requires none.")
    recoveries: Recoveries = Field(default_factory=Recoveries)
    percentage_rent: PercentageRent | None = None
    options: list[LeaseOption] = Field(default_factory=list)

    permitted_use: str | None = None
    exclusive_use: str | None = Field(None, description="Exclusive use protection granted to the tenant, if any.")
    co_tenancy: str | None = Field(None, description="Summary of any co-tenancy clause and the tenant's remedy.")
    radius_restriction: str | None = None

    notes: str | None = Field(
        None, description="Anything unusual a property manager should know that does not fit above."
    )
    evidence: list[FieldEvidence] = Field(
        default_factory=list,
        description="One entry per important value you extracted, citing the page and quote.",
    )

    def current_monthly_rent(self, on: date | None = None) -> float | None:
        """Base rent in effect on a given date (today by default)."""
        on = on or date.today()
        for step in self.base_rent_schedule:
            starts = step.start_date or date.min
            ends = step.end_date or date.max
            if starts <= on <= ends:
                return step.monthly_rent
        return None

    def display_name(self) -> str:
        name = self.trade_name or self.tenant_name or "Unknown tenant"
        return f"{name} ({self.suite})" if self.suite else name
