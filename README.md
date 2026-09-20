# EventScout

EventScout is a planned event discovery app for Georgia Tech and Atlanta. It will combine a searchable event catalog with source-cited, conversational recommendations.

**Status:** step 2, event storage. The web app checks the API's health, and the backend can persist event observations with stable IDs and atomic indexing jobs. Feed ingestion, search, accounts, and AI features are not implemented yet.

## Stack

- Web: React, TypeScript, Vite, React Router, TanStack Query, and Tailwind CSS.
- API: Python 3.13, FastAPI, and Pydantic, managed with uv.
- Planned services: Supabase Postgres and Google sign-in, Pinecone, OpenAI, LangGraph, and GitHub Actions ingestion.
- Planned hosting: Vercel for the web app and Render for the API.

The frontend lives in `apps/web`. The backend package lives in `apps/api/app`, with Uvicorn entry point `app.main:app`; backend configuration, dependencies, and the ignored `.env` file stay in `apps/api`.

## Local setup

Prerequisites: Node.js `>=22.15 <23` (the checked version is in `.node-version`), pnpm `10.21.0`, Python `3.13`, and uv. The backend uses hosted Supabase for event storage; the health screen runs without a database or provider credentials.

From the repository root:

```powershell
pnpm install --frozen-lockfile
uv sync --directory apps/api --locked
```

The example environment files document the local defaults. Copy them if you need overrides:

```powershell
if (!(Test-Path apps/web/.env.local)) { Copy-Item apps/web/.env.example apps/web/.env.local }
if (!(Test-Path apps/api/.env)) { Copy-Item apps/api/.env.example apps/api/.env }
```

Variables starting with `VITE_` are visible in the browser. Never put service-role keys, OpenAI keys, Pinecone keys, or other secrets in them. Local environment files are ignored by Git; keep real credentials out of committed examples.

Start these in separate terminals from the repository root:

```powershell
pnpm dev:api
```

```powershell
pnpm dev:web
```

- Web: <http://localhost:5173>
- API health: <http://127.0.0.1:8000/health>
- API documentation: <http://127.0.0.1:8000/docs>

The web screen should show whether the API is reachable. Stop the API to check its unavailable state, then restart it and retry from the screen.

## Checks

Run from the repository root:

```powershell
pnpm lint
pnpm typecheck
pnpm build
pnpm check:api
```

The repository's CI runs lint, formatting, type checks, and the web build with locked dependencies. This project does not include an automated test suite; verify feature behavior manually at each checkpoint.

## Hosted database for development

EventScout uses a hosted Supabase project for its development catalog. Docker is not needed to connect the backend to it. In the Supabase dashboard, choose **Connect → Direct → Session pooler**, copy the connection URI, and set `EVENTSCOUT_DATABASE_URL` in `apps/api/.env`. Replace the password placeholder, URL-encode any special characters in the password, and append `?sslmode=require` for an encrypted connection. Copy the exact pooler host from the dashboard; it cannot be inferred reliably from the region.

The session pooler uses port `5432` and supports IPv4 and prepared statements. The current backend connects directly to Postgres, so it does not need a Supabase API key. Keep this connection string in the backend's ignored `.env` file.

The schema is managed directly in the hosted Supabase project. Inspect the live schema before making database changes. This repository does not store SQL migrations or automatically create the catalog tables in a new database.

## Event storage

The catalog lives in the `eventscout` Postgres schema, which is not exposed through Supabase's public Data API. Its tables track sources, source records, canonical event occurrences, ingestion runs, and pending indexing jobs. Row-level security is enabled without browser-access policies; the storage module uses a trusted server-side connection.

An import identifies a source record by its source, publisher-provided external ID, and optional stable occurrence key. It does not use a mutable title or start time as the identity. The same record keeps its event ID across reschedules. Matching events across different sources comes in a later checkpoint.

Canonical content changes increment a version and queue an indexing job in the same transaction. Repeated content only refreshes observation metadata. Cancellation preserves the event and queues index removal. There is no Pinecone worker yet. Unknown prices and eligibility remain unknown, and all-day dates are kept separate from timed events.

## Working in checkpoints

Each implementation checkpoint ends with reviewed changes, relevant checks, and a suggested short commit message. Kevin handles staging, commits, and pushes. Coding agents must leave Git history and the staging area alone unless Kevin explicitly changes that instruction.
