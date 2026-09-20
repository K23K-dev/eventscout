import type { Event } from './api'

export const timezone = 'America/New_York'
export const priceLabels = { free: 'Free', paid: 'Paid', conditional: 'See price details', unknown: 'Price not listed' }
export const locationLabels = { in_person: 'In person', online: 'Online', hybrid: 'In person & online', unknown: 'Location not listed' }

export function today() {
  return calendarDate(new Date().toISOString())
}

export function calendarDate(value: string) {
  return value.length === 10 ? value : new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))
}

export function addDays(date: string, days: number) {
  const value = new Date(`${date}T12:00:00Z`)
  value.setUTCDate(value.getUTCDate() + days)
  return value.toISOString().slice(0, 10)
}

export function isDate(value: string | null): value is string {
  return !!value && value >= '1900-01-01' && value <= '2100-04-01' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T12:00:00Z`)) && addDays(value, 0) === value
}

export function dateWindow(params: URLSearchParams) {
  const from = params.get('date_from')
  const to = params.get('date_to')
  const start = isDate(from) ? from : today()
  return { start, end: isDate(to) ? addDays(to, -1) : addDays(start, 29) }
}

export function formatDate(value: string, options: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' }) {
  const dateOnly = value.length === 10
  return new Intl.DateTimeFormat('en-US', { ...options, timeZone: dateOnly ? 'UTC' : timezone }).format(new Date(dateOnly ? `${value}T12:00:00Z` : value))
}

export function eventDate(event: Event) {
  const start = event.all_day ? event.start_date : event.starts_at
  if (!start) return 'Date to be announced'
  const end = event.all_day && event.end_date ? addDays(event.end_date, -1) : event.ends_at
  const options: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }
  const first = formatDate(start, options)
  return end && first !== formatDate(end, options) ? `${first} – ${formatDate(end, options)}` : first
}

export function eventTime(event: Event) {
  if (event.all_day) return 'All day'
  if (!event.starts_at) return 'Time to be announced'
  const options: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' }
  return `${formatDate(event.starts_at, options)}${event.ends_at ? ` – ${formatDate(event.ends_at, options)}` : ''}`
}

export function checkedAt(value: string) {
  return formatDate(value, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
}
