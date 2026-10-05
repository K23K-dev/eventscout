# Enrichment

Enrichment writes a one-sentence summary and one to three topics for each listed event. Calendars describe events inconsistently, and some barely at all. A short, uniform description and a fixed topic list make three things possible:
- event cards that read well
- the topic filter on Discover
- embeddings that capture what an event is about

It runs as the second stage of `pnpm ingest`.

## Design

- **Cached by content, so it's idempotent.** Results are stored in `event_enrichments` keyed by `(content_hash, prompt_version)`. An event is enriched only when its current content has no enrichment for the current prompt version. Re-running, re-importing unchanged events, or two calendars listing identical content costs nothing. Changing the instructions means bumping `PROMPT_VERSION`.
- **Soonest first, capped.** Each run takes up to 500 candidates: scheduled, unmerged events that haven't ended yet, ordered by start. That bounds cost per run, and a backlog drains over a few runs.
- **Structured output.** The model (`EVENTSCOUT_OPENAI_MODEL`, GPT-5.6 Luna by default) answers through the OpenAI Responses API's structured parsing into `Enrichment`. Topics must come from the `Topic` list in `events/models.py`, the same list the web app maps to icons and colors.
- **Code still checks the output.** The summary is normalized and must be 1–300 characters, with 1–3 distinct topics. Anything else counts as `rejected`, and that event is retried on a later run.
- **Facts stay with the listing.** The prompt asks for what happens at the event and nothing else. Dates, prices, places and eligibility stay in the canonical fields, so a summary can never contradict them. The listing (title, tags, the first 1,500 characters of the description) is sent as data, with instructions never to follow text inside it.
- **Throughput and failure.** Eight requests run at once, in chunks of 40 that are each stored in one transaction. A provider error (outage, rate limit, bad key) stops the run after the current chunk; the next run picks up where this one left off.
- **The search index follows.** Each new enrichment requeues the matching index jobs, so the next indexing stage re-embeds those events with their summary and topics.

The run report (`candidates`, `enriched`, `rejected`, `requeued`, token counts, `errors`) is printed with the import report. The workflow summary shows it.
