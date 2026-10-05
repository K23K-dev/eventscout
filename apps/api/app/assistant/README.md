# Assistant

The AI chat behind `/ask`. A user types a request such as "free jazz this weekend" and gets a short answer that cites real events from the catalog, streamed as it's produced. Follow-ups such as "only free ones" refine the same search.

The design rule: **Postgres supplies every fact; the model only chooses and explains.** The model never sees anything but numbered candidates from the catalog, and code checks its citations before anything is shown.

## Pipeline (LangGraph)

```
START ─▶ parse ──clarification──▶ END
           │
           ▼
        retrieve ◀───────────────── one broader search, if nothing fit
           │                               │
           ▼                               │
        explain ◀── fix citations ──┐      │
           │                        │      │
           ▼                        │      │
        validate ───────────────────┴──────┴─▶ END
```

- **parse** (`intent.py`). GPT-5.6 Luna turns the message into a `SearchIntent`: only what this message changes. That covers the query and keywords, a date phrase, region, price, format, venue, and a clarification question for messages that can't become a search, such as greetings. Code then merges the intent into the conversation's `SearchState`. Unmentioned constraints carry over, dates like "this weekend" resolve in code against today in Atlanta, and windows are capped at 90 days. The model never computes dates.
- **retrieve** finds 8 candidates with [hybrid search](../search/README.md), inside a read-only snapshot transaction.
- **explain** sends the search and the numbered candidates to the model as data. Each candidate carries its title, when, where, format, price, who can attend, publisher, and a description excerpt. The model writes 2–4 sentences citing candidates as `[n]`, and says whether anything matched. When nothing fits, it may also suggest a broader search.
- **validate** is plain code:
  - Out-of-range citations send the draft back to explain once, with the problem stated.
  - When nothing matched, at most two alternatives are allowed.
  - When nothing was cited and the model suggested a broader search that hasn't been tried, the graph loops back to retrieve once.
  - Citations are renumbered to match the cards shown, up to five.
  - If drafting fails outright, the user still gets the matching cards with a short note.

`run_turn` streams each node's state update, which is what lets the endpoint report progress.

## Streaming endpoint

`POST /api/turns` (`router.py`) answers with Server-Sent Events:

| Event | When |
| --- | --- |
| `turn` | At once: the conversation and turn IDs. A new conversation is created on the first message. |
| `status` | `understanding`, then `searching` (with the broader phrase on a second search), then `writing` |
| `results` | Candidate cards as soon as retrieval ends, before the answer is written |
| `answer` | The reply, the cited cards, or a clarification or note |
| `done` / `error` | End of the stream |

Before streaming starts, the dependency `start_turn` raises anything that can be expressed as a status code:
- 401: no sign-in
- 404: unknown conversation
- 409: an answer is already being written in this conversation
- 503: AI isn't configured or reachable

The answer is produced in a background task that outlives the request. The stream only relays it through a queue, so closing the tab or refreshing mid-answer doesn't lose it; the turn is saved anyway, and the web app polls until it's done. Each server process writes at most two answers at once. The OpenAI and Pinecone clients open on first use, so the API starts without them.

## Conversations

`store.py` keeps `conversations` and `turns`, every query scoped to the owner. The owner is the user ID from a verified Supabase access token (see `app/auth.py`).

- A turn moves `running` → `done` or `failed`. It stores the search state, the cited event IDs, and the reply, clarification or note.
- A unique partial index allows one running turn per conversation. A running turn older than two minutes counts as abandoned and is marked failed when the next message arrives.
- A follow-up starts from the search state of the latest answered turn.
- History endpoints: `GET /api/conversations` (50 most recent), `GET /api/conversations/{id}` (turns with their event cards, re-read from the catalog), and `DELETE /api/conversations/{id}`.

## Safety

- Listing text and earlier searches go to the model only in developer messages labelled as data, and the instructions say never to follow anything inside them.
- Structured outputs (`SearchIntent`, `Draft`) are parsed and validated, never executed.
- Event facts in answers come from the cards, which the web app renders from catalog data. The reply text is rendered as plain text with citation links, never as HTML.
