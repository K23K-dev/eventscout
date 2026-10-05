# Events

The public, read-only catalog API. The web app's Discover and event pages use it, and so does the chat when it hydrates search results from Postgres.

## Endpoints

| Route | Returns |
| --- | --- |
| `GET /api/events` | One page of upcoming events matching `EventFilters`, with the total |
| `GET /api/events/{id}` | One event, including past or cancelled ones. The ID of a merged event resolves to the event it was merged into. |

`EventFilters` (`models.py`) validates the query string and rejects unknown parameters:

| Parameter | Meaning |
| --- | --- |
| `q` | Keywords; supports quoted phrases and `or` (Postgres `websearch_to_tsquery`) |
| `date_from`, `date_to` | A window of America/New_York calendar dates. The end is exclusive; the default is today plus 30 days, and the maximum is 90 days. |
| `region`, `price_status`, `location_kind` | Exact matches on the normalized fields |
| `venue` | Case-insensitive substring of the venue |
| `topic` | Events enrichment tagged with that topic |
| `page`, `page_size` | Offset pagination, up to 100 per page |

## Queries

`EventRepository` (`repository.py`) holds all catalog SQL.

- **Overlap, not start.** A window matches every scheduled, unmerged event that overlaps it. All-day dates are interpreted in each event's own timezone, and a timed event without an end matches if it starts inside the window. Ongoing exhibitions therefore appear.
- **Ordering.**
  1. Keyword rank, when there are keywords: `ts_rank_cd` over a weighted, generated `search_document` (title, then venue, then description).
  2. Events starting in the window before ones already running, so months-long exhibitions don't fill the first page.
  3. Start time, then ID, which keeps paging stable.
- **Detail columns** come from lateral joins:
  - `sources`: the calendars that list the event, for "Listed by" links
  - the latest enrichment's `summary` and `topics`
  - the newest picture any listing published (`image_url`)

  Events with no listing never appear.
- **Methods for the chat.** `get_many` re-applies every filter to a set of IDs, which is how [search](../search/README.md) drops vector hits that don't qualify. `get_by_ids` returns events in a given order for chat history.

## Request handling

Each request opens its own connection and runs in a `READ ONLY`, `REPEATABLE READ` transaction, so the count and the page come from one snapshot. A 5-second statement timeout and an 8-second overall timeout keep a slow database from tying up the server. Any database failure becomes a 503 ("temporarily unavailable"), which the web app shows as a retryable state.

`EventResponse` is `EventContent` plus `id`, `content_version`, the source list, enrichment `summary`/`topics`, and `image_url`. It's the same shape the chat streams in its event cards.
