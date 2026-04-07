# AlphaAnalyst

AlphaAnalyst is an open-source autonomous equity research agent. Give it a US
stock ticker and it produces a research memo: executive summary, financial
snapshot, recent catalysts, DCF + comparables valuation, earnings-call tone
shift, bull/bear cases, and risks — every numerical claim cited back to its
primary source.

The pipeline runs ten data fetchers concurrently (SEC EDGAR, Polygon, FMP,
Finnhub, MarketAux, Google News, FRED, Voyage embeddings, sec-api XBRL,
FMP transcripts), indexes the long-form documents into pgvector, then runs
six LLM agents — five "constructive" agents plus a Devil's Advocate forced
to use a different model family for genuine independence. A pure-Python DCF
in `decimal.Decimal` and a peer-comparable multiples engine produce the
valuation. A synthesizer with a hard schema and a citation validator writes
the final memo.

Two design choices anchor the project: **the LLM is a writer, not a knower**
(numbers come from APIs; the synthesizer downgrades any section whose
numerical claims aren't tagged to a real source) and **valuation is pure
Python** (`decimal.Decimal` everywhere; no LLM ever touches an arithmetic
operator). The result is a memo you can hand to an analyst and have them
audit every number to a 10-K page.

## Architecture

```
                     ┌──────────────┐
  ticker (e.g. TSLA) │   Frontend   │  Next.js 14 + TanStack Query
  ──────────────────►│  (App Router)│  Types codegen'd from /openapi.json
                     └──────┬───────┘
                            │ POST /api/v1/analyze
                            ▼
                     ┌──────────────┐
                     │   FastAPI    │  Lifespan, CORS, exception handlers
                     │ Orchestrator │  Per-step Redis progress writes
                     └──────┬───────┘
                            │ asyncio.gather
       ┌────────────────────┼─────────────────────────┐
       ▼                    ▼                         ▼
  ┌─────────┐         ┌──────────────┐          ┌──────────┐
  │Fetchers │         │   Indexer    │          │  Agents  │
  │  (10)   │         │ (Voyage AI,  │          │   (6+1)  │
  │         │         │   pgvector)  │          │          │
  └────┬────┘         └──────┬───────┘          └────┬─────┘
       │                     │                       │
       ▼                     ▼                       ▼
 SEC EDGAR / sec-api   Postgres + pgvector    Anthropic + OpenAI
 Polygon / FMP                                via LiteLLM
 Finnhub / MarketAux
 FRED / Google News
                            │
                            ▼
                     ┌──────────────┐
                     │   Modeler    │  decimal.Decimal everywhere
                     │ (DCF + Comps)│  5×5 sensitivity grid
                     └──────┬───────┘
                            │
                            ▼
                     ┌──────────────────┐
                     │   Synthesizer    │  task=synthesis (Claude Opus)
                     │     + Citation   │
                     │      Validator   │ ◄─── Devil's Advocate
                     └──────┬───────────┘      task=devils_advocate
                            │                  (gpt-4o, intentionally
                            │                   different family)
              ┌─────────────┴───────────────┐
              ▼                             ▼
          PDF (reportlab)               Excel (openpyxl)
                                        live formulas + sensitivity
```

## Repository layout

```
alpha-analyst/
├── backend/                 Python service (FastAPI + agents + pipeline)
│   ├── src/
│   │   ├── clients/         External-API wrappers (httpx + tenacity)
│   │   ├── llm/             LiteLLM wrapper + cost tracking
│   │   ├── models/          Pydantic data models
│   │   ├── fetchers/        clients + cache + DB persistence
│   │   ├── agents/          LLM-driven analysis units + synthesizer
│   │   ├── modeler/         DCF + comps (pure Python)
│   │   ├── orchestrator/    pipeline + PDF/Excel exporters
│   │   └── api/             FastAPI app, routes, schemas
│   ├── alembic/             schema migrations
│   ├── config/models.yaml   per-task model + fallback config
│   └── tests/               pytest + respx + eval harness
├── frontend/                Next.js 14 app (App Router, TanStack Query)
├── scripts/run_evals.py     eval CLI (hits real APIs)
└── docker-compose.yml       postgres (pgvector) + redis
```

## Setup

### Prerequisites

- macOS / Linux
- Python 3.11+ via [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Docker Desktop or [OrbStack](https://orbstack.dev) (`brew install orbstack`)
- Node.js 20+ and npm
- API keys (see `backend/.env.example`)

### Backend

```bash
cd backend
cp .env.example .env                       # fill in API keys
uv sync                                    # creates .venv with all deps
docker compose -f ../docker-compose.yml up -d   # postgres + redis
uv run alembic upgrade head                # apply both migrations
uv run pytest                              # ~80 tests should pass
uv run uvicorn src.api.main:app --reload --port 8000
```

The API ships its OpenAPI schema at <http://localhost:8000/openapi.json>.
Interactive docs at <http://localhost:8000/docs>.

### Frontend

```bash
cd frontend
cp .env.local.example .env.local           # NEXT_PUBLIC_API_URL=...
npm install
npm run codegen                            # regenerates types/api.ts
npm run dev                                # http://localhost:3000
```

`npm run codegen` re-fetches `/openapi.json` and rewrites `types/api.ts`. Run
it whenever the backend's API contract changes.

### Running an analysis

From the home page, type a ticker (e.g. `TSLA`) and press Enter. The page
routes to `/analysis/{job_id}` which polls `/api/v1/jobs/{job_id}` every 2s,
shows step-by-step progress, and renders the memo when the pipeline finishes.
Download buttons in the right rail stream the PDF / live-formula Excel from
the backend.

<!--
## Running the eval suite

The eval suite is the project's truth check. It hits **real** APIs (SEC
EDGAR, Polygon, FMP, Finnhub, Voyage, FRED, sec-api, Anthropic, OpenAI), so
budget for it accordingly:

- ~$1.50 average cost per analysis × 10 tickers = **~$15 per full eval run**
- Latency: ~1–3 minutes per ticker; ~15–30 minutes total
- Eval credits should come from a separate API budget, not your dev account

```bash
cd backend
uv run python ../scripts/run_evals.py                # full run, stdout
uv run python ../scripts/run_evals.py --output ../eval-report.md
uv run python ../scripts/run_evals.py --only TSLA AAPL  # restricted run
```

The CLI exits non-zero if any of these thresholds breaches:

- Numerical accuracy < **99%**
  (revenue figures from each ticker's 10-K must appear in the memo within
  a 5% tolerance)
- Hallucinated `[F#]` citation tags > **0**
  (every tag in the memo body must map to an entry in `memo.citations`)
- Average cost > **$1.50** per analysis

Ground-truth facts live in `backend/tests/eval/dataset.py` and cite the
exact 10-K Item where each number appears. If a number drifts because the
underlying filing was revised, fix the dataset, not the pipeline.
-->

<!--
## Sample memo (illustrative)

The full output is a JSON object validated against the Memo schema in
[backend/src/agents/synthesizer.py](backend/src/agents/synthesizer.py).
Below is a representative slice for TSLA — every numerical claim is tagged.

```
Ticker: TSLA
As of: 2025-01-30

Executive summary
Tesla reported FY2024 revenue of $97.69B [F1], up 1% YoY against a tougher
auto market. Operating margin compressed to 7.2% [F2] vs 9.2% in FY2023,
driven by ASP cuts in China and ramp costs at Cybertruck. The Energy
Storage segment grew 67% YoY and contributed 9% of total revenue [F3].

Financial snapshot
Revenue $97.69B [F1] · GAAP net income $7.13B [F4] · Free cash flow
$3.58B [F5] · Total liquidity $36.6B [F6].

Recent catalysts
News signal for TSLA (last 90d, recency-weighted): net_sentiment=+0.124
across 47 classified articles (28 positive, 9 negative, 6 high-materiality).
Most-material event: regulatory clearance for FSD v13 in select markets [F7].

Valuation
DCF base case intrinsic value per share: $245.50 [F8]. WACC 9.0%, terminal
growth 2.5%, 5-year projection. Sensitivity grid (WACC × g) shows IV/share
in [$181, $312] across ±2% WACC and ±1% terminal-growth combinations.

Citations
[F1] (filings_agent) Revenue grew 1% YoY in FY2024.
     filing — 10-K 0002193125-25-013328 p.42
[F2] (filings_agent) Operating margin compressed to 7.2%.
     filing — 10-K 0002193125-25-013328 p.42
[F3] (filings_agent) Energy Storage revenue grew 67% YoY.
     filing — 10-K 0002193125-25-013328 p.78
... (typical run produces 30-60 citations)
```

The actual JSON shape is defined by the `Memo` Pydantic model and is what
the frontend consumes; the prose above is what the synthesizer renders into
the PDF / web view.
-->

<!--
## Known limitations

This is a v0.1.0 — the pipeline runs end-to-end and produces a memo, but
several rough edges remain:

- **Single-period DCF**: the orchestrator currently builds the DCF from a
  one-period revenue history (no multi-year XBRL roll-up). CAGR resolves
  to 0; the resulting valuation is a no-growth perpetuity unless the user
  edits the Excel export. Tracked for v0.2.0.
- **Free-text Memo sections**: the Memo schema returns prose; structured
  series like quarterly revenue, segment breakouts, and tone keyword counts
  per quarter are *computed* in the agents but not surfaced as JSON. The
  frontend renders empty-state placeholders for those charts until they
  ship in MemoResponse.
- **Equity-only**: no fixed-income, no derivatives, no preferred shares
  treatment. US tickers only.
- **No MNPI compliance layer**: the system has no provenance check on
  insider information; do not feed it documents under embargo.
- **No backtesting**: memo recommendations are not back-tested against
  realized returns. Treat the system as a research-acceleration tool, not a
  signal generator.
- **No multi-currency**: every number is assumed USD; non-USD filers (e.g.
  ADRs of European companies) will produce confused output.
- **No real-time streaming**: news sentiment is computed at memo-generation
  time; no continuous re-evaluation.
-->

## Production roadmap

- **v0.2** Structured Memo: extend `MemoResponse` with `financials.price_history`,
  `financials.annual_series`, `catalysts.events[]`, `valuation.sensitivity_table`,
  `valuation.method_comparison`. Wire into the existing chart components.
- **v0.3** Multi-period DCF: orchestrator pulls 5+ years of XBRL facts,
  computes proper revenue CAGR, exposes per-segment series.
- **v0.4** Backtesting harness: memo → forward-12-month return; track hit
  rate against benchmark.
- **v0.5** Real-time streaming: incremental memo updates on new 8-Ks, news,
  or price moves > 2σ.
- **v0.6** Multi-currency + non-US filers (TSE, LSE, HKEX).
- **v0.7** MNPI compliance: source-provenance gate, audit log, per-user
  permissions.
- **v1.0** Production observability: per-run tracing, cost dashboards, SLOs.

## Tagging the v0.1.0 release

Once `python scripts/run_evals.py` passes locally:

```bash
git tag -a v0.1.0 -m "v0.1.0 — eval thresholds met"
git push --tags
```

---

**A note on the eval suite philosophy.** Every claim in this README — the
memo's structure, the Devil's Advocate model split, the strict
synthesizer validator — exists because we expect a human analyst to audit
the output. The eval suite tests exactly that: numerical claims trace back
to 10-K pages, citations are non-fabricated, the cost-per-analysis is
sustainable. If those three properties degrade, the whole pitch falls
apart, so the eval CLI is wired to fail loudly.
