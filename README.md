# AlphaAnalyst

AlphaAnalyst is an experimental US equity research assistant. It collects filings,
market data, news, and earnings transcripts and drafts a research memo with source
references and experimental valuation outputs. Analyst review is required.

Claim-level checks establish exact-source matching and consistency with retained
financial observations. They do **not** certify source correctness, qualitative
entailment, investment outperformance, or production readiness. The current DCF uses simplified inputs;
Excel exports are snapshots with a few total formulas, not recalculating models.
A different model providing skeptical review does not guarantee independent research.

## Reproducible setup

Use Python 3.13 (see `.python-version`), uv 0.12.21, and Node.js 22 (see
`frontend/.nvmrc`; Node 24 is also supported). Docker is needed for live analysis
and database integration tests. Dependency installation initially requires network
access. Commit both `backend/uv.lock` and `frontend/package-lock.json` when updating
dependencies. Run commands from the repository root unless a `cd` is shown.

### Offline interface demonstration

No provider accounts, backend, database, or Redis are needed:

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000/demo. The homepage links to this fictional, fixed
fixture. It demonstrates report layout, source references, coverage warnings, and
unavailable exports. It does not demonstrate live research quality. After the
initial install, frontend builds and this demo do not fetch fonts or company data.

### Backend and live analysis

```bash
cd backend
uv sync --frozen
cp .env.example .env
```

Edit `backend/.env` before submitting live analyses. The default model configuration
requires `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`. Also configure `FMP_API_KEY`,
`SEC_API_KEY`, `VOYAGE_API_KEY`, and `EDGAR_USER_AGENT` with a real contact email.
Missing essential configuration returns HTTP 503 before creating a job.

Configure Polygon, Finnhub, MarketAux, and FRED keys for the corresponding coverage.
Provider account plans must permit the endpoints used. Missing optional coverage,
provider failures, low-confidence cross-checks, and unavailable sections produce
partial reports with warnings. Requests incur provider charges. Model availability
and provider entitlements need a live smoke test with your accounts.

Start PostgreSQL and Redis, apply migrations, then start the backend:

```bash
# From backend/
docker compose -f ../docker-compose.yml up -d --wait
uv run --frozen alembic upgrade head
uv run --frozen uvicorn src.api.main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
cp .env.local.example .env.local
npm ci
npm run dev
```

Open http://localhost:3000. API documentation is at http://localhost:8000/docs.
Compose credentials and exposed service ports are for local development.

## Report states

| State | Meaning |
| --- | --- |
| `queued` / `running` | Processing has not finished. Progress indicates pipeline stages, not evidence quality or remaining time. |
| `complete` | No known processing or coverage gaps were detected by the current checks. This is not independent factual verification. |
| `partial` | A readable, cited memo exists, but warnings identify missing coverage, agent errors, insufficient sections, validation downgrades, or export failures. |
| `failed` | Essential inputs were absent, no usable cited memo was produced, synthesis failed, the budget cap was exceeded, or execution failed. No memo is published. |

Both complete and partial memos can be read and downloaded where exports exist.
The API exposes `warnings` on job and memo responses. Unavailable export URLs are
`null`; downloads require a finished job, an availability flag, and an existing file.
The frontend stops polling all terminal states. Legacy `error` jobs remain readable
as failures during the 24-hour cache transition.

Current full-report checks require a 10-K and a fundamentals snapshot with revenue.
They also check for unavailable 10-Qs, prices, news, macro data, transcripts,
estimates, peers, agent findings, valuations, and memo sections. Zero recent 8-Ks
is a valid result. Some capabilities, including short-interest analysis, remain
unimplemented, so live reports will commonly be partial. Warnings are conservative;
there is not yet a configurable coverage policy for different research workflows.

Citation objects now carry their explicit `tag` when emitted by the synthesizer.
The interface does not guess missing tag mappings. Actual filing-page extraction remains future work. The claim-verification stage
below adds retained source passages and conservative claim-level publication checks.

## Validation without provider credentials

