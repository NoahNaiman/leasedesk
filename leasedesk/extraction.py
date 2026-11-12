"""AI lease abstraction with Claude.

The whole PDF is sent to Claude as a document. Claude reads both the text
layer and the page images, so digital and scanned leases go through the same
path with no separate OCR step. Structured outputs guarantee the response
matches the ``LeaseAbstract`` schema, so the result is validated data rather
than free text that needs parsing.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import anthropic
import pydantic

from leasedesk import config, db, wire_format
from leasedesk.models import DocumentType, LeaseAbstract

SYSTEM_PROMPT = """You are an experienced commercial real estate lease administrator \
abstracting retail shopping center leases for a property manager.

Extract the lease terms into the provided schema. Guidelines:
- Only record what the document states. If a value is not in the document, leave it null \
rather than guessing. Do not invent standard terms.
- Dates must be exact calendar dates. If the lease gives a formula (for example, "180 days \
before the Expiration Date"), compute the date.
- Base rent schedule: one entry per distinct rent period, with monthly dollars and annual \
dollars per square foot. If only one of those is stated, compute the other from the square footage.
- Percentages are numbers on a 0-100 scale (4.25 means 4.25%).
- Recoveries: capture how CAM, taxes, and insurance are passed through, including pro-rata \
share, the denominator, caps (and whether cumulative), base year, admin fee, gross-up, and exclusions.
- If this document is an amendment, assignment, or guaranty rather than an original lease, \
set document_type accordingly and fill in only what this document establishes or changes.
- If the file contains the original lease together with later amendments, set document_type to \
"lease" and abstract the terms as amended, noting each amendment in notes.
- For each important value, add an evidence entry with the 1-based PDF page number, a short \
verbatim quote, and your confidence. Use "low" confidence for anything hard to read, ambiguous, \
or computed rather than stated.
- Conventions: list each option separately, one entry per notice deadline ("three renewal options" \
is three entries); co-tenancy remedies go in co_tenancy, not options. A lease with no CAM cap clause \
has cam_cap_type "none". If the lease says there is no security deposit, leave security_deposit empty. \
If a lease sets no separate rent commencement date, rent commences on the Commencement Date.
- Put anything unusual that a property manager should know (side letters referenced, \
unusual landlord obligations, rent abatement, kick-out clauses) in notes."""

USER_PROMPT = "Abstract this lease document."
MAX_TOKENS = 16000
EFFORT = "high"


class ExtractionError(Exception):
    """A failure with a message suitable for showing to a non-technical user."""


@dataclass
class ExtractionResult:
    abstract: LeaseAbstract
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.input_tokens, self.output_tokens)


def estimate_cost(input_tokens: int | None, output_tokens: int | None) -> float:
    return ((input_tokens or 0) * config.MODEL_INPUT_PRICE_PER_MTOK
            + (output_tokens or 0) * config.MODEL_OUTPUT_PRICE_PER_MTOK) / 1_000_000


def apply_conventions(abstract: LeaseAbstract) -> LeaseAbstract:
    """Conventions applied in code after extraction, so they hold even when the model is
    inconsistent about them.

    - A lease that sets no separate rent commencement date: rent starts on the Commencement
      Date. (Leases only: an amendment's dates describe what it changes.)
    """
    if (abstract.document_type == DocumentType.LEASE and abstract.rent_commencement_date is None
            and abstract.commencement_date is not None):
        abstract = abstract.model_copy(update={"rent_commencement_date": abstract.commencement_date})
    return abstract


def make_client(api_key: str) -> anthropic.Anthropic:
    # Long scanned leases can take a few minutes to read.
    return anthropic.Anthropic(api_key=api_key, timeout=900, max_retries=3)


def abstract_pdf(client: anthropic.Anthropic, pdf_path: Path) -> ExtractionResult:
    """Send one PDF to Claude and return a validated abstract."""
    pdf_b64 = base64.standard_b64encode(Path(pdf_path).read_bytes()).decode("ascii")
    try:
        response = client.beta.messages.create(
            model=config.EXTRACTION_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            output_config={"effort": EFFORT,
                           "format": {"type": "json_schema", "schema": wire_format.WIRE_SCHEMA}},
            # If a request is ever declined by a safety classifier, retry it on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                    {"type": "text", "text": USER_PROMPT},
                ],
            }],
        )
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("The API key was rejected. Check it on the Settings page.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise ExtractionError("This API key does not have access to the model. Check your Anthropic account.") from exc
    except anthropic.RateLimitError as exc:
        raise ExtractionError("Anthropic's rate limit was reached. Wait a minute and try again.") from exc
    except anthropic.BadRequestError as exc:
        raise ExtractionError(f"The document could not be processed: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionError(f"Anthropic returned an error ({exc.status_code}). Try again later.") from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionError("Could not reach Anthropic. Check your internet connection.") from exc

    if response.stop_reason == "refusal":
        raise ExtractionError("The AI declined to process this document.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The lease was too long to abstract in one pass.")
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        abstract = wire_format.from_wire(json.loads(text))
    except (ValueError, pydantic.ValidationError) as exc:
        raise ExtractionError("The AI response could not be read as a lease abstract.") from exc

    return ExtractionResult(
        abstract=apply_conventions(abstract),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


ProgressCallback = Callable[[int, int, str, str | None], None]


def process_documents(
    conn,
    client: anthropic.Anthropic,
    doc_ids: list[int],
    *,
    max_workers: int = 4,
    on_progress: ProgressCallback | None = None,
) -> tuple[int, int]:
    """Abstract several documents in parallel. Returns (succeeded, failed).

    API calls run on worker threads; all database writes happen on the calling
    thread because SQLite connections are not shared across threads.
    """
    records = [r for r in (db.get_document(conn, i) for i in doc_ids) if r]
    ok = failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(abstract_pdf, client, Path(r.stored_path)): r for r in records}
        for done, future in enumerate(as_completed(futures), 1):
            record = futures[future]
            error = None
            try:
                result = future.result()
                db.save_ai_abstract(conn, record.id, result.abstract,
                                    input_tokens=result.input_tokens, output_tokens=result.output_tokens)
                ok += 1
            except ExtractionError as exc:
                error = str(exc)
            except Exception as exc:  # keep going if one file fails unexpectedly
                error = f"Unexpected error: {exc}"
            if error:
                db.mark_failed(conn, record.id, error)
                failed += 1
            conn.commit()
            if on_progress:
                on_progress(done, len(records), record.file_name, error)
    return ok, failed
