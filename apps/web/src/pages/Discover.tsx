import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Search, X } from 'lucide-react'
import { createElement, useRef, type FormEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { cn } from '@/utils/cn'
import { fetchEvents } from '@/lib/api'
import { DateRangePicker } from '@/components/DateRangePicker'
import { EventTile } from '@/components/EventCard'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Toggle } from '@/components/ui/toggle'
import { dateRange, dateWindow } from '@/utils/format'
import { topics } from '@/utils/topics'

const filterKeys = ['q', 'date_from', 'date_to', 'region', 'price_status', 'topic', 'page']

export function Discover() {
  const [searchParams, setSearchParams] = useSearchParams()
  const resultsHeading = useRef<HTMLHeadingElement>(null)
  const params = new URLSearchParams()
  for (const key of filterKeys) {
    const value = searchParams.get(key)?.trim()
    if (value) params.set(key, value)
  }
  params.set('page_size', '24')
  const query = useQuery({
    queryKey: ['events', params.toString()],
    queryFn: ({ signal }) => fetchEvents(params, signal),
  })
  const { start, end } = dateWindow(params)
  const currentSearch = searchParams.toString() ? `?${searchParams}` : ''
  const hasFilters = filterKeys.some(key => key !== 'page' && params.has(key))
  const keywords = params.get('q') || ''
  const free = params.get('price_status') === 'free'
  const campus = params.get('region') === 'gt'
  const topic = params.get('topic')

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

  const heading = query.isPending
    ? 'Finding events…'
    : query.isError
      ? 'Events unavailable'
      : `${query.data.total.toLocaleString()} ${query.data.total === 1 ? 'event' : 'events'}${keywords ? ` for “${keywords}”` : ''}`

  return (
    <main id="main" tabIndex={-1} className="w-full pb-16 focus:outline-none">
      <title>Discover events · EventScout</title>
      <header className="pt-10 pb-6 sm:pt-14">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Discover</h1>
        <p className="mt-2 text-muted-foreground">
          Talks, shows, and something different for your weekend, from calendars across Georgia Tech
          and Atlanta.
        </p>
      </header>

      <form key={keywords} className="relative" onSubmit={search} role="search">
        <Search
          className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <label className="sr-only" htmlFor="search">
          Search events
        </label>
        <Input
          id="search"
          name="q"
          type="search"
          defaultValue={keywords}
          maxLength={200}
          placeholder="Search events, venues, or topics"
          className="h-10 pr-10 pl-9 [&::-webkit-search-cancel-button]:hidden"
        />
        {keywords && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="absolute top-1/2 right-1 -translate-y-1/2 text-muted-foreground"
            onClick={() => apply({ q: '' })}
            aria-label="Clear search"
          >
            <X aria-hidden="true" />
          </Button>
        )}
      </form>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <DateRangePicker
          start={start}
          end={end}
          custom={params.has('date_from') || params.has('date_to')}
          apply={(from, until) => apply({ date_from: from, date_to: until })}
        />
        <Toggle
          variant="outline"
          size="sm"
          className="px-3 data-[state=on]:text-primary"
          pressed={free}
          onPressedChange={on => apply({ price_status: on ? 'free' : '' })}
        >
          Free
        </Toggle>
        <Toggle
          variant="outline"
          size="sm"
          className="px-3 data-[state=on]:text-primary"
          pressed={campus}
          onPressedChange={on => apply({ region: on ? 'gt' : '' })}
        >
          Georgia Tech
        </Toggle>
        <Select
          value={topic ?? 'any'}
          onValueChange={value => apply({ topic: value === 'any' ? '' : value })}
        >
          <SelectTrigger size="sm" aria-label="Topic" className={cn(topic && 'text-primary')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">All topics</SelectItem>
            {Object.entries(topics).map(([key, item]) => (
              <SelectItem key={key} value={key}>
                {createElement(item.icon, { style: { color: item.color }, 'aria-hidden': true })}
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <section className="mt-8" aria-labelledby="results-heading" aria-busy={query.isFetching}>
        <div className="flex items-end justify-between gap-4 border-b pb-3">
          <div>
            <h2
              id="results-heading"
              ref={resultsHeading}
              tabIndex={-1}
              className="scroll-mt-20 text-sm font-medium focus:outline-none"
              aria-live="polite"
            >
              {heading}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {dateRange(start, end)} · Eastern time
            </p>
          </div>
          {hasFilters && (
            <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={reset}>
              Clear filters
            </Button>
          )}
        </div>

        {query.isPending ? (
          <p role="status" className="mt-6 text-sm text-muted-foreground">
            Loading events…
          </p>
        ) : query.isError ? (
          <Notice title="We couldn’t load these events." text={query.error.message} role="alert">
            <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
              Try again
            </Button>
          </Notice>
        ) : query.data.items.length === 0 ? (
          <Notice
            title={query.data.total > 0 ? 'No events on this page.' : 'No events found.'}
            text={
              query.data.total > 0
                ? 'The event list may have changed. Head back to the first page.'
                : 'Try another search, a wider date range, or fewer filters.'
            }
          >
            <Button
              variant="outline"
              size="sm"
              onClick={() => (query.data.total > 0 ? goToPage(1) : reset())}
            >
              {query.data.total > 0 ? 'Go to first page' : 'Show all events'}
            </Button>
          </Notice>
        ) : (
          <>
            <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {query.data.items.map(event => (
                <EventTile key={event.id} event={event} backTo={`/${currentSearch}`} />
              ))}
            </div>
            <nav className="mt-8 flex items-center justify-between gap-3" aria-label="Event pages">
              <Button
                variant="outline"
                size="sm"
                disabled={query.data.page <= 1}
                onClick={() => goToPage(query.data.page - 1)}
              >
                <ArrowLeft aria-hidden="true" /> Previous
              </Button>
              <span className="text-xs text-muted-foreground tabular-nums">
                Page {query.data.page} of{' '}
                {Math.ceil(query.data.total / query.data.page_size).toLocaleString()}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={!query.data.has_more}
                onClick={() => goToPage(query.data.page + 1)}
              >
                Next <ArrowRight aria-hidden="true" />
              </Button>
            </nav>
          </>
        )}
      </section>
    </main>
  )
}

function Notice({
  title,
  text,
  role,
  children,
}: {
  title: string
  text: string
  role?: 'alert'
  children: ReactNode
}) {
  return (
    <div className="mt-6 rounded-lg border border-dashed px-6 py-14 text-center" role={role}>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">{text}</p>
      <div className="mt-5">{children}</div>
    </div>
  )
}
