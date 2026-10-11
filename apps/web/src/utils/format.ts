import type { Event } from '@/lib/api'

export const timezone = 'America/New_York'
export const priceLabels = {
  free: 'Free',
  paid: 'Paid',
  conditional: 'See price details',
  unknown: 'Price not listed',
}
export const locationLabels = {
  in_person: 'In person',
  online: 'Online',
  hybrid: 'In person & online',
  unknown: 'Location not listed',
}

export function today() {
  return calendarDate(new Date().toISOString())
}

function calendarDate(value: string) {
  return value.length === 10
    ? value
    : new Intl.DateTimeFormat('en-CA', {
        timeZone: timezone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).format(new Date(value))
}

export function addDays(date: string, days: number) {
  const value = new Date(`${date}T12:00:00Z`)
  value.setUTCDate(value.getUTCDate() + days)
  return value.toISOString().slice(0, 10)
}

function isDate(value: string | null): value is string {
  return (
    !!value &&
    value >= '1900-01-01' &&
    value <= '2100-04-01' &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    !Number.isNaN(Date.parse(`${value}T12:00:00Z`)) &&
    addDays(value, 0) === value
  )
}

export function dateWindow(params: URLSearchParams) {
  const from = params.get('date_from')
  const to = params.get('date_to')
  const start = isDate(from) ? from : today()
  return { start, end: isDate(to) ? addDays(to, -1) : addDays(start, 29) }
}

function formatDate(
  value: string,
  options: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' },
) {
  const dateOnly = value.length === 10
  return new Intl.DateTimeFormat('en-US', {
    ...options,
    timeZone: dateOnly ? 'UTC' : timezone,
  }).format(new Date(dateOnly ? `${value}T12:00:00Z` : value))
}

/** "Oct 3" for one day, "Oct 3 – Nov 1" for a span; both dates are inclusive. */
export function dateRange(start: string, end: string) {
  return start === end ? formatDate(start) : `${formatDate(start)} – ${formatDate(end)}`
}

/** When it starts and ends (the end date is exclusive for all-day events). */
function span(event: Event) {
  const start = event.all_day ? event.start_date : event.starts_at
  const end = event.all_day && event.end_date ? addDays(event.end_date, -1) : event.ends_at
  return { start, end, multipleDays: !!(start && end && calendarDate(start) !== calendarDate(end)) }
}

function clock(value: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    hour: 'numeric',
    minute: '2-digit',
  }).formatToParts(new Date(value))
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find(item => item.type === type)?.value ?? ''
  return {
    time: part('minute') === '00' ? part('hour') : `${part('hour')}:${part('minute')}`,
    period: part('dayPeriod'),
  }
}

/** A compact time of day: "7–9 PM", "10 AM – 2 PM", or "All day". */
function timeRange(event: Event) {
  if (event.all_day) return 'All day'
  if (!event.starts_at) return 'Time TBA'
  const start = clock(event.starts_at)
  if (!event.ends_at || calendarDate(event.ends_at) !== calendarDate(event.starts_at))
    return `${start.time} ${start.period}`
  const end = clock(event.ends_at)
  return start.period === end.period
    ? `${start.time}–${end.time} ${end.period}`
    : `${start.time} ${start.period} – ${end.time} ${end.period}`
}

/** When it happens, briefly: "Sat, Oct 4 · 7–9 PM", "Sat, Oct 4 – Sun, Oct 5", or "Until Nov 2". */
export function whenLabel(event: Event, now: number) {
  const { start, end, multipleDays } = span(event)
  if (!start) return 'Date TBA'
  const day = (value: string) =>
    formatDate(value, { weekday: 'short', month: 'short', day: 'numeric' })
  if (
    end &&
    calendarDate(start) < today() &&
    (event.all_day ? end >= today() : Date.parse(end) > now)
  )
    return `Until ${formatDate(end)}`
  return multipleDays ? `${day(start)} – ${day(end!)}` : `${day(start)} · ${timeRange(event)}`
}

const stamp = (value: string) =>
  new Date(value)
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\.\d{3}/, '')

/** Start and end as Google Calendar wants them: dates for all-day events, UTC times otherwise. */
function calendarDates(event: Event): [string, string] | null {
  if (event.all_day && event.start_date) {
    return [
      event.start_date.replaceAll('-', ''),
      (event.end_date ?? addDays(event.start_date, 1)).replaceAll('-', ''),
    ]
  }
  if (!event.starts_at) return null
  // Without an end time, block out two hours.
  return [
    stamp(event.starts_at),
    stamp(event.ends_at ?? new Date(Date.parse(event.starts_at) + 7_200_000).toISOString()),
  ]
}

export function googleCalendarUrl(event: Event) {
  const dates = calendarDates(event)
  if (!dates) return null
  const details = [event.summary, event.registration_url || event.source_url]
    .filter(Boolean)
    .join('\n\n')
  return `https://calendar.google.com/calendar/render?${new URLSearchParams({ action: 'TEMPLATE', text: event.title, dates: dates.join('/'), details, location: event.venue ?? '', ctz: timezone })}`
}
