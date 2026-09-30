import { useQuery } from '@tanstack/react-query'
import { useRef, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fetchEvents } from './api'
import { EventCard } from './components/EventCard'
import { Filters } from './components/Filters'
import { addDays, dateWindow, formatDate, today } from './events'

const filterKeys = ['q', 'date_from', 'date_to', 'region', 'price_status', 'location_kind', 'venue', 'audience', 'sort', 'page']

export function App() {
  const [searchParams, setSearchParams] = useSearchParams()
  const resultsHeading = useRef<HTMLHeadingElement>(null)
  const params = new URLSearchParams()
  for (const key of filterKeys) {
    const value = searchParams.get(key)?.trim()
    if (value) params.set(key, value)
  }
  params.set('page_size', '12')
  const query = useQuery({
    queryKey: ['events', params.toString()],
    queryFn: ({ signal }) => fetchEvents(params, signal),
  })
  const { start, end } = dateWindow(params)
  const currentSearch = searchParams.toString() ? `?${searchParams}` : ''
  const hasFilters = filterKeys.some(key => !['sort', 'page'].includes(key) && params.has(key))

  function apply(values: Record<string, string>) {
    const next = new URLSearchParams(searchParams)
    next.delete('page')
    for (const [key, value] of Object.entries(values)) {
      if (value.trim()) next.set(key, value.trim())
      else next.delete(key)
    }
    setSearchParams(next)
    focusResults()
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    apply({ q: String(new FormData(event.currentTarget).get('q') || '') })
  }

  function preset(kind: 'month' | 'today' | 'weekend' | 'week') {
    let from = today()
    let days = kind === 'month' ? 30 : kind === 'week' ? 7 : 1
    if (kind === 'weekend') {
      const weekday = new Date(`${from}T12:00:00Z`).getUTCDay()
      from = addDays(from, weekday === 0 ? 0 : (6 - weekday + 7) % 7)
      days = weekday === 0 ? 1 : 2
    }
    apply({ date_from: from, date_to: addDays(from, days) })
  }

  function goToPage(page: number) {
    const next = new URLSearchParams(searchParams)
    next.set('page', String(page))
    setSearchParams(next)
    focusResults()
  }

  function reset() {
    setSearchParams({})
    focusResults()
  }

  function focusResults() {
    resultsHeading.current?.focus({ preventScroll: true })
    resultsHeading.current?.scrollIntoView({ block: 'start', behavior: 'instant' })
  }

  return (
    <main id="main" tabIndex={-1} className="pb-16 focus:outline-none">
      <title>Explore events · EventScout</title>
      <section className="pt-10 pb-8 sm:pt-14 sm:pb-10" aria-labelledby="browse-heading">
        <p className="mb-4 text-[11px] font-bold tracking-[0.16em] text-scout uppercase">Your next good plan</p>
        <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <h1 id="browse-heading" className="font-display text-[clamp(2.6rem,5vw,4.25rem)] leading-[1.04] font-semibold tracking-[-0.045em]">A little more<br className="sm:hidden" /> out there.</h1>
          <p className="max-w-72 text-sm leading-relaxed text-muted">Campus talks, live music, and something different for your weekend.</p>
        </div>
        <form key={params.get('q') || ''} className="flex items-center gap-3 rounded-xl border border-line bg-white p-2 pl-4 focus-within:border-scout sm:pl-5" onSubmit={search} role="search">
          <svg className="hidden size-5 shrink-0 text-scout sm:block" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></svg>
          <label className="sr-only" htmlFor="search">Search events</label>
          <input id="search" name="q" type="search" defaultValue={params.get('q') || ''} maxLength={200} className="min-h-11 min-w-0 flex-1 bg-transparent text-base outline-offset-0 placeholder:text-muted/80" placeholder="Search events, places, or interests" />
          <button className="min-h-11 cursor-pointer rounded-lg bg-scout px-5 py-3 text-sm font-semibold text-white hover:bg-ink sm:px-8" type="submit">Search</button>
        </form>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="mr-1 text-xs text-muted">Make time for</span>
          {([['month', 'Next 30 days'], ['today', 'Today'], ['weekend', 'This weekend'], ['week', 'Next 7 days']] as const).map(([kind, label]) => (
            <button key={kind} type="button" onClick={() => preset(kind)} className="min-h-9 cursor-pointer rounded-full border border-line px-3.5 text-xs font-medium hover:border-scout hover:bg-white hover:text-scout">{label}</button>
          ))}
        </div>
      </section>

      <div className="grid items-start gap-7 border-t border-line pt-7 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-10 lg:pt-8">
        <Filters key={params.toString()} params={params} apply={apply} />
        <section className="min-w-0" aria-labelledby="results-heading" aria-busy={query.isFetching}>
          <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 id="results-heading" ref={resultsHeading} tabIndex={-1} className="scroll-mt-6 text-lg font-semibold tracking-tight" aria-live="polite">
                {query.isPending ? 'Finding events…' : query.isError ? 'Events unavailable' : `${query.data.total.toLocaleString()} ${query.data.total === 1 ? 'event' : 'events'} to explore`}
              </h2>
              <p className="mt-1 text-xs text-muted">{formatDate(start)} – {formatDate(end)} <span className="mx-1" aria-hidden="true">·</span> Eastern time</p>
            </div>
            <div className="flex items-center gap-4">
              {hasFilters && <button type="button" className="min-h-10 cursor-pointer text-xs font-semibold text-scout underline underline-offset-4" onClick={reset}>Clear filters</button>}
              <label className="text-xs text-muted">Sort by
                <select className="ml-2 min-h-10 rounded-lg border border-line bg-white px-2 text-xs text-ink" value={params.get('sort') || 'relevance'} onChange={event => apply({ sort: event.target.value })}>
                  <option value="relevance">{params.get('q') ? 'Relevance' : 'Soonest'}</option><option value="start_time">Date</option>
                </select>
              </label>
            </div>
          </div>

          {query.isPending ? (
            <div className="grid gap-4 sm:grid-cols-2" role="status" aria-label="Loading events">
              {Array.from({ length: 6 }, (_, index) => <div key={index} className="h-72 rounded-2xl border border-line bg-white p-6 motion-safe:animate-pulse" aria-hidden="true"><div className="mb-8 h-16 w-16 rounded-xl bg-scout-soft" /><div className="mb-3 h-4 w-3/4 rounded bg-paper" /><div className="h-4 w-1/2 rounded bg-paper" /></div>)}
            </div>
          ) : query.isError ? (
            <div className="rounded-2xl border border-line bg-white px-6 py-14 text-center" role="alert">
              <h3 className="text-xl font-semibold">We couldn’t load these events.</h3>
              <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-muted">{query.error.message}</p>
              <button type="button" onClick={() => { void query.refetch() }} className="mt-6 min-h-11 cursor-pointer rounded-lg bg-scout px-5 py-3 text-sm font-semibold text-white hover:bg-ink">Try again</button>
              <button type="button" onClick={reset} className="ml-4 min-h-11 cursor-pointer text-sm font-semibold text-scout underline underline-offset-4">Reset search</button>
            </div>
          ) : query.data.items.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-line bg-white px-6 py-16 text-center">
              <span className="mb-4 block font-display text-4xl text-scout" aria-hidden="true">↗</span>
              <h3 className="text-xl font-semibold">{query.data.total > 0 ? 'No events on this page.' : 'Nothing here just yet.'}</h3>
              <p className="mx-auto mt-3 max-w-sm text-sm leading-relaxed text-muted">{query.data.total > 0 ? 'The event list may have changed. Return to the first page.' : 'Try another search, a wider date range, or fewer filters.'}</p>
              <button type="button" className="mt-6 min-h-11 cursor-pointer rounded-lg bg-scout px-5 py-3 text-sm font-semibold text-white hover:bg-ink" onClick={() => query.data.total > 0 ? goToPage(1) : reset()}>{query.data.total > 0 ? 'Go to first page' : 'Browse all events'}</button>
            </div>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                {query.data.items.map(event => <EventCard key={event.id} event={event} backTo={`/${currentSearch}`} />)}
              </div>
              <nav className="mt-8 flex items-center justify-between gap-3 border-t border-line pt-6 text-xs" aria-label="Event pages">
                <button type="button" disabled={query.data.page <= 1} onClick={() => goToPage(query.data.page - 1)} className="min-h-11 cursor-pointer rounded-lg border border-line bg-white px-4 font-semibold hover:border-scout disabled:cursor-default disabled:opacity-40">← Previous</button>
                <span className="text-muted">Page {query.data.page} of {Math.ceil(query.data.total / query.data.page_size).toLocaleString()}</span>
                <button type="button" disabled={!query.data.has_more} onClick={() => goToPage(query.data.page + 1)} className="min-h-11 cursor-pointer rounded-lg border border-line bg-white px-4 font-semibold hover:border-scout disabled:cursor-default disabled:opacity-40">Next →</button>
              </nav>
            </>
          )}
        </section>
      </div>
    </main>
  )
}
