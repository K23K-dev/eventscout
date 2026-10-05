# Ingestion

Ingestion turns public event calendars into one deduplicated catalog in Postgres. It runs as `python -m app.ingestion` (`pnpm ingest`), every six hours in GitHub Actions. Each run:
1. Imports the next 90 days from every calendar.
2. Summarizes new or changed events ([enrichment](../enrichment/README.md)).
3. Updates the search index ([indexing](../indexing/README.md)).

Each stage is isolated: if one fails, the next still runs, and the command exits non-zero. Without the OpenAI and Pinecone keys, the import still runs and the other two stages are reported as not run.

## Pipeline

```
SOURCES (sources.py)
  │  collect every calendar concurrently           adapters → SourceCollection
  ▼
newest version per event ID                       runner._collect
  │  drop older source revisions, keep the window   runner._groups
  ▼
cross-calendar matching                            matching.group_events
  │  one group per real-world event
  ▼
EventStore.upsert_group, 4 writers                 storage/
```

- **One import at a time.** A Postgres advisory lock enforces this. The workflow's concurrency group also prevents overlapping scheduled runs.
- **The window is now → +90 days.** Events already known to the catalog stay matched even when their dates move outside it, so reschedules aren't lost.
- **The report is JSON.** It lists records seen and created/updated/unchanged/issues per calendar, plus `unique_events`: the upcoming, scheduled events this run imported. The workflow turns it into a step summary.

## Adapters

`sources.py` is the registry. Each entry names the calendar (slug, publisher, display name, public URL) and its `collect` function. Order matters: when several calendars list the same event, the earliest one in `SOURCES` supplies the canonical details.

| Adapter | Format | Calendars |
| --- | --- | --- |
| `gatech.py` | RSS category feeds | Georgia Tech campus calendar |
| `library.py` | Paginated HTML listing + Drupal detail pages | Georgia Tech Library |
| `trumba.py` | Trumba JSON, fetched in weekly slices that split down to single days when a slice hits the 1,000-event cap | Emory |
| `localist.py` | Localist iCalendar exports | Georgia State, Kennesaw State, Agnes Scott |
| `communico.py` | Per-branch iCalendar exports, merged per library system | DeKalb and Gwinnett public libraries |
| `nature.py` | HTML month views; a Squarespace list read through its calendar-export links | Trees Atlanta, South Fork Conservancy |
| `tribe.py` | The Events Calendar REST API | Piedmont Park, Cobb Travel & Tourism, ArtsATL |
| `tech_village.py` | Webflow listing + detail pages | Atlanta Tech Village |
| `chambermaster.py` | ChamberMaster month grids + detail pages | DeKalb, Brookhaven and Greater Perimeter chambers |
| `carbonhouse.py` | Venue month listings + showing pages | Atlanta Symphony, Fox Theatre, State Farm Arena |
| `puppetry.py` | Month calendar, read with the publisher's 30-second delay | Center for Puppetry Arts |

Every adapter returns a `SourceCollection` (`records.py`): parsed events, records seen, per-record issues, and warnings such as "export cap reached". A `ParsedEvent` carries:
- a stable `external_id`, never the date
- normalized `EventContent`
- the source's own revision time, when it publishes one
- the raw payload, kept for provenance (including any picture URL)

Shared code:
- `http.py` bounds every download to 60 seconds and 20 MiB.
- `parsing.py` holds the cross-publisher rules: visible text, online/in-person detection, free/paid wording, registration links, and safe localization of wall-clock times. `localize` refuses nonexistent or ambiguous DST times.
- `icalendar_feed.py` reads iCalendar exports for the iCal-based adapters.

## Parsing policy

- **No guessing.** A record without an explicit start becomes an issue, not an event; the same goes for a date without a time, or an end before the start. An all-day event must have clear date boundaries.
- **The event model is the last line of validation.** `EventContent` rejects impossible combinations. Adapters keep only the checks the model can't make:
  - stable IDs
  - links that stay on the publisher's site
  - page and size limits
  - DTD/entity guards on XML
  - time semantics
- **Isolated failures.** A bad record is reported and skipped. A failed page or calendar is reported in the run summary, and the other calendars still import.
- **Polite crawling.** Adapters use the publisher's advertised exports or APIs where they exist, bounded pagination, small concurrency, and any delay a publisher asks for.
- **Junk filters stay with the adapter that needs them.** For example, academic deadlines, closed rehearsals, away games, and "self-guided any time" attractions are filtered per publisher, because only that publisher produces them.

## Matching duplicates

Several calendars often list the same event (ArtsATL reposts a GSU recital, say). `matching.py` builds exact keys for each observation and joins observations that share one, using union-find:

| Key | Joins |
| --- | --- |
| source URL + start | the same listing seen twice |
| registration URL + title + start | one ticket page shared by two listings |
| publisher + title + start + venue | one publisher's repeated posts |
| title + start + venue + description (200+ chars) | identical reposts |
| title + start + venue name of three or more words | the same event, venue spelled the same way |
| title + start + street address | the same event, with street abbreviations normalized |

Matching also reconciles with the stored catalog:
- A record keeps its event across runs.
- A rescheduled occurrence moves without merging into another event.
- Two catalog events are merged when new evidence joins them.

Joins are blocked when they would be ambiguous, such as two occurrences of a series behind one URL or a moved event bridging two others. In those cases the occurrences are kept apart and a warning is reported.

## Adding a calendar

1. Write a `collect(client, *, window_start, window_end) -> SourceCollection` function, reusing a family adapter when the publisher's platform is already supported.
2. Add an entry to `SOURCES`, placed by how authoritative its details are.
3. Run `pnpm ingest --source <slug>` and read the report.
