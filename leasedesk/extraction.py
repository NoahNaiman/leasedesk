"""AI lease abstraction with Claude.

The whole PDF is sent to Claude as a document. Claude reads both the text
layer and the page images, so digital and scanned leases go through the same
path with no separate OCR step. Structured outputs guarantee the response
matches the ``LeaseAbstract`` schema, so the result is validated data rather
than free text that needs parsing.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import anthropic

from leasedesk import config, db
from leasedesk.models import LeaseAbstract

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
- For each important value, add an evidence entry with the 1-based PDF page number, a short \
verbatim quote, and your confidence. Use "low" confidence for anything hard to read, ambiguous, \
or computed rather than stated.
- Put anything unusual that a property manager should know (side letters referenced, \
unusual landlord obligations, rent abatement, kick-out clauses) in notes."""

USER_PROMPT = "Abstract this lease document."


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


def make_client(api_key: str) -> anthropic.Anthropic:
    # Long scanned leases can take a few minutes to read.
    return anthropic.Anthropic(api_key=api_key, timeout=900, max_retries=3)


def abstract_pdf(client: anthropic.Anthropic, pdf_path: Path) -> ExtractionResult:
    """Send one PDF to Claude and return a validated abstract."""
    pdf_b64 = base64.standard_b64encode(Path(pdf_path).read_bytes()).decode("ascii")
    try:
        response = client.beta.messages.parse(
            model=config.EXTRACTION_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "high"},
            output_format=LeaseAbstract,
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
    if response.parsed_output is None:
        raise ExtractionError("The AI response could not be read as a lease abstract.")

    return ExtractionResult(
        abstract=response.parsed_output,
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
