# EventScout

Event discovery for Georgia Tech and Atlanta, built with React, TypeScript, FastAPI, and Supabase.

In development. Event ingestion, keyword search, and the browsing UI are implemented; AI search is next.

## Development

Requires Node.js 22.15+ (22.x), pnpm 10.21.0, Python 3.13, and uv.

```sh
pnpm install --frozen-lockfile
uv sync --directory apps/api --locked
```

For ingestion, create `apps/api/.env` from `.env.example` if missing and set `EVENTSCOUT_DATABASE_URL` to the hosted Supabase session-pooler connection string. The database schema is managed in Supabase. A GitHub Actions workflow imports and indexes events every six hours; it reads `EVENTSCOUT_DATABASE_URL`, `EVENTSCOUT_OPENAI_API_KEY`, and `EVENTSCOUT_PINECONE_API_KEY` from repository secrets.

Run in separate terminals:

```sh
pnpm dev:api
pnpm dev:web
```

Web: <http://localhost:5173> · API docs: <http://127.0.0.1:8000/docs>

## Commands

| Command | Purpose |
| --- | --- |
| `pnpm ingest --dry-run` | Preview the next 90 days of events |
| `pnpm ingest` | Import events into Supabase |
| `pnpm index` | Embed new or changed events into Pinecone |
| `pnpm search "jazz"` | Compare keyword, vector, and hybrid results; `--spot-checks` runs the saved queries |
| `pnpm chat` | Search by chatting in the terminal; `--script` replays and checks saved conversations |
| `pnpm check` | Run lint, formatting, type checks, and the web build |
