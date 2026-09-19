# EventScout

EventScout is a planned event discovery app for Georgia Tech and Atlanta. It will combine a searchable event catalog with source-cited, conversational recommendations.

**Status:** step 1, workspace scaffold. The web app checks the API's health; event ingestion, search, accounts, and AI features are not implemented yet.

## Stack

- Web: React, TypeScript, Vite, React Router, TanStack Query, and Tailwind CSS.
- API: Python 3.13, FastAPI, and Pydantic, managed with uv.
- Planned services: Supabase Postgres and Google sign-in, Pinecone, OpenAI, LangGraph, and GitHub Actions ingestion.
- Planned hosting: Vercel for the web app and Render for the API.

## Local setup

Prerequisites: Node.js `>=22.15 <23` (the checked version is in `.node-version`), pnpm `10.21.0`, Python `3.13`, and uv. Docker Desktop with a running Linux engine is needed only for local Supabase; the step 1 health screen runs without a database or provider credentials.

From the repository root:

```powershell
pnpm install --frozen-lockfile
uv sync --directory apps/api --locked
```

The example environment files document the local defaults. Copy them if you need overrides:

```powershell
Copy-Item apps/web/.env.example apps/web/.env.local
Copy-Item apps/api/.env.example apps/api/.env
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

The repository's CI runs the same checks with locked dependencies. These validate the scaffold; they do not imply that planned product features exist.

## Optional local database

Start Docker Desktop's Linux engine before starting Supabase. The project uses a pinned local Supabase CLI through pnpm, so a global Supabase installation is unnecessary.

```powershell
pnpm db:start
pnpm db:status
pnpm db:stop
```

The first start downloads container images. Default local endpoints are API <http://127.0.0.1:54321>, Studio <http://127.0.0.1:54323>, and Postgres port `54322`. Use the local values reported by `pnpm db:status` when database integration is added. No application schema is included in step 1.

## Working in checkpoints

Each implementation checkpoint ends with reviewed changes, relevant checks, and a suggested short commit message. Kevin handles staging, commits, and pushes. Coding agents must leave Git history and the staging area alone unless Kevin explicitly changes that instruction.
