You are building "AlphaAnalyst" — an open-source autonomous equity research agent. Given a US stock ticker, it produces a research memo with DCF valuation, news analysis, and full citations. This file pins the architecture. Do not deviate.

==========================
MONOREPO STRUCTURE
==========================
alpha-analyst/
├── backend/                    Python FastAPI service
│   ├── pyproject.toml
│   ├── alembic/
│   ├── config/models.yaml
│   ├── src/
│   │   ├── settings.py
│   │   ├── clients/            external API wrappers
│   │   ├── llm/                LiteLLM wrapper + cost tracking
│   │   ├── models/             Pydantic data models
│   │   ├── fetchers/           orchestrate clients + cache
│   │   ├── agents/             LLM-driven analysis units
│   │   ├── modeler/            DCF + comps (pure Python)
│   │   ├── orchestrator/       wires agents into pipeline
│   │   └── api/                FastAPI routes
│   └── tests/
├── frontend/                   Next.js app
│   ├── package.json
│   ├── app/                    App Router pages
│   ├── components/
│   ├── lib/                    api client, utils, hooks
│   ├── types/                  generated from backend OpenAPI
│   └── public/
├── docker-compose.yml          postgres + redis + (optional) services
└── README.md

==========================
LOCKED BACKEND STACK
==========================
- Python 3.11+ with uv
- FastAPI + uvicorn
- Postgres 16 + pgvector
- Redis 7
- LiteLLM (NEVER call provider SDKs directly)
- Pydantic v2, SQLAlchemy 2.x async + asyncpg, Alembic
- pytest + pytest-asyncio + respx

==========================
LOCKED FRONTEND STACK
==========================
- Next.js 14+ with App Router (NOT Pages Router)
- TypeScript strict mode (NO any types unless justified in a comment)
- Tailwind CSS v3 (NO CSS-in-JS, NO styled-components)
- shadcn/ui for primitives (canary CLI; install components on demand only)
- TanStack Query v5 for server state (NOT SWR, NOT Redux)
- Tremor for charts/dashboard components (NOT Chart.js, NOT D3 directly)
- Lucide React for icons (NOT Heroicons, NOT Font Awesome)
- Zod for runtime validation
- next-themes for dark mode
- openapi-typescript to generate API types from backend OpenAPI schema

==========================
LOCKED EXTERNAL APIS (do not invent endpoints)
==========================
SEC EDGAR, sec-api.io, Polygon.io, Financial Modeling Prep, Finnhub,
MarketAux, Google News RSS, FRED, Voyage AI (voyage-finance-2)

==========================
LLM ABSTRACTION RULE
==========================
All LLM calls go through `backend/src/llm/client.py::AnalystLLM`. Never call provider SDKs directly. Tasks map to models in `backend/config/models.yaml`. Never hardcode a model name in agent code.

==========================
ANTI-HALLUCINATION RULES (MOST IMPORTANT)
==========================
1. The LLM is a writer, not a knower. Numbers come from APIs ONLY. LLMs synthesize narrative; they NEVER type numerical facts from memory.
2. Every fact passed to an LLM must include a source identifier. LLM output must include these citations.
3. If a required input is missing, raise an exception. Do NOT default-and-pretend. Do NOT fabricate.
4. Do not invent API endpoints, SDK methods, library functions, env var names, or shadcn/ui components.
5. Do not add libraries not in pyproject.toml or package.json. Ask first.
6. All numerical computation happens in Python — NEVER inside an LLM prompt.
7. Frontend never invents data. If the API doesn't return a field, do NOT default it to "—" or "N/A" silently — show a typed empty state.

==========================
API CONTRACT RULE
==========================
Backend exposes OpenAPI schema at /openapi.json. Frontend types are generated from this schema with `npm run codegen`. Frontend NEVER hand-writes types for backend responses. If the frontend needs a field that doesn't exist, update the backend Pydantic model first.

==========================
SCOPE DISCIPLINE
==========================
- Implement ONLY what the current phase prompt requests.
- Stop at listed deliverables.
- TODOs use `# TODO Phase N: ...` (Python) or `// TODO Phase N: ...` (TS).
- Ask before: adding dependencies, restructuring, expanding scope.

==========================
CODING RULES
==========================
- Backend: all I/O async, no blocking calls in routes.
- Backend: tests for every fetcher and agent; mock external APIs with respx.
- Frontend: server components by default; "use client" only when needed (state, effects, browser APIs).
- Frontend: data fetching via TanStack Query hooks in `frontend/lib/api/`; never fetch in components directly.
- No comments unless the WHY is non-obvious.
- No emoji in code, UI, or output.