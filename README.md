# EventScout

Event discovery for Georgia Tech and Atlanta, built with React, TypeScript, FastAPI, and Supabase.

In development. Browsing, keyword search, AI chat search, and Google sign-in are implemented; deployment is next.

**Ask** (`/ask`) answers requests like "free jazz this weekend" from real listings: GPT-5.6 turns each message into a search, hybrid keyword and vector retrieval finds candidates, and a LangGraph pipeline writes a short answer whose citations are checked against the results. Progress, event cards, and the answer stream to the page as they're ready, and follow-ups such as "only free ones" keep the earlier context. Chatting requires Google sign-in through Supabase, and each account sees only its own conversations; browsing stays open to everyone.

## Design docs

- [API overview](apps/api/app/README.md), with one doc per package: [ingestion](apps/api/app/ingestion/README.md), [storage](apps/api/app/storage/README.md), [enrichment](apps/api/app/enrichment/README.md), [indexing](apps/api/app/indexing/README.md), [events](apps/api/app/events/README.md), [search](apps/api/app/search/README.md), [assistant](apps/api/app/assistant/README.md)
- [Web app](apps/web/src/README.md)

## Development

Requires Node.js 22.15+ (22.x), pnpm 10.21.0, Python 3.13, and uv.

```sh
pnpm install --frozen-lockfile
uv sync --directory apps/api --locked
```

For ingestion, create `apps/api/.env` from `.env.example` if missing and set `EVENTSCOUT_DATABASE_URL` to the hosted Supabase session-pooler connection string, plus `EVENTSCOUT_OPENAI_API_KEY` and `EVENTSCOUT_PINECONE_API_KEY`. The database schema is managed in Supabase. A GitHub Actions workflow runs `pnpm ingest` every six hours with the same three values from repository secrets.

For sign-in, enable Supabase's Google provider and allow `http://localhost:5173/**` and `http://127.0.0.1:5173/**` as redirect URLs. Then set `EVENTSCOUT_SUPABASE_URL` in `apps/api/.env`, and create `apps/web/.env` from its `.env.example` with the project URL and publishable key.

Run in separate terminals:

```sh
pnpm dev:api
pnpm dev:web
```

Web: <http://localhost:5173> · API docs: <http://127.0.0.1:8000/docs>

## Commands

| Command | Purpose |
| --- | --- |
| `pnpm ingest` | Import events into Supabase, then summarize and tag new or changed ones (cached by content) and embed them into Pinecone |
| `pnpm ingest --reindex` | The same, but re-embed every event, after a change to how events are embedded |
| `pnpm check` | Run lint, formatting, type checks, and the web build |
