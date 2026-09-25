"""How often reviewers correct the AI, measured from real approved abstracts.

Every document keeps the AI's original draft (``ai_abstract``) separately from the
version a person approved (``abstract``). Comparing the two with the same field-by-field
scoring the benchmark uses gives a correction rate on real leases, which the synthetic
benchmark can't measure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from leasedesk import evaluation
from leasedesk.db import STATUS_APPROVED, LeaseRecord


@dataclass
class CorrectionReport:
    documents: int = 0                 # approved documents that had an AI draft
    documents_corrected: int = 0       # ...of which a reviewer changed at least one field
    fields: int = 0                    # fields compared across those documents
    fields_corrected: int = 0
    by_field: Counter = field(default_factory=Counter)        # field -> times corrected
    field_totals: Counter = field(default_factory=Counter)    # field -> times compared

    @property
    def rate(self) -> float:
        """Share of fields a reviewer changed."""
        return self.fields_corrected / self.fields if self.fields else 0.0

    @property
    def document_rate(self) -> float:
        """Share of documents with at least one correction."""
        return self.documents_corrected / self.documents if self.documents else 0.0

    def most_corrected(self, n: int = 5) -> list[tuple[str, int, int]]:
        """(field, times corrected, times compared), most corrected first."""
        return [(f, c, self.field_totals[f]) for f, c in self.by_field.most_common(n)]


def _field_group(name: str) -> str:
    """Collapse per-item fields ("base_rent_schedule[2025-01-01]") into their group."""
    return name.split("[")[0]


def correction_report(records: list[LeaseRecord]) -> CorrectionReport:
    report = CorrectionReport()
    for r in records:
        if r.status != STATUS_APPROVED or r.ai_abstract is None or r.abstract is None:
            continue
        # The approved version is the right answer; the AI draft is what is being scored.
        # Exact comparison: unlike the benchmark, any change a reviewer made is a correction.
        results = evaluation.compare(r.abstract, r.ai_abstract, rel_tol=0.0)
        report.documents += 1
        corrected = [res for res in results if not res.correct]
        report.documents_corrected += bool(corrected)
        report.fields += len(results)
        report.fields_corrected += len(corrected)
        for res in results:
            report.field_totals[_field_group(res.field)] += 1
        for res in corrected:
            report.by_field[_field_group(res.field)] += 1
    return report