```bash
cd backend
uv sync --frozen
uv run --frozen pytest -m 'not integration' -q
```

From the repository root, regenerate API types without starting services:

```bash
uv run --frozen --project backend python scripts/export_openapi.py
cd frontend
npm ci
npm run codegen
npm run test
npm run typecheck
npm run lint
npm run build
```

OpenAPI JSON and generated TypeScript are committed. Export and regenerate both
when the backend contract changes. CI rejects stale generated contracts and runs
the tests, type checking, linting, and production build. Tests and schema export use
LiteLLM's bundled pricing map rather than fetching pricing metadata at import time.
Costs remain estimates; provider billing is authoritative.

### Database integration checks

These tests create/drop tables. Use a dedicated, disposable test database. They
skip unless `TEST_DATABASE_URL` is explicitly set; never point it at your working
research database.

```bash
# From repository root, with Docker running:
docker compose up -d --wait
docker compose exec -T postgres createdb -U analyst analyst_test
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://analyst:analyst@localhost:5432/analyst_test uv run --frozen pytest -m integration -q
```

The database CI job checks migrations and PostgreSQL/pgvector integration against
an isolated database. Unit and fixture tests do not validate provider availability
or the financial accuracy of a live memo. The existing evaluation scripts check
limited revenue matching and citation structure; they are not institutional-quality
financial validation.

## Architecture and remaining work

- `backend/src/clients` and `fetchers`: provider access, caching, persistence.
- `backend/src/agents`: specialist findings and memo synthesis through LiteLLM.
- `backend/src/modeler`: deterministic, experimental valuation calculations.
- `backend/src/orchestrator`: pipeline, report outcomes, PDF/Excel snapshots.
- `backend/src/api`: FastAPI contracts and job/report endpoints.
- `frontend`: Next.js, TanStack Query, Zod, and source-reference views.

Reports currently expire from Redis after 24 hours and exports use local temporary
storage. Jobs use in-process background tasks. Authentication, durable workers,
tenant isolation, canonical point-in-time financial data, corrected valuation,
recalculating Excel models, and independent semantic/source-quality review remain pending.
Charts that need structured series explicitly show unavailable states; this stage
does not add chart data to the API. The financial-observation foundation is described below. A defensible valuation model and an analyst pilot remain
subsequent milestones.

The report-status and nullable-export contract changes require consumers to update
from the previous development checkout. Deploy the backend and frontend together;
regenerate external clients from the committed OpenAPI schema.


## Financial observation foundation

Run `uv run --frozen alembic upgrade head` from `backend/` before using this update.
Migration `0003_financial_observations` adds an immutable observation store. Existing
`facts` rows are preserved but are not used for financial cross-checking: their
missing reporting context cannot safely be reconstructed. Run a new analysis to
populate canonical observations. Versioned cache keys bypass the old snapshots.

Each accepted observation stores its metric, original provider tag, finite Decimal
value, canonical unit, declared currency, start/end dates, instant/duration type,
period basis, dimensions, source record locator, filing accession/date, retrieval
time, and a canonical JSON payload hash. Parsed source records are retained in the
database. Numeric decimal JSON values are decoded without a binary-float conversion.
Repeated identical observations deduplicate; changed payloads and different filings
retain separate identities. Hashes identify parsed provider records, not original
HTTP bytes or whole filing documents. Missing dates and currencies remain null.

