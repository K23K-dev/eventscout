import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  ArrowUpRight,
  CalendarDays,
  CalendarPlus,
  Globe,
  Link2,
  MapPin,
  Ticket,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { createElement, useState, type ReactNode } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ApiError, fetchEvent, type Event } from '@/lib/api'
import { TopicBadge } from '@/components/EventCard'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  calendarDate,
  checkedAt,
  eventDate,
  formatDate,
  googleCalendarUrl,
  locationLabels,
  priceLabels,
  span,
  timeRange,
  today,
} from '@/lib/events'
import { topics } from '@/lib/topics'

export function EventDetail() {
  const { eventId = '' } = useParams()
  const location = useLocation()
  const from = location.state?.backTo
  const backTo =
    typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') ? from : '/'
  const query = useQuery({
    queryKey: ['event', eventId],
    queryFn: ({ signal }) => fetchEvent(eventId, signal),
  })
  const notFound = query.error instanceof ApiError && [404, 422].includes(query.error.status)

  return (
    <main
      id="main"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl flex-1 py-6 focus:outline-none sm:py-8"
    >
      <Button variant="ghost" size="sm" asChild className="-ml-2.5 text-muted-foreground">
        <Link to={backTo}>
          <ArrowLeft aria-hidden="true" />{' '}
          {backTo.startsWith('/ask') ? 'Back to chat' : 'Back to events'}
        </Link>
      </Button>

      {query.isPending ? (
        <section className="mt-6 space-y-4" aria-busy="true">
          <title>Loading event · EventScout</title>
          <p role="status" className="sr-only">
            Getting the details…
          </p>
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-10 w-3/4" />
          <Skeleton className="h-5 w-1/2" />
        </section>
      ) : query.isError ? (
        <section className="mt-6 rounded-lg border bg-card p-6">
          <title>{`${notFound ? 'Event not found' : 'Event unavailable'} · EventScout`}</title>
          <h1 className="text-lg font-semibold">
            {notFound ? 'This event couldn’t be found.' : 'The details aren’t loading.'}
          </h1>
          <p className="mt-1 max-w-lg text-sm text-muted-foreground" role="alert">
            {notFound
              ? 'The link may be incorrect, or this event may no longer be in the catalog.'
              : 'We couldn’t reach the event catalog. Give it another try in a moment.'}
          </p>
          {!notFound && (
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              disabled={query.isFetching}
              onClick={() => {
                void query.refetch()
              }}
            >
              {query.isFetching ? 'Trying again…' : 'Try again'}
            </Button>
          )}
        </section>
      ) : (
        <EventInformation key={query.data.id} event={query.data} />
      )}
    </main>
  )
}

