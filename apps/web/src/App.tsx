import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  ArrowRight,
  CalendarX2,
  Search,
  WifiOff,
  X,
  type LucideIcon,
} from 'lucide-react'
import { createElement, useRef, type FormEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { fetchEvents, type Event } from './api'
import { DateRangePicker } from './components/DateRangePicker'
import { EventRow } from './components/EventCard'
import { Filters } from './components/Filters'
import { Button, buttonVariants } from './components/ui/button'
import { Input } from './components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './components/ui/select'
import { Skeleton } from './components/ui/skeleton'
import { Toggle } from './components/ui/toggle'
import { ToggleGroup, ToggleGroupItem } from './components/ui/toggle-group'
import { addDays, calendarDate, dateWindow, dayHeading, formatDate, span, today } from './events'
import { topics } from './topics'

const filterKeys = [
  'q',
  'date_from',
  'date_to',
  'region',
  'price_status',
  'location_kind',
  'venue',
  'audience',
  'topic',
  'sort',
  'page',
]
const presets = [
  ['today', 'Today'],
  ['tomorrow', 'Tomorrow'],
  ['weekend', 'Weekend'],
  ['week', '7 days'],
  ['month', '30 days'],
] as const
type Preset = (typeof presets)[number][0]

/** A preset's first day and its exclusive end. */
function presetRange(kind: Preset): [string, string] {
  const now = today()
  if (kind === 'today') return [now, addDays(now, 1)]
  if (kind === 'tomorrow') return [addDays(now, 1), addDays(now, 2)]
  if (kind === 'week') return [now, addDays(now, 7)]
  if (kind === 'month') return [now, addDays(now, 30)]
  const weekday = new Date(`${now}T12:00:00Z`).getUTCDay()
  const from = addDays(now, weekday === 0 ? 0 : (6 - weekday + 7) % 7)
  return [from, addDays(from, weekday === 0 ? 1 : 2)]
}

interface Day {
  key: string
  title: string
  date?: string
  events: Event[]
}

/** Consecutive events under one heading per day; anything that began before the window is "Ongoing". */
function byDay(events: Event[], windowStart: string): Day[] {
  const days: Day[] = []
  for (const event of events) {
    const { start } = span(event)
    const date = start ? calendarDate(start) : null
    const key = !date ? 'tba' : date < windowStart ? 'ongoing' : date
    let day = days.at(-1)
    if (day?.key !== key) {
      const heading =
        key === 'ongoing'
          ? { title: 'Ongoing', date: 'Exhibitions and multi-day events' }
          : key === 'tba'
            ? { title: 'Date to be announced' }
            : dayHeading(key)
      day = { key, ...heading, events: [] }
      days.push(day)
    }
    day.events.push(event)
  }
  return days
}

export function App() {
  const [searchParams, setSearchParams] = useSearchParams()
  const resultsHeading = useRef<HTMLHeadingElement>(null)
  const params = new URLSearchParams()
  for (const key of filterKeys) {
    const value = searchParams.get(key)?.trim()
    if (value) params.set(key, value)
  }
  params.set('page_size', '20')
  const query = useQuery({
    queryKey: ['events', params.toString()],
    queryFn: ({ signal }) => fetchEvents(params, signal),
  })
  const { start, end } = dateWindow(params)
  const currentSearch = searchParams.toString() ? `?${searchParams}` : ''
  const hasFilters = filterKeys.some(key => !['sort', 'page'].includes(key) && params.has(key))
  const keywords = params.get('q') || ''
  const sort = params.get('sort') || 'relevance'
  const activePreset = presets.find(([kind]) => {
    const [from, to] = presetRange(kind)
    return from === start && addDays(to, -1) === end
  })?.[0]
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
    <main id="main" tabIndex={-1} className="mx-auto w-full max-w-4xl pb-16 focus:outline-none">
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
        <div className="-mx-4 w-[calc(100%+2rem)] overflow-x-auto px-4 scrollbar-none sm:mx-0 sm:w-auto sm:px-0">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={activePreset ?? ''}
            onValueChange={value => {
              if (value) {
                const [from, to] = presetRange(value as Preset)
                apply({ date_from: from, date_to: to })
              }
            }}
            aria-label="When"
          >
            {presets.map(([kind, label]) => (
              <ToggleGroupItem key={kind} value={kind} className="px-3">
                {label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <DateRangePicker
          start={start}
          end={end}
          custom={!activePreset}
          apply={(from, until) => apply({ date_from: from, date_to: until })}
        />
        <Toggle
          variant="outline"
          size="sm"
          className="px-3"
          pressed={free}
          onPressedChange={on => apply({ price_status: on ? 'free' : '' })}
        >
          Free
        </Toggle>
        <Toggle
          variant="outline"
          size="sm"
          className="px-3"
          pressed={campus}
          onPressedChange={on => apply({ region: on ? 'gt' : '' })}
        >
          Georgia Tech
        </Toggle>
        <Filters key={params.toString()} params={params} apply={apply} />
        <Select value={sort} onValueChange={value => apply({ sort: value })}>
          <SelectTrigger size="sm" className="ml-auto w-32" aria-label="Sort events">
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="end">
            <SelectItem value="relevance">{keywords ? 'Best match' : 'Soonest'}</SelectItem>
            <SelectItem value="start_time">By date</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div
        className="-mx-4 mt-3 flex gap-1.5 overflow-x-auto px-4 pb-1 mask-[linear-gradient(to_right,black_calc(100%-40px),transparent)] scrollbar-none sm:mx-0 sm:px-0"
        role="group"
        aria-label="Topics"
      >
        {Object.entries(topics).map(([key, item]) => {
          const on = topic === key
          return (
            <button
              key={key}
              type="button"
              aria-pressed={on}
              onClick={() => apply({ topic: on ? '' : key })}
              className={cn(
                buttonVariants({ variant: on ? 'secondary' : 'ghost', size: 'xs' }),
                'h-7 shrink-0 cursor-pointer gap-1.5 px-2.5 font-normal',
                on ? 'text-foreground' : 'text-muted-foreground',
              )}
            >
              {createElement(item.icon, {
                className: 'size-3.5',
                style: { color: item.color },
                'aria-hidden': true,
              })}
              {item.label}
            </button>
          )
        })}
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
              {formatDate(start)} – {formatDate(end)} · Eastern time
            </p>
          </div>
          {hasFilters && (
            <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={reset}>
              Clear filters
            </Button>
          )}
        </div>

        {query.isPending ? (
          <div
            className="mt-6 divide-y rounded-lg border bg-card"
            role="status"
            aria-label="Loading events"
          >
            {Array.from({ length: 6 }, (_, index) => (
              <div key={index} className="flex gap-5 px-4 py-4" aria-hidden="true">
                <Skeleton className="h-4 w-16" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3.5 w-1/2" />
                </div>
              </div>
            ))}
          </div>
        ) : query.isError ? (
          <Notice
            icon={WifiOff}
            title="We couldn’t load these events."
            text={query.error.message}
            role="alert"
          >
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void query.refetch()
              }}
            >
              Try again
            </Button>
          </Notice>
        ) : query.data.items.length === 0 ? (
          <Notice
            icon={CalendarX2}
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
            {keywords && sort === 'relevance' ? (
              <div className="mt-6 divide-y overflow-hidden rounded-lg border bg-card">
                {query.data.items.map(event => (
                  <EventRow key={event.id} event={event} backTo={`/${currentSearch}`} showDate />
                ))}
              </div>
            ) : (
              <div className="mt-4 space-y-6">
                {byDay(query.data.items, query.data.date_from).map((day, index) => (
                  <section key={`${day.key}-${index}`} aria-labelledby={`day-${index}`}>
                    <div className="sticky top-14 z-10 -mx-1 flex items-baseline gap-2 bg-background/90 px-1 py-2.5 backdrop-blur">
                      <h3 id={`day-${index}`} className="text-sm font-semibold">
                        {day.title}
                      </h3>
                      {day.date && (
                        <span className="text-sm text-muted-foreground">{day.date}</span>
                      )}
                    </div>
                    <div className="divide-y overflow-hidden rounded-lg border bg-card">
                      {day.events.map(event => (
                        <EventRow key={event.id} event={event} backTo={`/${currentSearch}`} />
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            )}
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
  icon,
  title,
  text,
  role,
  children,
}: {
  icon: LucideIcon
  title: string
  text: string
  role?: 'alert'
  children: ReactNode
}) {
  return (
    <div
      className="mt-6 flex flex-col items-center rounded-lg border border-dashed px-6 py-14 text-center"
      role={role}
    >
      <div className="flex size-10 items-center justify-center rounded-md border bg-card text-muted-foreground">
        {createElement(icon, { className: 'size-5', 'aria-hidden': true })}
      </div>
      <h3 className="mt-4 text-sm font-medium">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{text}</p>
      <div className="mt-5">{children}</div>
    </div>
  )
}
