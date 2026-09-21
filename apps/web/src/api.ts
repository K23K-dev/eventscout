const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()
const apiBaseUrl = (configuredBaseUrl || 'http://127.0.0.1:8000').replace(/\/+$/, '')

export interface EventSource {
  slug: string
  publisher: string
  name: string
  url: string
  last_observed_at: string
  last_attempt_at: string | null
  last_success_at: string | null
  health: 'healthy' | 'partial' | 'failed' | 'unknown'
  coverage_warnings: string[]
}

export interface Event {
  id: string
  title: string
  description: string
  starts_at: string | null
  ends_at: string | null
  all_day: boolean
  start_date: string | null
  end_date: string | null
  timezone: string
  venue: string | null
  location_kind: 'in_person' | 'online' | 'hybrid' | 'unknown'
  region: 'gt' | 'atlanta'
  price_status: 'free' | 'paid' | 'conditional' | 'unknown'
  price_details: string | null
  audience: string[]
  tags: string[]
  source_url: string
  registration_url: string | null
  status: 'scheduled' | 'cancelled'
  content_version: number
  last_observed_at: string
  last_verified_at: string | null
  is_stale: boolean
  sources: EventSource[]
}

export interface EventPage {
  items: Event[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  date_from: string
  date_to: string
  timezone: 'America/New_York'
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, signal: AbortSignal): Promise<T> {
  try {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
    })
    if (!response.ok) {
      const message = response.status === 404
        ? 'This event could not be found.'
        : response.status === 422
          ? 'Check your search filters and choose a date range of 1–90 days.'
          : 'The event catalog is temporarily unavailable. Please try again.'
      throw new ApiError(response.status, message)
    }
    return await response.json() as T
  } catch (error) {
    if (signal.aborted || error instanceof ApiError) throw error
    throw new ApiError(0, 'Cannot reach the event catalog. Please try again.')
  }
}

export function fetchEvents(params: URLSearchParams, signal: AbortSignal): Promise<EventPage> {
  return request<EventPage>(`/api/events?${params}`, signal)
}

export function fetchEvent(id: string, signal: AbortSignal): Promise<Event> {
  return request<Event>(`/api/events/${encodeURIComponent(id)}`, signal)
}
