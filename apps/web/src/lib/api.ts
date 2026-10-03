import { accessToken } from '@/lib/auth'

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()
const apiBaseUrl = (configuredBaseUrl || 'http://127.0.0.1:8000').replace(/\/+$/, '')

export interface EventSource {
  slug: string
  publisher: string
  name: string
  url: string
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
  summary: string | null
  topics: string[]
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

const catalogErrors: Record<number, string> = {
  404: 'This event could not be found.',
  422: 'Check your search filters and choose a date range of 1–90 days.',
}

async function request<T>(
  path: string,
  signal: AbortSignal,
  errors = catalogErrors,
  headers: HeadersInit = {},
): Promise<T> {
  try {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      headers,
      signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
    })
    if (!response.ok) {
      throw new ApiError(
        response.status,
        errors[response.status] ??
          'The event catalog is temporarily unavailable. Please try again.',
      )
    }
    return (await response.json()) as T
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

export interface Answer {
  reply: string | null
  cards: Event[]
  clarification: string | null
  note: string | null
}

export interface ConversationSummary {
  id: string
  title: string
  updated_at: string
}

export interface StoredTurn extends Answer {
  id: string
  message: string
  status: 'running' | 'done' | 'failed'
  created_at: string
}

export interface Conversation extends ConversationSummary {
  turns: StoredTurn[]
}

export type Stage = 'understanding' | 'searching' | 'writing'

export type TurnEvent =
  | { type: 'turn'; conversation_id: string; turn_id: string }
  | { type: 'status'; stage: Stage; broader?: string }
  | { type: 'results'; events: Event[] }
  | ({ type: 'answer' } & Answer)
  | { type: 'done' }
  | { type: 'error'; message: string }

const chatErrors: Record<number, string> = {
  401: 'Sign in to use AI search.',
  404: 'This conversation no longer exists.',
  409: 'Still answering your last message. Try again in a moment.',
  422: 'Messages can be up to 500 characters.',
}

/** Chat requests carry the Google sign-in; the API answers 401 without one. */
async function signedIn(): Promise<Record<string, string>> {
  const token = await accessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function fetchConversations(signal: AbortSignal): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>('/api/conversations', signal, chatErrors, await signedIn())
}

export async function fetchConversation(id: string, signal: AbortSignal): Promise<Conversation> {
  return request<Conversation>(
    `/api/conversations/${encodeURIComponent(id)}`,
    signal,
    chatErrors,
    await signedIn(),
  )
}

export async function deleteConversation(id: string): Promise<void> {
  const headers = await signedIn()
  const response = await fetch(`${apiBaseUrl}/api/conversations/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers,
  }).catch(() => null)
  if (!response?.ok && response?.status !== 404) {
    throw new ApiError(
      response?.status ?? 0,
      'That conversation could not be deleted. Please try again.',
    )
  }
}

/** Send a chat message and report each Server-Sent Event as it arrives. EventSource can't POST. */
export async function askEvents(
  body: { message: string; request_id: string; conversation_id?: string },
  onEvent: (event: TurnEvent) => void,
): Promise<void> {
  const auth = await signedIn()
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}/api/turns`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...auth },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(90_000),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach EventScout. Check your connection and try again.')
  }
  if (!response.ok || !response.body) {
    throw new ApiError(
      response.status,
      chatErrors[response.status] ?? 'AI search is temporarily unavailable. Please try again.',
    )
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return
      buffer += value.replace(/\r\n/g, '\n')
      let end: number
      while ((end = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        let type = ''
        let data = ''
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) type = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trimStart()
        }
        if (type && data) onEvent({ type, ...JSON.parse(data) } as TurnEvent)
      }
    }
  } catch {
    throw new ApiError(0, 'The connection dropped before the answer finished.')
  }
}