The SEC parser preserves annual, quarterly, YTD, instant, and segment contexts.
Unit aliases normalize to forms such as `USD`, `EUR/share`, `shares`, and `ratio`.
`decimals` records rounding precision; it does not multiply amounts. This follows
[sec-api's documented unscaled response and period/segment format](https://sec-api.io/api-reference/xbrl-to-json).

FMP statement ingestion requests six annual records and keeps each accepted record
with its own provenance. When consecutive fiscal year ends establish a full annual
interval, the next start is derived from the preceding end plus one day. That
normalization is labeled and records the prior row's locator and hash. It handles
non-calendar and 52/53-week years; it does not invent January starts or missing
currencies. EPS uses a diluted EPS field and currency-per-share units, without a
fallback to basic EPS. Missing or incompatible balance-sheet/cash-flow contexts
are withheld from the assembled snapshot. TTM ratios and undated market values do
not inherit annual financial-statement dates.

The pipeline extracts both the fetched 10-K and 10-Q before refreshing fundamentals.
Revenue/EPS cross-checks require complete matching intervals, currency, unit,
consolidated dimensions, and a compatible filing version. Conflicting candidates
are not arbitrarily selected. `confidence` describes the revenue/EPS cross-check;
it does not certify every observation. Per-field source references remain attached
when SEC values replace vendor values. Latest-filing pointers now revalidate on a
five-minute cache interval; old-report amendments do not outrank newer report years.

`MemoResponse.financial_facts` exposes the selected observations and provenance,
without raw provider payloads. The Financials tab shows these records. Reports
retain warnings about missing context. Monetary observations without known currency
are withheld from the synthesis fact bundle. Comparables require verified
market-cap date/currency and exclude peers in other currencies; there is no
automatic FX conversion. The traced DCF described below requires date-matched
debt, cash and share observations plus explicit scenario assumptions.

Still pending: applying/testing migrations on a live PostgreSQL instance; provider
account smoke tests; a larger independently reviewed company corpus; full XBRL
context/taxonomy coverage; a security master; historical/as-of query APIs; raw HTTP
archives; automatic restatement/reconciliation workflows; verified quote metadata;
and a financially reviewed valuation methodology. The existing price, macro, and
estimates pipelines have not been converted into this financial-statement model.
The stored filing dates and timezone-aware acceptance timestamps do not by
themselves make the application a point-in-time backtesting system. Naive provider
acceptance timestamps remain in source payloads and are not assigned a timezone.


## Claim-level evidence and publication checks

Apply migration `0004_evidence_documents` with `uv run --frozen alembic upgrade head`
from `backend/` before running this version. Run a fresh analysis to re-index source
material. Existing retrieval chunks without archive/version/offset metadata are
excluded from verifiable retrieval; old reports remain visibly unverified.

The indexer now retains immutable versions of captured normalized filing text and
transcripts. Retrieval chunks reference document hashes and real Unicode character
offsets, never invented PDF page numbers. Filing searches are restricted to the
requested accession numbers. News evidence captures provider headline/snippet
excerpts and explicitly identifies them as excerpts, not full articles. Changed
source text keeps a separate document identity. Identical captures retain their
original retrieval timestamp. These are captured normalized text versions, not raw
HTML/PDF byte archives; table layout preservation remains separate work.

Agents must supply a real chunk identifier and an exact contiguous quote. Unknown
references, altered/noncontiguous quotes, and out-of-range evidence are rejected.
Transcript guidance quotes are checked against cited paragraphs; missing paragraph
references no longer receive a fabricated fallback citation. Skeptical review
retains all original evidence instead of replacing it with generated analysis.

The synthesizer now produces structured atomic claims instead of free-form memo
sections. A deterministic publication gate supports these claim kinds:

| Kind | Publication rule | Meaning of the displayed result |
| --- | --- | --- |
| `quote` | Every cited tag must contain the exact quote in an independently reloaded source snapshot. | The retained source said this; its real-world truth is not independently certified. |
| `financial` | Copy a specific observation assertion. The value, metric, unit, currency, dates, period kind/basis, source payload hash, and source binding must match. | The server renders a statement from the checked observation; arbitrary numerical prose is not accepted. |
| `interpretation` | Evidence references must resolve; numerical/date prose is rejected. | Labeled as requiring analyst review; semantic entailment is not automatically certified. |

Financial source records are reloaded independently from the observation store.
Derived annual start dates require the actual preceding fiscal boundary record and
its matching hash. Original provider JSON fields are retained as source passages.
Financial statements are rendered by Python; the LLM cannot turn an asset value
into revenue by changing prose, add an unrelated citation, infer a currency, or
substitute a different fiscal year. Rounded numerical prose and FX conversions remain unsupported. Registered
source-linked recipes for growth, margins and valuation are described below;
arbitrary LLM arithmetic is never accepted through a text-number match.

Legacy free-text LLM responses cannot bypass this gate. Rejected claims are withheld
from section prose and recorded in `Memo.claim_audit`. Interpretations and rejected
claims generate partial-report warnings. A separate report-integrity gate verifies
that all published prose was rendered from accepted claims and explicit citation
identifiers. Citation auditing now checks identities and coverage rather than
comparing citation counts alone. If no usable cited research survives, no memo is
published.

The interface exposes the claim audit, individual numerical checks, and a citation
inspection dialog with exact passages, version hashes, offsets, and retrieval times.
`GET /api/v1/memos/{job_id}/evidence/{document_id}` returns a retained source snapshot
only when it is cited by that completed/partial claim-v1 report. The captured JSON
link is included on returned passages. Offsets use Unicode code points, not bytes
or JavaScript UTF-16 indices. PDFs include the source identifiers and claim audit.

The calculation stage below extends the numerical gate to registered growth,
margin, DCF and comparable recipes. Macro snapshots, price returns, insider
aggregations, sentiment statistics and FX conversions still lack registered traces
and remain outside the verified memo evidence bundle. Excel files remain snapshots;
new traced DCF exports also include exact-value trace and source sheets.

Still pending: live PostgreSQL/migration and provider-account validation, a larger
independently labeled claim-support corpus, semantic entailment/contradiction review,
full article retrieval, HTML/PDF table/page locators, additional derived calculations,
and production access controls/retention policies. Deterministic quote and numerical
checks do not replace a research analyst's judgment or prove investment merit.


## Source-linked calculation recipes

Apply migration `0005_calculation_records` from `backend/` before running this
version (`uv run --frozen alembic upgrade head`). No existing reports or untraced
valuation values are upgraded to verified calculations. A fresh analysis uses
archived financial observations, including historical periods, and stores verified
calculation records immutably in PostgreSQL, independently of the 24-hour job cache.
The fundamentals cache namespace is bumped so the new balance-sheet fields are
fetched. Normalization now retains reported total debt, cash and cash equivalents,
and point-in-time common shares outstanding; weighted-average EPS shares are not
substituted.

Each `Memo.calculations` record includes a versioned recipe, input observation IDs,
explicit assumptions, named formula steps, exact Decimal values, units, output
node references, source metadata and retained source passages. A content hash
identifies the full recipe and results. Replaying a recipe rebuilds the entire
trace under 28-digit Decimal precision with half-even rounding, independent of the
process-wide Decimal context. Changed steps, outputs, source bindings or assumptions
fail verification. Changed legitimate inputs or scenarios produce a new identity.
Before publication, observations and original payloads are independently reloaded,
source records/passages are checked, and the registered recipe is rerun. Arbitrary
formula text and LLM-supplied arithmetic are not executed.

Initial coverage is deliberately explicit:

| Recipe | Formula and conditions |
| --- | --- |
| `growth-v1` | Annual revenue / preceding annual revenue minus one. Requires positive values, consecutive complete annual intervals, comparable duration lengths, the same currency and no segment dimensions. This is annual growth, not a multi-year CAGR. |
| `net-margin-v1` | Net income / revenue, with a positive revenue denominator and matching fiscal interval, currency and filing version. Negative income produces a negative margin. |
| `ocf-margin-v1` | Operating cash flow / revenue under the same matching rules. It is labeled operating cash flow margin, never free cash flow margin. |
| `dcf-fcff-v1` | Projects revenue using the checked annual growth rate, multiplies by an explicitly assumed unlevered FCFF margin, discounts at the explicit WACC, and applies Gordon growth. Equity equals enterprise value minus reported debt plus reported cash, divided by reported fiscal-end shares. Every projection, discount factor, equity-bridge value and valid sensitivity cell is traced. |
| `comps-v1` | Per-peer P/S and eligible positive-income P/E, medians, and target implied market capitalization. Requires common market-cap dates, currency and exact annual fiscal intervals. Financial filing availability must be known and no later than the capitalization date; statements older than 550 days are excluded. Negative/missing income excludes P/E without suppressing valid P/S. |

Ratios are stored as fractions: `0.10` means 10%, rather than ten percent being
silently converted to a different scale. Source revisions are not averaged;
ambiguous historical versions require explicit selection. Automatic peer fetches
are limited to five and excluded peers receive reasons. Comparables select one common capitalization date: the largest eligible peer
cohort, then the most recent date for that cohort. Different dates are never
blended into one median. Market cap / operating cash
flow is no longer described as EV/EBITDA, and no EV/EBITDA estimate is generated.

The DCF no longer derives shares from market cap divided by an unrelated price,
uses liabilities as debt, invents missing cash, assumes a fallback historical margin,
or adjusts valuation assumptions from generated news sentiment. It requires an
explicit scenario. Enter one in the optional DCF scenario form, or submit:

```json
{
  "ticker": "AAPL",
  "dcf_scenario": {
    "name": "Illustrative scenario",
    "rationale": "Illustrative assumptions supplied by the analyst; not a recommendation.",
    "wacc": "0.10",
    "terminal_growth": "0.025",
    "fcff_margin": "0.12",
    "projection_years": 5
  }
}
```

These example assumptions are not estimated or recommended by the application.
FCFF margin is a forecast assumption, not a reported observation or a substitute
for an operating cash flow margin. Terminal growth must be below WACC. Missing
scenario/source inputs withhold the DCF with an actionable warning. Scenario,
methodology and equity-bridge limitations remain visible in partial-report warnings.
The model uses fiscal-end debt, cash and shares and is not a current-market or
point-in-time backtesting valuation. Preferred equity, minority interests, lease
policy, excess cash and other equity-bridge adjustments still require review.

A structured `calculation` claim must copy one exact output assertion (trace ID,
output name, value, unit and currency) and reference its own calculation. The server
renders its text; rounding or relabeling cannot turn a calculated scenario into an
unqualified fact. The report-integrity gate replays calculations again and checks
that numerical claim text, checks and references agree. The interface displays the
trace, assumptions and source passages; source JSON is scoped to records included
in that report. PDFs include calculation steps and source identities. Traced DCF
Excel snapshots include exact Decimal strings and source IDs alongside the existing
numeric presentation sheets; editing the workbook does not recalculate the model.

Download trace JSON from the report and replay it without provider credentials:

```bash
backend/.venv/bin/python scripts/replay_calculation.py downloaded-trace.json
backend/.venv/bin/python scripts/replay_calculation.py downloaded-trace.json --documents captured-sources/
```

The first command checks arithmetic against the included observation metadata.
With full captured-source JSON files, the second also checks exact passages and
original payload hashes/values. Neither proves source truth, accounting judgment,
or investment merit. Job-scoped report URLs still expire with the job cache;
durable records can be reloaded internally with `calculation_store.load_record`.
An authenticated historical-record browsing API remains future work.

Pending for this stage: live PostgreSQL migration/integration tests and provider
smoke tests; broader market-cap coverage beyond the recent-date path below;
financial expert review and a broader golden-company corpus; multi-year CAGR and
additional profitability/leverage/return ratios; broader TTM coverage and financial review;
actual driver-based FCFF forecasts and reviewed WACC estimation; EV/EBITDA using real
enterprise value and EBITDA inputs; FX/security-master support; scenario comparison,
restatement selection, retention/access controls, and editable recalculating Excel.


## Recent dated market capitalization for comparables

The live comparables pipeline now fetches FMP's
[historical market-cap records](https://site.financialmodelingprep.com/developer/docs/stable/historical-market-cap)
and a separately retained
[current listing profile](https://site.financialmodelingprep.com/developer/docs/stable/profile-symbol).
The dated endpoint supplies symbol, date and market cap; the profile supplies quote
currency, exchange and issuer/listing metadata. Statement reporting currency and
undated TTM market cap are never substitutes for quote-currency/date evidence.

The supported initial contract is intentionally narrow:

- Dates must come from the original cap row, be strictly before the UTC capture
  date, and lie within the preceding seven calendar days. Same-day rows are withheld
  to avoid presenting an incomplete session as a completed-date snapshot. There is
  no exchange-calendar claim, and this is not an intraday valuation feed.
- Quote profiles must identify the exact symbol, a supported major currency,
  exchange and issuer identifier, confirm active trading, and explicitly classify
  the listing as neither an ADR, ETF nor fund. Missing/ambiguous metadata is rejected.
- When the cap row lacks currency, implicit mapping from the quote profile is
  supported only for USD listings on NASDAQ, NYSE or AMEX. Other exchange/currency
  combinations require an explicit, matching currency on the cap row. Minor units
  such as GBp/GBX and unrecognized currency codes are not relabeled as major units.
- Profiles must be captured on the cap retrieval's UTC date. The immutable document
  ID includes the daily profile capture identity. Current profile currency is a
  disclosed recent-date mapping policy, **not proof of historical currency validity**
  or a full security master. Arbitrary historical backtests are unsupported.
- Values must be finite and positive. Identical duplicate rows deduplicate;
  conflicting records for the same symbol/date are withheld. API errors and
  unsupported data produce peer-specific warnings, not invented values.

`FinancialObservation.market_cap_context` links the currency policy to the exact
profile document ID, hash, source URL and capture timestamp. Its contents participate
in the observation identity. Source verification independently reloads that document,
replays normalization from the original cap row and profile, and requires a matching
value, symbol, date, unit, currency and profile binding. Both source records contribute
exact passages to the calculation trace. Missing or changed currency evidence fails
publication. Trace downloads retain the context, and the UI explains the currency
basis separately from the statement's reporting currency. Offline replay with
`--documents` verifies the profile link as well as the primary cap payload.

Recent batches cache for one hour in UTC-date-scoped keys and must still pass date
freshness checks on reads. Fresh supported batches replace historical/TTM cap
candidates in the live peer path. A failed refresh does not revive old archived
values. With multiple cap dates, the builder chooses the largest eligible cohort's
latest common date and discloses peers excluded from that date. Matching statement
currency, fiscal intervals and filing availability remain required by `comps-v1`.
Neither currency conversion nor annual/TTM mixing happens implicitly.

No database migration is added for this stage: the context fits the existing
observation JSON and profile text uses the existing source archive. Existing
observation IDs, immutable record JSON and old calculation replay remain compatible
when no market-cap context is present. Prior migrations through `0005` are still
required. Start a fresh analysis after setting `FMP_API_KEY` and confirming account
access to the two stable endpoints; live analysis also needs its existing providers,
PostgreSQL and Redis.

Validation includes provider HTTP fixtures through dated-cap normalization,
independent archive reload, peer date selection and published calculation claims;
tampered/missing profile rejection; provider failures and stale caches; immutable
legacy compatibility; offline replay; and a PostgreSQL persistence test. Real account
smoke tests remain pending because no FMP credential is configured in this checkout.
PostgreSQL integration tests remain pending without a running test database.

Remaining work: actual provider/account validation and independent numerical
cross-checks; reviewed currency methodology beyond the supported USD listing path;
historical effective-dated currencies, security/issuer identity and share-class
handling; ADR ratios and FX; broader TTM/calendar coverage; exchange calendars,
corporate actions and provider corrections. Current-profile provenance does not
establish source truth or historical investability.


## TTM financials and differing fiscal calendars

The live calculation pipeline now fetches discrete quarterly income statements from
FMP's [stable income-statement endpoint](https://site.financialmodelingprep.com/developer/docs/stable/income-statement)
with `period=quarter&limit=12`. The existing annual snapshot remains separate.
New recipes `ttm-income-v1` and `comps-ttm-v1` produce source-linked revenue,
net income, net margin, P/S, P/E and implied market capitalization. TTM totals
are calculated trace nodes, never fabricated provider-reported observations.

Quarterly normalization requires the exact symbol, reported currency, fiscal year,
Q1–Q4 label and end date. A quarter start is the immediately preceding fiscal
quarter's end plus one day, including Q4-to-Q1 fiscal-year rollover. The predecessor
must be unambiguous and the interval 75–105 days. Its original payload hash is
retained in `period.start_evidence`; `start_method=prior_quarter_end` identifies this
derivation. Missing/conflicting predecessors leave the start unknown and exclude
the observation from TTM assembly. Independent source verification replays the
quarter normalization against the retained original row and predecessor, not just
the displayed date. Quarterly revenue and net income are normalized; EPS, annual
rows and cumulative/YTD cash flows are not summed.

Each TTM needs exactly four distinct, chronological, complete, contiguous quarters
with one currency and no segment dimensions. Gaps, overlaps, duplicates, unknown
starts, annual/YTD substitutions and ambiguous source versions are rejected. The
full interval must span 350–380 days, accommodating calendar and 52/53-week years.
Revenue and earnings must use matching quarter boundaries and filing versions.
Each filing must have known availability on/before the explicit as-of date;
future or unknown availability cannot supply a TTM denominator. A trace is replayed
from all retained quarter observations and their source passages before publication.

`comps-ttm-v1` keeps one market-cap date but allows companies' actual trailing windows
to differ under a visible policy:

| Check | Initial bound |
| --- | --- |
| Age of each TTM end at the market-cap date | At most 185 calendar days |
| Difference between target and peer TTM ends | At most 100 calendar days |
| Difference between their window lengths | At most 7 days |
| Currency | Identical statement and market-cap currencies; no FX inference |

This supports differing fiscal quarter/year ends without pretending all companies
reported over the same dates. The policy is a comparison tolerance, not calendarized
financials: there is no interpolation or proration, and different seasonal/economic
windows remain a disclosed limitation. Every company's actual start/end is retained
in `CalculationTrace.periods`, displayed in the interface and PDF, and included in
trace downloads and offline replay. Server-rendered claims use the entire target
TTM window, never the first component quarter's dates.

The selector uses the most recent available four-quarter set at each candidate
market-cap date. It does not silently resolve conflicting versions or skip an
incomplete latest quarter to find an older convenient window. Peer capitalization
dates still use the largest eligible cohort, then its latest common date. A valid
TTM cohort is preferred over annual comparables; annual and TTM multiples are never
combined into a median. If TTM inputs are unavailable, the separate `comps-v1` annual
recipe can still run under its original exact-interval rules. Earnings unavailable,
misaligned or nonpositive on a trailing basis suppress P/E while valid P/S survives.

The quarterly fetcher caches for one hour and persists original observations before
caching. A failed refresh produces a warning and does not reactivate old archived
quarterly candidates in the live path. Annual/Q4 observations that share an end date
are selected separately so quarterly ingestion does not break annual margins or
fallback P/E. The new recipes and period metadata preserve old calculation IDs,
serialized records and replay behavior; no new database migration is required.
Existing migrations through `0005` are still required. Run a fresh analysis to fetch
quarterly inputs; provider account access must include quarterly stable statements.

Tests cover fiscal-year rollover, calendar versus 52/53-week windows, source-boundary
replay, incomplete/duplicate/overlapping/YTD inputs, restatement conflicts, filing
availability, peer skew/staleness, loss-making P/E exclusions, annual fallback,
HTTP/cache behavior, published claims, offline replay and database persistence.
Live provider tests remain pending without an FMP key; PostgreSQL integration tests
require a disposable configured database.

Remaining work: financial expert calibration of these comparison tolerances and a
larger independently reviewed company corpus; actual provider/account validation;
SEC-driven Q4 residual reconstruction and annual-plus-current-YTD-minus-prior-YTD
TTM methods; cumulative cash-flow differencing; statement restatement selection,
issuer/share-class/corporate-action handling; true calendarization and seasonality
adjustments; broader non-US/half-year reporting and currency/FX support. The existing
annual DCF has not been silently switched to TTM inputs. Source availability checks
do not turn this application into a fully point-in-time backtesting system.
