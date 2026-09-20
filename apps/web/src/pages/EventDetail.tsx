import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useLocation, useParams } from 'react-router-dom'
import { ApiError, fetchEvent, type Event } from '../api'
import { checkedAt, eventDate, eventTime, locationLabels, priceLabels } from '../events'

export function EventDetail() {
  const { eventId = '' } = useParams()
  const location = useLocation()
  const search = location.state?.search
  const backTo = typeof search === 'string' && search.startsWith('?') ? `/${search}` : '/'
  const query = useQuery({
    queryKey: ['event', eventId],
    queryFn: ({ signal }) => fetchEvent(eventId, signal),
  })
  const notFound = query.error instanceof ApiError && [404, 422].includes(query.error.status)

  return (
    <main id="main" tabIndex={-1} className="flex-1 py-9 focus:outline-none sm:py-14">
      <Link to={backTo} className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-scout hover:underline">
        <span aria-hidden="true">←</span> Back to events
      </Link>

      {query.isPending ? (
        <section className="mt-8 rounded-2xl border border-line bg-white p-8 sm:p-12" aria-busy="true">
          <title>Loading event · EventScout</title>
          <p role="status" className="text-muted">Getting the details…</p>
          <div className="mt-6 h-10 w-3/4 rounded bg-paper" aria-hidden="true" />
          <div className="mt-4 h-5 w-1/2 rounded bg-paper" aria-hidden="true" />
        </section>
      ) : query.isError ? (
        <section className="mt-8 rounded-2xl border border-line bg-white p-8 sm:p-12">
          <title>{`${notFound ? 'Event not found' : 'Event unavailable'} · EventScout`}</title>
          <h1 className="font-display text-3xl font-semibold tracking-tight">
            {notFound ? 'This event couldn’t be found.' : 'The details aren’t loading.'}
          </h1>
          <p className="mt-3 max-w-lg leading-7 text-muted" role="alert">
            {notFound
              ? 'The link may be incorrect, or this event may no longer be in the catalog.'
              : 'We couldn’t reach the event catalog. Give it another try in a moment.'}
          </p>
          {!notFound && (
            <button
              type="button"
              className="mt-6 min-h-11 cursor-pointer rounded-lg bg-scout px-5 py-2.5 text-sm font-semibold text-white hover:bg-ink disabled:cursor-wait disabled:opacity-60"
              disabled={query.isFetching}
              onClick={() => { void query.refetch() }}
            >
              {query.isFetching ? 'Trying again…' : 'Try again'}
            </button>
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
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(now)
  const ended = event.all_day
    ? (event.end_date ? event.end_date <= today : (event.start_date ?? today) < today)
    : Boolean(event.ends_at && Date.parse(event.ends_at) <= now)
  const started = !event.all_day && !event.ends_at && Boolean(event.starts_at && Date.parse(event.starts_at) <= now)
  const cancelled = event.status === 'cancelled'

  return (
    <>
      <title>{`${event.title} · EventScout`}</title>
      <header className="mt-7 max-w-4xl">
        <p className="text-xs font-bold tracking-[0.14em] text-scout uppercase">
          {event.region === 'gt' ? 'Georgia Tech' : 'Around Atlanta'}
          <span className="px-2.5 text-line" aria-hidden="true">/</span>
          {locationLabels[event.location_kind]}
        </p>
        <h1 className="mt-4 font-display text-4xl leading-[1.08] font-semibold tracking-[-0.035em] break-words sm:text-5xl lg:text-6xl">
          {event.title}
        </h1>
      </header>

      {(cancelled || ended || started) && (
        <p className={`mt-7 rounded-xl border px-5 py-4 text-sm leading-6 ${cancelled ? 'border-red-200 bg-red-50 text-red-900' : 'border-line bg-white text-muted'}`}>
          <strong className="font-semibold">{cancelled ? 'This event is cancelled.' : ended ? 'This event has ended.' : 'This event has already started.'}</strong>
          {' '}{cancelled ? 'Check the organizer’s page for updates.' : 'Check the original listing for the latest information.'}
        </p>
      )}

      <div className="mt-8 grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_350px] lg:gap-12">
        <aside aria-label="Plan your visit" className="rounded-2xl border border-line bg-white p-6 sm:p-7 lg:col-start-2 lg:row-start-1">
          <dl className="space-y-6 text-sm">
            <div>
              <dt className="text-xs font-semibold tracking-wider text-muted uppercase">When</dt>
              <dd className="mt-2 font-semibold leading-6">{eventDate(event)}</dd>
              <dd className="mt-1 leading-6 text-muted">{eventTime(event)}</dd>
              {!event.all_day && event.starts_at && <dd className="mt-1 text-xs text-muted">All times Eastern.</dd>}
            </div>
            <div>
              <dt className="text-xs font-semibold tracking-wider text-muted uppercase">Where</dt>
              <dd className="mt-2 leading-6 break-words">{event.venue || (event.location_kind === 'online' ? 'Online; see the organizer’s page for access.' : 'Venue not provided.')}</dd>
            </div>
            <div>
              <dt className="text-xs font-semibold tracking-wider text-muted uppercase">Admission</dt>
              <dd className="mt-2 font-semibold">{priceLabels[event.price_status]}</dd>
              {event.price_details && event.price_details.toLowerCase() !== priceLabels[event.price_status].toLowerCase() && <dd className="mt-1 leading-6 break-words whitespace-pre-line text-muted">{event.price_details}</dd>}
              {event.price_status === 'unknown' && <dd className="mt-1 leading-6 text-muted">Check with the organizer for pricing.</dd>}
            </div>
            <div>
              <dt className="text-xs font-semibold tracking-wider text-muted uppercase">Who can attend</dt>
              <dd className="mt-2 leading-6">{event.audience.length ? event.audience.join(' · ') : 'Not specified; check with the organizer.'}</dd>
            </div>
          </dl>
          {!cancelled && (
            <a
              className="mt-7 flex min-h-12 items-center justify-between gap-3 rounded-lg bg-scout px-4 py-3 text-sm font-semibold text-white hover:bg-ink"
              href={ended ? event.source_url : event.registration_url || event.source_url}
              target="_blank"
              rel="noreferrer"
            >
              {ended ? 'View original event' : event.registration_url ? 'Registration & tickets' : 'View event details'}
              <span aria-hidden="true">↗</span>
            </a>
          )}
          {(cancelled || event.registration_url) && (
            <a className="mt-4 inline-flex min-h-11 items-center gap-2 text-sm text-scout underline underline-offset-4" href={event.source_url} target="_blank" rel="noreferrer">
              Original listing <span aria-hidden="true">↗</span>
            </a>
          )}
          <p className="mt-5 border-t border-line pt-4 text-xs leading-5 text-muted">
            Details can change. Confirm the time, access requirements, and availability before heading out.
          </p>
        </aside>
        <div className="min-w-0 lg:col-start-1 lg:row-start-1">
          <section aria-labelledby="about-heading" className="border-t border-line pt-7">
            <h2 id="about-heading" className="font-display text-2xl font-semibold tracking-tight">About the event</h2>
            <p className="mt-4 text-[15px] leading-7 break-words whitespace-pre-line text-muted">
              {event.description || 'The organizer hasn’t provided a description. Visit the original listing for more details.'}
            </p>
            {event.tags.length > 0 && (
              <ul aria-label="Event topics" className="mt-6 flex flex-wrap gap-2">
                {event.tags.map((tag) => <li key={tag} className="rounded-full border border-line px-3 py-1 text-xs text-muted">{tag}</li>)}
              </ul>
            )}
          </section>

          <section aria-labelledby="sources-heading" className="mt-10 border-t border-line pt-6">
            <h2 id="sources-heading" className="text-sm font-semibold">From the source</h2>
            <ul className="mt-4 space-y-4">
              {event.sources.map((source) => (
                <li key={source.slug} className="text-sm">
                  <a className="font-medium text-scout underline decoration-scout/30 underline-offset-4 hover:decoration-scout" href={source.url} target="_blank" rel="noreferrer">
                    {source.name} <span aria-hidden="true">↗</span>
                  </a>
                  <p className="mt-1 text-xs leading-5 text-muted">
                    {source.publisher} · Last checked {checkedAt(source.last_observed_at)}
                  </p>
                </li>
              ))}
            </ul>
            <p className="mt-5 text-xs leading-5 text-muted">
              Last checked means we retrieved this listing, not that the organizer recently updated it.
            </p>
          </section>
        </div>

      </div>
    </>
  )
}
