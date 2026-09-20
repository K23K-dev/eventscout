import { Link } from 'react-router-dom'
import { useState } from 'react'
import type { Event } from '../api'
import { addDays, calendarDate, checkedAt, eventDate, eventTime, formatDate, locationLabels, priceLabels, today } from '../events'

export function EventCard({ event, search }: { event: Event; search: string }) {
  const [now] = useState(Date.now)
  const start = event.all_day ? event.start_date : event.starts_at
  const end = event.all_day && event.end_date ? addDays(event.end_date, -1) : event.ends_at
  const multipleDays = !!(start && end && calendarDate(start) !== calendarDate(end))
  const ongoing = !!(start && end && calendarDate(start) < today() && (event.all_day ? end >= today() : Date.parse(end) > now))
  const ticketDate = ongoing ? end : start
  return (
    <article className="group flex h-full min-w-0 flex-col rounded-2xl border border-line bg-white p-5 transition-colors hover:border-scout/50 sm:p-6">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex min-h-17 w-15 shrink-0 flex-col items-center justify-center rounded-xl bg-scout-soft text-scout" aria-label={eventDate(event)}>
            <span className="text-[10px] font-bold tracking-widest uppercase">{ticketDate ? formatDate(ticketDate, { month: 'short' }) : 'Date'}</span>
            <span className="font-display text-3xl leading-none font-semibold">{ticketDate ? formatDate(ticketDate, { day: 'numeric' }) : 'TBA'}</span>
          </div>
          <div className="text-xs leading-relaxed text-muted">
            <p className="font-semibold text-ink">{ongoing ? 'On through' : ticketDate ? formatDate(ticketDate, { weekday: 'long' }) : 'To be announced'}</p>
            <p>{multipleDays ? 'Multi-day event' : eventTime(event)}</p>
          </div>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${event.price_status === 'free' ? 'bg-scout-soft text-scout' : 'bg-paper text-muted'}`}>
          {priceLabels[event.price_status]}
        </span>
      </div>
      <p className="mb-2 truncate text-[10px] font-semibold tracking-widest text-muted uppercase">{event.sources[0]?.publisher || (event.region === 'gt' ? 'Georgia Tech' : 'Atlanta')}</p>
      <h3 className="text-lg leading-snug font-semibold tracking-tight">
        <Link className="decoration-scout underline-offset-4 hover:text-scout hover:underline" to={`/events/${event.id}`} state={{ search }}>
          {event.title}
        </Link>
      </h3>
      {event.description && <p className="mt-3 line-clamp-2 text-sm leading-relaxed text-muted">{event.description}</p>}
      <div className="mt-5 flex-1 text-xs leading-relaxed text-muted">
        <p>{event.venue || locationLabels[event.location_kind]}</p>
        {event.location_kind === 'online' && event.venue && <p>Online</p>}
        {event.location_kind === 'hybrid' && <p>In person & online</p>}
        {multipleDays && <p className="mt-1">{eventDate(event)}</p>}
      </div>
      <div className="mt-5 flex items-center justify-between gap-2 border-t border-line/70 pt-4 text-[11px] text-muted">
        <span>{event.region === 'gt' ? 'Georgia Tech' : 'Atlanta area'}</span>
        <span title={checkedAt(event.last_observed_at)}>Checked {formatDate(event.last_observed_at)}</span>
      </div>
    </article>
  )
}
