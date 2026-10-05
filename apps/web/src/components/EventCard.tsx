import { MapPin } from 'lucide-react'
import { createElement, useState } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/utils/cn'
import type { Event } from '@/lib/api'
import { locationLabels, whenLabel } from '@/utils/format'
import { topicOf, type Topic } from '@/utils/topics'
import { Badge } from '@/components/ui/badge'

const priceTags: Partial<Record<Event['price_status'], string>> = {
  free: 'Free',
  paid: 'Paid',
  conditional: 'Varies',
}

// Badges sitting on a picture get a solid backing so they stay readable.
const onPicture = 'border-transparent bg-background/85 backdrop-blur'

function PriceBadge({ event, className }: { event: Event; className?: string }) {
  const price = priceTags[event.price_status]
  if (!price) return null
  return (
    <Badge
      variant="outline"
      className={cn(
        event.price_status === 'free'
          ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-400'
          : 'text-muted-foreground',
        className,
      )}
    >
      {price}
    </Badge>
  )
}

/** A category as a tinted icon with its name. */
export function TopicBadge({ topic, className }: { topic: Topic; className?: string }) {
  return (
    <Badge variant="outline" className={cn('gap-1 font-normal text-muted-foreground', className)}>
      {createElement(topic.icon, { style: { color: topic.color }, 'aria-hidden': true })}
      {topic.label}
    </Badge>
  )
}

/** The calendar's picture of the event, or a cover in its topic's color when there's none. */
export function Cover({
  event,
  className,
  iconClassName,
}: {
  event: Event
  className: string
  iconClassName: string
}) {
  const [broken, setBroken] = useState(false)
  if (event.image_url && !broken) {
    return (
      <img
        src={event.image_url}
        alt=""
        loading="lazy"
        decoding="async"
        referrerPolicy="no-referrer"
        onError={() => setBroken(true)}
        className={cn('object-cover', className)}
      />
    )
  }
  const topic = topicOf(event)
  return (
    <div
      className={cn('flex items-center justify-center', className)}
      style={{ backgroundColor: `${topic.color}1a`, color: topic.color }}
      aria-hidden="true"
    >
      {createElement(topic.icon, { className: iconClassName, strokeWidth: 1.5 })}
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

/** A picture card in an event grid; the whole card opens the event. Day-grouped grids pass no date. */
export function EventTile({
  event,
  backTo,
  showDate = false,
}: {
  event: Event
  backTo: string
  showDate?: boolean
}) {
  const [now] = useState(Date.now)
  return (
    <article className="relative flex flex-col overflow-hidden rounded-lg border bg-card transition-colors hover:border-primary/40 has-[a:focus-visible]:ring-[3px] has-[a:focus-visible]:ring-ring/50">
      <div className="relative">
        <Cover event={event} className="aspect-video w-full" iconClassName="size-10" />
        <div className="absolute top-2 left-2 flex gap-1.5">
          {event.topics.length > 0 && <TopicBadge topic={topicOf(event)} className={onPicture} />}
          <PriceBadge event={event} className={onPicture} />
        </div>
      </div>
      <div className="flex flex-1 flex-col p-3.5">
        <p className="text-xs font-medium text-primary tabular-nums">
          {whenLabel(event, now, showDate)}
        </p>
        <h3 className="mt-1 line-clamp-2 text-sm leading-5 font-medium">
          <Link
            className="after:absolute after:inset-0 focus-visible:outline-none"
            to={`/events/${event.id}`}
            state={{ backTo }}
          >
            {event.title}
          </Link>
        </h3>
        <div className="mt-auto pt-2">
          <Venue event={event} />
        </div>
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
      <Cover event={event} className="size-10 shrink-0 rounded-md border" iconClassName="size-4" />
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
