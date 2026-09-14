# AlphaAnalyst

AlphaAnalyst is an experimental US equity research assistant. It collects filings,
market data, news, and earnings transcripts and drafts a research memo with source
references and experimental valuation outputs. Analyst review is required.

The implementation does **not** establish claim-level factual accuracy, investment
outperformance, or production readiness. The current DCF uses simplified inputs;
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
The interface does not guess missing tag mappings. Actual filing-page extraction,
quote verification, and claim-by-claim source support remain future work.

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
recalculating Excel models, and claim-level verification remain pending.
Charts that need structured series explicitly show unavailable states; this stage
does not add chart data to the API. The financial-observation foundation is described below. Exact claim verification,
a defensible valuation model, and an analyst pilot remain subsequent milestones.

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
are withheld from the synthesis fact bundle. Valuation is withheld when necessary
quote currency or market-cap date/currency is unverified. Comparable valuations
exclude peers in other currencies; there is no automatic FX conversion.

Still pending: applying/testing migrations on a live PostgreSQL instance; provider
account smoke tests; a larger independently reviewed company corpus; full XBRL
context/taxonomy coverage; a security master; historical/as-of query APIs; raw HTTP
archives; automatic restatement/reconciliation workflows; verified quote metadata;
and a financially reviewed valuation methodology. The existing price, macro, and
estimates pipelines have not been converted into this financial-statement model.
The stored filing dates and timezone-aware acceptance timestamps do not by
themselves make the application a point-in-time backtesting system. Naive provider
acceptance timestamps remain in source payloads and are not assigned a timezone.
