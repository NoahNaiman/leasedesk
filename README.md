# LeaseDesk

[![tests](https://github.com/NoahNaiman/leasedesk/actions/workflows/tests.yml/badge.svg)](https://github.com/NoahNaiman/leasedesk/actions/workflows/tests.yml)

LeaseDesk extracts the terms of commercial retail leases from PDF files using the Anthropic API.
It handles both scanned and digital leases. Each term is validated against a Pydantic schema and
linked to the page and quote it came from. A person reviews each abstract, side by side with the
lease, before it is used. Approved abstracts feed a searchable lease library and a calendar of
expirations, option deadlines and rent increases, exportable to Excel and to calendar apps.

The dashboard opens on the day's agenda: option notice deadlines (with reminders 90 and 30 days
ahead), lease expirations and commencements, rent increases, and expiring insurance certificates.
Any other day can be picked to see what falls on it.

LeaseDesk also builds on those terms to do three property management jobs:

- **Rent roll check.** Compares an accounting system's rent roll with each lease, including its
  amendments.
- **Insurance tracking.** Checks each tenant's certificate of insurance against the landlord's
  requirements.
- **Partner distributions.** Splits partnership distributions through a preferred-return waterfall.

LeaseDesk is in use at a small commercial real estate company. All 31 of its properties (261 lease
documents) have been abstracted, and review of the abstracts is under way.

## Sample output

From one of the benchmark's synthetic leases: a seven-year lease whose pro-rata share is given only
as a formula (2,760 SF ÷ 64,200 SF) and whose CAM cap is described without the word "cumulative".
The values below are the AI's actual answer from the November 11, 2026 benchmark run, abridged.

```text
Tenant            Summit Optical Group PC (d/b/a Summit Eyecare), Unit E-5, 2,760 SF
Term              2025-05-01 to 2032-04-30
Base rent         $6,612.50/mo from 2025-05-01; $7,130.00/mo from 2029-05-01
Recoveries        NNN; pro-rata share 4.30%; CAM cap 4% cumulative; admin fee 10%; gross-up 95%
Renewal option    5 years; notice deadline 2031-08-04
Deposit           $6,612.50
```

Each value carries its evidence, for example:

| Field | Page | Confidence | Quote from the lease |
|---|---|---|---|
| `recoveries.pro_rata_share_pct` | 1 | low | "the denominator of which is the floor area of all leasable buildings in the Shopping Center, currently 64,200 square feet" |
| `recoveries.cam_cap_type` | 1 | medium | "shall not exceed Tenant's Share of Controllable Operating Costs for the first full calendar year of the Term, increased by four percent (4%) per year, compounded annually" |
| `recoveries.admin_fee_pct` | 1 | high | "a management fee equal to ten percent (10%) of Operating Costs, excluding Taxes and Insurance" |
| `security_deposit` | 1 | low | "an amount equal to one month of Base Rent at the initial rate." |

Values that were computed rather than stated, such as the 4.30% share and the deposit, come back as
low confidence. The review screen highlights them.

## Architecture

```text
property folders ──► library.py: index files, pick out lease documents (rules + small-model triage)
                          │
                          ▼
             extraction.py: PDF → Claude (claude-opus-5-5), structured output
                          │   wire_format.py: compact schema out, full model back
                          ▼
             models.py: LeaseAbstract (Pydantic), with FieldEvidence per key value
                          │
                          ▼
             db.py (SQLite): ai_abstract_json (AI draft, never edited)
                             abstract_json    (human-reviewed)
                          │  approve
                          ▼
             merge.py: each tenant's lease + amendments → current terms, with sources
             portfolio.py: properties, tenants, critical dates, Excel and .ics export
             corrections.py: how often reviewers changed the AI draft
             agenda.py: everything falling on a given day, for the dashboard
             rentroll.py: billed rent (accounting export) vs. merged lease terms
             insurance.py: certificates of insurance vs. requirements (Claude reads the PDF)
             distributions.py: pro rata and waterfall partner distributions
```

- **UI:** Streamlit, under `app/` (one file per page).
- **PDFs:** files are read in place; nothing is copied. Claude receives each PDF as both text and
  page images, so scanned leases need no separate OCR step.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/build_sandbox.py          # fictional properties, no API key needed
LEASEDESK_DATA_DIR=sandbox/data .venv/bin/streamlit run app/main.py
```

- **The sandbox:** two fictional shopping centers, laid out the way real property folders are. It
  includes:
  - documents in every review state
  - a fictional rent roll export with one of each billing problem planted in it
  - insurance certificates covering every status
  - two partnerships with distribution histories, one of which reaches the promote
- **Your own leases:** add an Anthropic API key on the Settings page, then point the Import page at
  a folder with one subfolder per property.
- **Without the command line:** on macOS, double-click `Start LeaseDesk.command`, or
  `Start LeaseDesk Sandbox.command` for the sandbox.

## Benchmark

**Method.** The benchmark has 22 synthetic leases, each with a known-correct abstract.
`evaluation.compare` scores every field, including:

- parties and premises
- dates
- deposit
- recoveries
- percentage rent
- the rent in effect at each scheduled step
- each option's notice deadline

Numbers are matched within 0.5%, which allows for rounding. Names ignore entity descriptions such as
", a Colorado corporation". A failed extraction counts as every field missed.

There are two sets:

| Set | Leases | Purpose |
|---|---|---|
| Standard | 12 | Clean demo leases with one layout: a regression check. Two are scans. |
| Hard | 10 | Each lease is built around one known difficulty; the list follows this table. |

The hard leases cover:

- a rent schedule described only in prose
- dates written in words or set by formula
- option notice windows given in months
- a lease and its amendment in one file
- struck-through, initialed corrections
- a 16-page degraded scan with terms buried in later articles
- a pro-rata share given only as a formula
- a cumulative cap described indirectly
- rent given only as annual totals, with a base year
- distractor figures
- a guarantor named only in an exhibit

Each answer key is computed by the same code that writes its lease text.
`tests/test_benchmark_hard.py` checks the keys against their PDFs offline.

**Results.** Live run on November 11, 2026, model `claude-opus-5-5`:

| Set | Fields correct | Citation coverage | Cost |
|---|---|---|---|
| Standard (12) | 100% (338/338) | 74% (144/195) | $1.37 |
| Hard (10) | 100% (249/249) | 72% (111/155) | $1.04 |

*Citation coverage* is the share of filled-in, scored fields that have a page citation.

Earlier runs scored lower: 96.0% on the standard set, then 96.8% and 97.6% on the hard set. None of
those misses was a wrong rent, date, share or deadline. They fell into two groups:

- **Different conventions.** For example, a lease with no CAM cap clause can be recorded as cap type
  "none" or as "unknown". These conventions are now written into the schema and the prompt. Where
  the model stayed inconsistent, the convention is applied in code after extraction.
- **Errors in my answer keys.** One option deadline was off by a day across a leap year, and two
  leases were marked triple-net without saying so.

**Failure modes, honestly stated.** Both sets now score 100%, so the benchmark no longer separates
better and worse versions. It catches regressions. It does not measure accuracy on real leases:

- the leases are generated
- the same person wrote the leases and the answer keys
- no generator reproduces decades-old scans, handwriting in margins, or genuinely ambiguous drafting

The real-world measure is the reviewer correction rate (see below). Not enough real abstracts have
been approved yet to report one.

```bash
.venv/bin/python scripts/evaluate_extraction.py --set all          # live run, about $2.50
.venv/bin/python scripts/evaluate_extraction.py --rescore benchmark_results/<run>   # free
```

**Benchmark before shipping.** A full live run writes `benchmark/receipt.json`, recording a
fingerprint of everything that affects extraction:

- the model
- the prompts and request settings
- the schema sent to the API
- the conventions applied after extraction

`tests/test_benchmark_receipt.py` recomputes the fingerprint offline. If any of those inputs changed
without a new benchmark run, CI fails and prints the command to run. A manual GitHub Actions
workflow (`benchmark.yml`) runs the paid benchmark when an `ANTHROPIC_API_KEY` secret is set.

## Design decisions

- **The AI's draft and the approved abstract are stored separately.** Every document keeps the AI's
  output (`ai_abstract_json`) unchanged next to the version a person approved (`abstract_json`). That
  makes review auditable.
  - `corrections.py` compares the two field by field, exactly to the cent, which gives a correction
    rate on real leases.
  - The Dashboard and `scripts/correction_rate.py` report it.
- **A compact wire format fits the API's limits.** Structured outputs compile the JSON schema into a
  grammar. A lease abstract has dozens of optional fields, and the full Pydantic schema exceeded two
  API limits: the number of optional (union-typed) fields and the size of the compiled grammar.
  - `wire_format.py` sends every optional value as a string, where "" means not stated, and marks
    every key required.
  - The reply is converted back to numbers, dates, booleans and `None`, then validated against the
    full `LeaseAbstract`.
  - The rest of the code only ever sees the real model.
- **Evidence for key values.** The prompt asks for a page number, a short verbatim quote and a
  confidence level for each important value. The review screen jumps to the cited page. Coverage is
  measured, not assumed: about three quarters of filled-in scored fields are cited in the latest run.
- **Amendments are merged into current terms.** Each document is abstracted on its own. An
  amendment's abstract holds only what it changes. `merge.py` applies a tenant's documents in date
  order, starting from the newest lease:
  - A later document replaces only what it states, and "unknown" never overwrites a known value.
  - An amendment's rent table replaces the rent from its first step onward.
  - New options replace old ones.
  - The lease's own dates come from the lease.

  Every merged value records the document it came from, and the Tenants page labels values a later
  document changed. Tenant lists, property totals and critical dates all use the merged terms, so an
  amended expiration doesn't also appear from the original lease.
- **Conventions are enforced in code where the model is inconsistent.** One example is defaulting
  the rent commencement date to the commencement date when a lease states none. The rule lives in
  `extraction.apply_conventions`, so it holds whatever the model returns.
- **Local and single-user.** SQLite and a folder of PDFs, with no server. Lease contents leave the
  machine only for the AI steps.

## Testing

```bash
.venv/bin/pytest        # 173 tests, about 60 seconds
```

- **Offline:** the suite runs with no network access. The Anthropic API is replaced by an httpx
  `MockTransport`, and a fixture in `tests/conftest.py` fails any test that tries to open a network
  connection.
- **Coverage:**
  - the extraction call and its error handling
  - the wire format
  - folder scanning, checked against a generated sandbox whose files record the expected decision
  - portfolio tables, critical dates, Excel and .ics export
  - the correction rate
  - the benchmark answer keys
  - every Streamlit page, rendered headlessly with `streamlit.testing`, including clicks through
    search, scanning, approving and removing
- **CI:** GitHub Actions runs `ruff check` and `pytest` on every push.

## Limitations

- **Merging follows fixed rules.** For example, an amendment that lists options replaces all earlier
  options, so a termination right that survives an amendment would be dropped. Each merged value
  shows its source document so this can be caught in review.
- **Current rent can be missing.** When the documents don't state it (for example, an extension
  that keeps CPI-adjusted rent), it has to come from the accounting system.
- **Not every value is cited.** Citation coverage is about 73%.
- **Some files are out of reach.** PDFs only, up to 600 pages. Files over 23 MB are sent as
  compressed page images. Leases saved only as Word files are skipped.
- **Single user, single machine.** There's no multi-user access control.
- **Rent roll formats vary.** The rent roll import was built without a real accounting export to
  hand. It finds columns by common header names, and they can be remapped by hand, but an unusual
  export may need its mapping adjusted.
- **Insurance requirements are portfolio-wide.** One set of minimums applies to every tenant; lease-
  by-lease insurance clauses aren't extracted yet.
- **Waterfalls have one shape.** Preferred return (simple, accruing daily), then return of capital,
  then a residual split with one promote tier. Catch-up provisions and multiple IRR hurdles aren't
  modeled.
- **Planned, not built:** CAM reconciliation.

## How it was built

I wrote a first prototype by hand in June 2026 and lost it to hardware damage. This version was
built with Claude Code as a pair programmer. I directed the design, tested it against real leases,
and reviewed the changes. The commit history covers this rebuild.

Lease files, the database and API keys live in `data/`, which git ignores. Every tenant, person and
property in this repository is fictional.