function EventInformation({ event }: { event: Event }) {
  const [now] = useState(Date.now)
  const day = today()
  const ended = event.all_day
    ? event.end_date
      ? event.end_date <= day
      : (event.start_date ?? day) < day
    : Boolean(event.ends_at && Date.parse(event.ends_at) <= now)
  const started =
    !event.all_day &&
    !event.ends_at &&
    Boolean(event.starts_at && Date.parse(event.starts_at) <= now)
  const cancelled = event.status === 'cancelled'
  const { start, end, multipleDays } = span(event)
  const ongoing = !!(start && end && calendarDate(start) < day && !ended)
  const sheet = start && (ongoing ? end : start)
  const google = !ended && !cancelled ? googleCalendarUrl(event) : null
  const action = ended
    ? 'View original event'
    : event.registration_url
      ? 'Get tickets'
      : 'Go to event page'

  async function share() {
    try {
      await navigator.clipboard.writeText(window.location.href)
      toast.success('Link copied')
    } catch {
      toast.error('Couldn’t copy the link.')
    }
  }

  return (
    <>
      <title>{`${event.title} · EventScout`}</title>
      <div className="mt-5 grid grid-cols-1 items-start gap-y-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-x-12">
        <header className="min-w-0 lg:col-start-1 lg:row-start-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {event.topics.map(key => {
              const topic = topics[key]
              return topic && <TopicBadge key={key} topic={topic} />
            })}
          </div>
          <h1 className="mt-4 text-3xl font-semibold tracking-tight wrap-break-word sm:text-4xl">
            {event.title}
          </h1>
          {event.summary && (
            <p className="mt-3 max-w-2xl text-base text-muted-foreground sm:text-lg">
              {event.summary}
            </p>
          )}
          {(cancelled || ended || started) && (
            <p
              className={`mt-5 rounded-lg border px-4 py-3 text-sm ${cancelled ? 'border-destructive/30 bg-destructive/10 text-red-300' : 'bg-card text-muted-foreground'}`}
            >
              <span className="font-medium text-foreground">
                {cancelled
                  ? 'This event is cancelled.'
                  : ended
                    ? 'This event has ended.'
                    : 'This event has already started.'}
              </span>{' '}
              {cancelled
                ? 'Check the organizer’s page for updates.'
                : 'Check the original listing for the latest information.'}
            </p>
          )}
          {event.is_stale && (
            <p className="mt-5 rounded-lg border border-amber-400/25 bg-amber-400/10 px-4 py-3 text-sm text-amber-200">
              <span className="font-medium">Needs a refresh.</span> Check the organizer’s listing
              for the latest details.
            </p>
          )}
        </header>

        <aside
          aria-label="Plan your visit"
          className="lg:sticky lg:top-20 lg:col-start-2 lg:row-span-2 lg:row-start-1"
        >
          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center gap-3">
              {sheet && (
                <div
                  className="w-11 shrink-0 overflow-hidden rounded-md border text-center"
                  aria-hidden="true"
                >
                  <p className="border-b bg-secondary py-px text-[10px] font-medium text-muted-foreground uppercase">
                    {ongoing ? 'Until' : formatDate(sheet, { month: 'short' })}
                  </p>
                  <p className="py-0.5 text-lg font-semibold tabular-nums">
                    {formatDate(sheet, { day: 'numeric' })}
                  </p>
                </div>
              )}
              <div className="min-w-0 text-sm">
                <p className="font-medium">
                  {!start
                    ? 'Date to be announced'
                    : ongoing
                      ? `On now, until ${formatDate(end!, { month: 'long', day: 'numeric' })}`
                      : multipleDays
                        ? `${formatDate(start, { month: 'short', day: 'numeric' })} – ${formatDate(end!, { month: 'short', day: 'numeric' })}`
                        : formatDate(start, { weekday: 'long', month: 'long', day: 'numeric' })}
                </p>
                <p className="text-muted-foreground">
                  {timeRange(event)} · {priceLabels[event.price_status]}
                </p>
              </div>
            </div>
            <div className="mt-4 grid gap-2">
              <Button asChild className="w-full">
                <a
                  href={
                    cancelled || ended
                      ? event.source_url
                      : event.registration_url || event.source_url
                  }
                  target="_blank"
                  rel="noreferrer"
                >
                  {cancelled ? 'Original listing' : action} <ArrowUpRight aria-hidden="true" />
                </a>
              </Button>
              {google && (
                <Button variant="outline" asChild className="w-full">
                  <a href={google} target="_blank" rel="noreferrer">
                    <CalendarPlus aria-hidden="true" /> Add to Google Calendar
                  </a>
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground"
                onClick={() => void share()}
              >
                <Link2 aria-hidden="true" /> Copy link
              </Button>
            </div>
            <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">
              Details can change. Confirm the time and access before heading out.
            </p>
          </div>
        </aside>

        <div className="min-w-0 lg:col-start-1 lg:row-start-2">
          <dl className="divide-y rounded-lg border bg-card text-sm">
            <Detail icon={CalendarDays} label="When">
              <p>{eventDate(event)}</p>
              <p className="text-muted-foreground">
                {timeRange(event)}
                {!event.all_day && event.starts_at ? ' (Eastern)' : ''}
              </p>
            </Detail>
            <Detail icon={MapPin} label="Where">
              <p className="wrap-break-word">
                {event.venue ||
                  (event.location_kind === 'online'
                    ? 'Online; see the organizer’s page for access.'
                    : 'Venue not provided')}
              </p>
            </Detail>
            <Detail icon={Globe} label="Format">
              <p>
                {locationLabels[event.location_kind]} ·{' '}
                {event.region === 'gt' ? 'Georgia Tech' : 'Atlanta area'}
              </p>
            </Detail>
            <Detail icon={Ticket} label="Admission">
              <p>{priceLabels[event.price_status]}</p>
              {event.price_details &&
                event.price_details.toLowerCase() !==
                  priceLabels[event.price_status].toLowerCase() && (
                  <p className="wrap-break-word whitespace-pre-line text-muted-foreground">
                    {event.price_details}
                  </p>
                )}
            </Detail>
            <Detail icon={Users} label="Who can attend">
              <p>{event.audience.length ? event.audience.join(' · ') : 'Not specified'}</p>
            </Detail>
          </dl>

          <section aria-labelledby="about-heading" className="mt-8">
            <h2 id="about-heading" className="text-base font-semibold">
              About this event
            </h2>
            <p className="mt-2 text-sm leading-7 wrap-break-word whitespace-pre-line text-muted-foreground">
              {event.description ||
                'The organizer hasn’t provided a description. Visit the original listing for more details.'}
            </p>
          </section>

          <p className="mt-8 border-t pt-4 text-xs text-muted-foreground">
            {event.sources.length > 0 && (
              <>
                Listed by{' '}
                {event.sources.map((source, index) => (
                  <span key={source.slug}>
                    {index > 0 && ', '}
                    <a
                      className="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {source.name}
                    </a>
                  </span>
                ))}
                {' · '}
              </>
            )}
            {event.last_verified_at
              ? `Details checked ${checkedAt(event.last_verified_at)}`
              : 'These details haven’t been verified yet.'}
          </p>
        </div>
      </div>
    </>
  )
}

function Detail({
  icon,
  label,
  children,
}: {
  icon: LucideIcon
  label: string
  children: ReactNode
}) {
  return (
    <div className="flex gap-3 px-4 py-3 sm:gap-4">
      <dt className="flex w-32 shrink-0 items-center gap-2 self-start text-muted-foreground">
        {createElement(icon, { className: 'size-4', 'aria-hidden': true })} {label}
      </dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  )
}
