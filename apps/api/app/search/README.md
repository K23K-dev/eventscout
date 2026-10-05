# Search

Hybrid retrieval for the chat: find the events that best match a description, under hard filters, using both keywords and meaning. It lives in one function, `search_events` (`retrieval.py`).

## How a query runs

```
query + keywords + EventFilters
  ├─ keyword ranking ── Postgres full-text search (EventRepository.search, ranked by ts_rank_cd)
  └─ vector ranking ─── OpenAI embedding of the query ─▶ Pinecone ANN with a metadata filter
             │
   reciprocal rank fusion (k = 60)
             │
   hydrate from Postgres with every exact filter (EventRepository.get_many)
             │
   collapse repeated listings ─▶ top N
```

1. **Two rankings, gathered concurrently.** Keyword search catches exact names, like a band or a speaker. Vector search catches paraphrases ("something with live music" → a jazz brunch). Each returns up to 30 IDs.
2. **Fusion by rank, not score.** Reciprocal rank fusion adds `1 / (60 + rank)` from each list. The two engines' scores aren't comparable, so only ranks are used. An event both engines rank highly wins.
3. **Postgres has the last word.** Pinecone's metadata filter is a loose superset: date overlap plus region, price and format. It can't do venue substrings, so when a venue is set it fetches ten times deeper. Hydrating the fused IDs through `get_many` re-applies every exact filter and drops events that have since ended, been cancelled, or been merged. A stale vector can never put an ineligible event in front of a user.
4. **One card per real event.** `distinct` keeps the best-ranked of listings with the same title at the same time, or the same title at the same venue. Dates are stripped from titles first, so the sessions of a series collapse too.

## Degrading gracefully

If embedding or Pinecone fails, the vector ranking comes back empty, a warning is logged, and the search continues on keywords alone. With no query at all, the filters alone apply and events come back soonest first.
