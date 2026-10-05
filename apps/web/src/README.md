# EventScout web

A React single-page app with two ways in:

- **Discover**: browse and filter the catalog.
- **Ask**: describe what you're in the mood for and get cited picks from the AI chat.

It talks only to the EventScout API, plus Supabase for Google sign-in.

Stack: React 19, React Router 7, TanStack Query, Tailwind CSS v4 with shadcn/ui, Vite, TypeScript, Prettier and ESLint (`pnpm check`).

## Layout

```
src/
├─ main.tsx       routes, query client, toaster
├─ styles.css     Tailwind theme tokens
├─ pages/         one component per route
├─ components/    app components; ui/ holds the shadcn primitives
├─ lib/           connections to outside services
│  ├─ api.ts      API types, fetch helpers, the SSE reader
│  └─ auth.ts     Supabase Google sign-in
└─ utils/         plain helpers
   ├─ cn.ts       class merging for shadcn
   ├─ format.ts   dates, times and labels, always in Atlanta time
   └─ topics.ts   the enrichment topics' labels, icons and colors
```

| Route                   | Page                                                                                                                                               |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`                     | `Discover`: search, date range, Free / Georgia Tech toggles, topic, and paged results (grouped by day when browsing, by best match when searching) |
| `/events/:eventId`      | `EventDetail`: picture, summary, facts, the organizer's link, Add to Google Calendar                                                               |
| `/ask/:conversationId?` | `Ask`: the chat, with history in a sidebar (a sheet on phones)                                                                                     |

## Design decisions

- **The URL is Discover's state.** Every filter lives in the query string, so links can be shared, the back button works, and TanStack Query caches each filter combination. Only known keys are forwarded to the API, which rejects anything else.
- **Server state lives in TanStack Query.** Queries are keyed by their inputs, with a 60-second stale time and no automatic retries; error states offer a "Try again" button. Chat history is cached per signed-in account, so switching accounts never shows the previous user's chats.
- **Streaming without `EventSource`.** The chat endpoint needs a POST body and an `Authorization` header, which `EventSource` can't send. `askEvents` in `lib/api.ts` reads Server-Sent Events from a `fetch` response stream instead. The page then shows, in order:
  1. a status line (understanding → searching → writing)
  2. the candidate cards as soon as they arrive
  3. the answer with numbered citations, which link to their cards

  Answers are saved server-side. After a refresh mid-answer the page polls until the saved turn is done, and if the connection drops, "Try again" sends the message again.

- **Lean sign-in.** `lib/auth.ts` uses `@supabase/auth-js` alone. The full supabase-js client pushed the bundle past 500 kB. Sign-in uses the PKCE flow: the returning one-time code is removed from the address before the router sees it, then exchanged for a session. The session reaches React through `useSyncExternalStore`. Browsing never needs an account, and only the chat sends a token.
- **Times are Atlanta times.** The catalog is local to Atlanta, so every date and time is formatted in America/New_York with `Intl`, whatever the visitor's timezone. Date-only values (all-day events, URL dates) are treated as calendar days, never as instants.
- **Cards with or without pictures.** Events whose calendar publishes a picture show it. The rest get a cover tinted with their topic's color and icon, so the grid stays even.
- **Accessible by default.**
  - A skip link sits at the top of every page, and focus moves to the main content on navigation.
  - Result counts are announced politely.
  - Each card is one big link with a visible focus ring.
  - The only controls are shadcn/Radix components with keyboard support, never native selects or alerts.
- **Theme.** Always dark: Georgia Tech navy surfaces with Tech Gold for primary actions and focus. The tokens are in `styles.css`, and every text pairing meets WCAG AA.

## Running

`pnpm dev:web` serves the app at <http://localhost:5173>. It expects the API at `http://127.0.0.1:8000`, which `VITE_API_BASE_URL` can override. Sign-in needs `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` in `apps/web/.env`. Both are public by design.
