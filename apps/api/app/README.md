# EventScout API

The backend has two jobs:
- It keeps a catalog of upcoming events around Georgia Tech and Atlanta current.
- It serves that catalog to the web app, both as a filterable list and through an AI chat.

Postgres (hosted on Supabase) is the only source of truth. Everything else derives from it and can be rebuilt: the Pinecone vectors, the AI summaries and the chat answers.

## How data moves

```
public calendars ──ingestion──▶ Postgres ◀──enrichment── OpenAI (summaries, topics)
                                   │
                         index_jobs (queued by a trigger)
                                   │
                                indexing ──▶ OpenAI embeddings ──▶ Pinecone

browser ──GET /api/events──────────▶ events      (read-only SQL)
browser ──POST /api/turns (SSE)────▶ assistant ──▶ search (Postgres + Pinecone) ──▶ OpenAI
```

A GitHub Actions workflow runs `python -m app.ingestion` every six hours. It imports, then enriches, then indexes. The API process itself never writes events; it only reads the catalog and stores chat history.

## Packages

| Package | Responsibility |
| --- | --- |
| [`ingestion/`](ingestion/README.md) | Scrape and parse the calendars, recognize the same event across calendars, store the result |
| [`storage/`](storage/README.md) | The canonical event model and the transactional writes behind it |
| [`enrichment/`](enrichment/README.md) | One-sentence summaries and topics per event, cached by content |
| [`indexing/`](indexing/README.md) | Keep the Pinecone index in step with the catalog through a job queue |
| [`events/`](events/README.md) | The public catalog: search filters, SQL queries, and the `/api/events` routes |
| [`search/`](search/README.md) | Hybrid retrieval that fuses keyword and vector rankings |
| [`assistant/`](assistant/README.md) | The LangGraph chat pipeline, its streaming endpoint, and chat history |

Top-level modules:

- `main.py` builds the FastAPI app: CORS, `/health`, and the two routers. Starting it needs no external service, and AI clients open on first use.
- `settings.py` holds configuration from `EVENTSCOUT_*` environment variables or `apps/api/.env`.
- `database.py` opens one autocommit psycopg connection per caller, with dict rows. Write transactions are owned by the code that writes.
- `auth.py` verifies Supabase access tokens: ES256 signatures checked against the project's published keys, plus issuer, audience and expiry.

## Conventions

- **Async throughout.** The code uses psycopg 3, httpx, and the async OpenAI and Pinecone clients. On Windows, psycopg needs a selector event loop. The CLI sets one, and the dev server runs uvicorn with `--reload` for the same reason.
- **The database enforces what it can.** Unique source identities, one running chat turn per conversation, content versioning, and index-job queueing all live in constraints and triggers, not only in Python.
- **Failures are contained.** One calendar failing never stops the others. An unavailable catalog returns 503 instead of a stack trace. CLI errors print only the exception type, so connection details stay out of the public workflow log.
- **Listings are data.** Text from calendars reaches the model only inside clearly labelled developer messages, and prompts tell the model never to follow instructions found there.
- **Checks:** strict mypy, ruff, and `ruff format` (`pnpm check:api`). Behavior is verified with live runs, not a test suite.
