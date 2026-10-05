# Storage

Storage defines what an event is (`models.py`) and how imports write events to Postgres (`store.py`). The schema itself lives in Supabase, in the `eventscout` schema, with row-level security on every table.

## Model

Two levels keep "what each calendar said" apart from "what we show".

| Table | One row per | Holds |
| --- | --- | --- |
| `sources` | calendar | slug, publisher, display name, public URL |
| `source_records` | calendar listing | that calendar's own normalized content, raw payload, and when it was observed. Unique on `(source_id, external_id, occurrence_key)`. |
| `event_occurrences` | real-world event | the canonical content shown to users, `content_hash`, `content_version`, `last_verified_at`, and `merged_into` |

Several source records can point at one occurrence, which is how duplicates across calendars become one event. When two occurrences turn out to be the same event, one is merged into the other:
- its records move to the survivor
- it keeps `merged_into`, so old links still resolve

## `EventContent`

`EventContent` is the normalized content every adapter produces and every reader receives. It is frozen and strict (`extra="forbid"`), and it validates what adapters shouldn't have to repeat:

- A title is required. Labels (`tags`, `audience`) are trimmed, deduplicated and sorted.
- An event is either timed (`starts_at`, optional later `ends_at`) or all-day (`start_date`, exclusive `end_date`), never both.
- Timestamps must carry a timezone and are stored in UTC. `timezone` must be a real IANA zone. It defaults to America/New_York, and `region` defaults to Atlanta.
- A title that opens with "Cancelled" marks the event cancelled, whatever the feed's status says.

`content_hash` is a SHA-256 of the normalized content, so unchanged content is detectable without comparing fields.

## Writes

`EventStore.upsert_group` saves one group (a real-world event and every calendar listing of it) in a single transaction:

1. **Locking.** It takes advisory locks on each source identity, then row locks on the canonical events involved, so concurrent writers can't interleave on the same event.
2. **Stale checks.** It refuses a stale write: an observation older than the stored one, an older source revision, or different content at the same observation time. The runner reports these as issues and moves on.
3. **Canonical event.** It inserts or updates the canonical event from the group's primary observation, and stamps `last_verified_at`.
4. **Merges.** It applies any merges the matcher decided on, refusing to merge an event into itself or into one already merged elsewhere.
5. **Source records.** It upserts each source record with its own content and raw payload.

Two triggers finish the job inside the same transaction:

- **`version_event_content`** increments `content_version` when the content hash or `merged_into` changes. It raises if content changes without a new hash, so nothing can bypass the hash.
- **`enqueue_event_index_job`** queues a search-index job for every new version: `upsert`, or `delete` for cancelled and merged events. This is how [indexing](../indexing/README.md) learns about changes without the import calling it.
