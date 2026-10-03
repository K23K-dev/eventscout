import { MapPin } from 'lucide-react'
import { createElement, useState } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import type { Event } from '@/lib/api'
import { locationLabels, whenLabel } from '@/lib/events'
import { topicOf, type Topic } from '@/lib/topics'
import { Badge } from '@/components/ui/badge'

const priceTags: Partial<Record<Event['price_status'], string>> = {
  free: 'Free',
  paid: 'Paid',
  conditional: 'Varies',
}

function PriceBadge({ event }: { event: Event }) {
  const price = priceTags[event.price_status]
  if (!price) return null
  return (
    <Badge
      variant="outline"
      className={
        event.price_status === 'free'
          ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-400'
          : 'text-muted-foreground'
      }
    >
      {price}
    </Badge>
  )
}

/** A category as a tinted icon with its name. */
export function TopicBadge({ topic }: { topic: Topic }) {
  return (
    <Badge variant="outline" className="gap-1 font-normal text-muted-foreground">
      {createElement(topic.icon, { style: { color: topic.color }, 'aria-hidden': true })}
      {topic.label}
    </Badge>
  )
}

/** A small tinted square with the category icon, standing in for a cover image. */
function TopicIcon({ event, className }: { event: Event; className?: string }) {
  const topic = topicOf(event)
  return (
    <div
      className={cn(
        'flex size-8 shrink-0 items-center justify-center rounded-md border',
        className,
      )}
      style={{
        backgroundColor: `${topic.color}14`,
        borderColor: `${topic.color}29`,
        color: topic.color,
      }}
      aria-hidden="true"
    >
      {createElement(topic.icon, { className: 'size-4', strokeWidth: 1.75 })}
    </div>
  )
}

function Venue({ event }: { event: Event }) {
  const source = event.sources[0]?.publisher
  return (
    <p className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
      <MapPin className="size-3 shrink-0" aria-hidden="true" />
      <span className="truncate">
        {event.venue || locationLabels[event.location_kind]}
        {source && ` · ${source}`}
      </span>
    </p>
  )
}

/** One row in an event list; the whole row opens the event. Lists grouped by day pass no date. */
export function EventRow({
  event,
  backTo,
  showDate = false,
}: {
  event: Event
  backTo: string
  showDate?: boolean
}) {
  const [now] = useState(Date.now)
  const price = priceTags[event.price_status]
  const blurb = event.summary || event.description
  return (
    <article className="relative flex flex-col gap-1 px-4 py-3.5 transition-colors hover:bg-accent/40 has-[a:focus-visible]:bg-accent/40 sm:flex-row sm:gap-5">
      <p
        className={cn(
          'shrink-0 text-xs text-muted-foreground tabular-nums sm:pt-0.5 sm:text-sm',
          showDate ? 'sm:w-44' : 'sm:w-24',
        )}
      >
        {whenLabel(event, now, showDate)}
        {price && <span className="sm:hidden"> · {price}</span>}
      </p>
      <div className="min-w-0 flex-1">
        <h3 className="text-sm leading-5 font-medium">
          <Link
            className="after:absolute after:inset-0 focus-visible:outline-none"
            to={`/events/${event.id}`}
            state={{ backTo }}
          >
            {event.title}
          </Link>
        </h3>
        {blurb && <p className="mt-0.5 line-clamp-1 text-sm text-muted-foreground">{blurb}</p>}
        <div className="mt-1.5">
          <Venue event={event} />
        </div>
        {event.is_stale && (
          <p className="mt-1.5 text-xs text-amber-400">Details may be out of date</p>
        )}
      </div>
      <div className="hidden shrink-0 items-start gap-1.5 sm:flex">
        {event.topics.length > 0 && <TopicBadge topic={topicOf(event)} />}
        <PriceBadge event={event} />
      </div>
    </article>
  )
}

/** A compact event card for chat answers, numbered to match the reply's citations. */
export function EventCard({
  event,
  backTo,
  number,
}: {
  event: Event
  backTo: string
  number?: number
}) {
  const [now] = useState(Date.now)
  return (
    <article className="relative flex min-w-0 items-start gap-3 rounded-lg border bg-card p-3 transition-colors hover:bg-accent/40 has-[a:focus-visible]:ring-[3px] has-[a:focus-visible]:ring-ring/50">
      <TopicIcon event={event} />
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground tabular-nums">
          {number !== undefined && (
            <span className="flex size-4 items-center justify-center rounded-sm bg-secondary text-[10px] font-semibold text-foreground">
              {number}
            </span>
          )}
          {whenLabel(event, now, true)}
        </p>
        <h3 className="mt-0.5 text-sm leading-5 font-medium">
          <Link
            className="after:absolute after:inset-0 focus-visible:outline-none"
            to={`/events/${event.id}`}
            state={{ backTo }}
          >
            {event.title}
          </Link>
        </h3>
        <div className="mt-1">
          <Venue event={event} />
        </div>
      </div>
      <PriceBadge event={event} />
    </article>
  )
}
