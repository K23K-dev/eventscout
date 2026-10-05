# Indexing

Indexing keeps a Pinecone index of event embeddings in step with the catalog, so the chat can search by meaning. It runs as the third stage of `pnpm ingest`; `pnpm ingest --reindex` re-embeds everything.

## Job queue

Postgres decides what needs indexing. The `enqueue_event_index_job` trigger adds a row to `index_jobs` for every new content version of an event: `upsert` normally, or `delete` when the event is cancelled or merged. Enrichment requeues jobs when a summary arrives. Imports never call Pinecone directly, so a Pinecone outage can't fail an import.

A run drains the queue:

1. **Crash recovery.** Jobs stuck in `processing` for over 15 minutes, left by a crashed run, go back to `pending`.
2. **Claiming.** It claims batches of 50 with `FOR UPDATE SKIP LOCKED`, so overlapping runs never take the same job.
3. **Deciding.** For each job:
   - skip it if the event already has a newer version (that version has its own job)
   - delete the vector if the event is cancelled or merged
   - expire it if the event has ended
   - otherwise embed it
4. **Embedding.** It embeds the batch with OpenAI `text-embedding-3-small` (1,536 dimensions, cosine) and upserts the vectors into the `events` namespace.
5. **Failures.** On a provider error, the batch's jobs back off exponentially (2, 4, 8… minutes). After five attempts a job is parked as `failed` for inspection. The run then stops, because an outage would fail every batch.
6. **Pruning.** Finally, it deletes vectors whose events have ended, since ending isn't a content change and queues no job.

## What gets embedded

`embedding_text` describes what an event is about, in this order:
- title
- summary and topics, from enrichment
- venue
- source tags
- audience
- the first 6,000 characters of the description

Dates, prices and formats are left out of the text. They live in metadata instead:

| Metadata | Used for |
| --- | --- |
| `starts_at`, `ends_at` (Unix seconds) | date-window filtering, and pruning ended events |
| `region`, `price_status`, `location_kind` | coarse filters at query time |

The metadata filters are deliberately loose. [Search](../search/README.md) re-checks every exact filter in Postgres, so Pinecone only has to narrow the field.

The index is created on first use (`dense_vector`, 1,536 dimensions). Its name comes from `EVENTSCOUT_PINECONE_INDEX` (default `eventscout`).
