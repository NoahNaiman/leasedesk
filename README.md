# LeaseDesk

**AI-assisted lease administration for retail shopping centers.** LeaseDesk reads lease PDFs (digital or scanned), extracts the terms a property manager actually needs, and keeps them in a searchable library with a calendar of every expiration, option deadline, and rent increase.

AI drafts each abstract, and a person approves it. Every extracted value links back to the page and quote it came from, so checking a lease takes minutes instead of an hour.

LeaseDesk was built to help The Robert L Naiman Co LLC with day-to-day property management: checking lease abstracts, tracking critical dates, and calculating partner distributions.

## Background

I built the first version of LeaseDesk in early June 2026: a hand-written prototype, made without AI assistance, to cut down the hours I was spending pulling terms out of our retail leases and checking them against Yardi. Hardware damage later destroyed my laptop, and the prototype was lost with it.

Rather than retype the prototype from memory, I rebuilt LeaseDesk with Claude as a pair programmer. Building the prototype had already taught me what the tool needed to do and where the hard parts were: scanned leases, amendments layered on top of decades-old originals, and recovery terms that differ tenant by tenant. So for the rebuild I focused on the design and on checking results against real leases, and used AI to move faster on the implementation. That made it possible to take the rebuild further than the original: page-level evidence for every extracted value, a review step before anything is trusted, and a repeatable accuracy benchmark. The commit history in this repository covers the rebuild.

---

## What it does

| | |
|---|---|
| **Import** | Point LeaseDesk at a folder with one subfolder per property. It reads the files in place (nothing is copied) and finds current lease documents (leases, amendments, extensions, assignments, guaranties) by folder and file name, skipping old tenants, insurance certificates, CAM and tax letters, photos, and so on. A fast, inexpensive AI model checks PDFs whose names don't say what they are, and any decision can be corrected by hand. Single PDFs can also be uploaded. |
| **AI abstraction** | Claude reads each lease and fills a structured abstract: parties, premises, term dates, rent schedule, CAM/tax/insurance recoveries (pro-rata share, caps, base year, gross-up, exclusions), percentage rent, options, exclusives, co-tenancy, and radius restrictions. |
| **Review** | The lease page and the editable abstract sit side by side. Each value cites its page; one click jumps there. Low-confidence values are called out. |
| **Properties** | Every property with its tenant count, leased square feet, rent, average rent per square foot, and upcoming expirations. Properties recorded under different names (a center name in one lease, a street address in an amendment) can be merged in one step. |
| **Tenants** | Every tenant, sorted by property, with all of its documents (lease and amendments) grouped together and the newest stated suite, rent, and expiration. |
| **Lease library** | Search across all approved leases and open a one-page summary. Export everything (leases, rent schedules, options) to Excel. |
| **Critical dates** | Expirations, option notice deadlines, and rent increases for any window, with urgent deadlines highlighted. Export to Excel or to your calendar, with a reminder two weeks before each option deadline. |
| **Demo mode** | Two fictional shopping centers, Willow Creek Commons and Maple Ridge Plaza, with 12 tenants between them, so you can explore without real data or an API key. |

## Getting started (no technical experience needed)

1. Install Python 3 from [python.org](https://www.python.org/downloads/) if you don't have it.
2. Double-click **`Start LeaseDesk.command`**. The first launch installs everything it needs (about a minute). After that, it opens in your browser right away.
   - If macOS says the file can't be opened because it's from an unidentified developer, right-click it, choose **Open**, then **Open** again.
3. Click **Load demo property** to explore, or go to **Settings** and add an [Anthropic API key](https://console.anthropic.com) to abstract your own leases.

Close the Terminal window to stop LeaseDesk. All data stays in the `data/` folder on your computer.

## How it works

```
 PDF (digital or scanned)
        │  hash → dedupe, detect scan
        ▼
 data/documents/<sha256>.pdf ──► Claude (PDF document input + structured output)
                                        │  validated LeaseAbstract (Pydantic)
                                        ▼
                        SQLite: ai_abstract_json (kept as-is)
                                abstract_json   (human-reviewed)
                                        │  approve
                                        ▼
                Library · Critical dates · Excel / .ics exports
```

```
leasedesk/            core logic, no UI code
  models.py           LeaseAbstract schema: the single source of truth
  extraction.py       Claude call, error handling, parallel batch processing
  wire_format.py      compact schema sent to the API, and conversion back
  documents.py        PDF import, dedupe, scanned-page detection
  db.py               SQLite storage
  portfolio.py        lease table, critical dates, Excel and calendar export
  evaluation.py       field-by-field accuracy scoring
  demo_data.py        generates the demo leases and their correct abstracts
app/                  Streamlit UI (one file per page)
scripts/              evaluate_extraction.py
tests/                pytest suite (runs offline against a mocked API)
```

### Design decisions

- **The schema drives everything.** One Pydantic model defines what Claude must return (via structured outputs), what gets stored, and what the review form edits. Adding a field means changing one class.
- **A wire format for the API.** Structured outputs compile the schema into a grammar with a size cap, and a lease abstract's dozens of optional fields exceed it. `wire_format.py` sends every optional value as a string ("" means not stated) and converts the reply back before validation, so the rest of the app only ever sees the real model.
- **AI drafts, people approve.** Nothing reaches the library until a person approves it. The AI's original draft is stored separately from the reviewed version, so correction rates can be measured over time.
- **Evidence for every value.** The model must cite a page number, a verbatim quote, and a confidence level for each important term. That turns review into verification instead of re-reading.
- **No separate OCR step.** Claude reads PDFs as both text and page images, so scanned leases go through the same path as digital ones.
- **Demo data with an answer key.** The demo generator produces each lease PDF *and* its correct abstract. That powers the demo, and it gives a repeatable benchmark for extraction accuracy (`scripts/evaluate_extraction.py`) so prompt or model changes can be measured, not guessed at.
- **Local-first.** One user, one machine: SQLite and a folder of PDFs, no server to maintain. Lease text leaves the computer only when you click **Abstract**.

## Measuring accuracy

```bash
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python scripts/evaluate_extraction.py
```

This abstracts all 12 demo leases (two of them scans) and reports per-field accuracy, the most-missed fields, and the total API cost.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest            # offline; the Anthropic API is mocked
.venv/bin/streamlit run app/main.py
```

## Roadmap

- [x] **Phase 1: Lease library.** AI abstraction, review, search, critical dates
- [ ] **Phase 2: Rent roll check.** Compare Yardi Breeze rent roll exports against abstracts and flag missed escalations and billing errors
- [ ] **Phase 3: CAM reconciliation.** Apply each lease's pro-rata share, caps, gross-up, and exclusions to actual expenses; generate tenant statements
- [ ] **Phase 4: Partner distributions.** Waterfall calculations (return of capital, preferred return, promote) from Breeze financials

## Privacy

Leases, the database, and your API key live in `data/`, which is excluded from git. Lease PDFs are sent to Anthropic's API only for abstraction; under Anthropic's commercial terms, API inputs are not used for model training. The demo property, tenants, and people are entirely fictional.
