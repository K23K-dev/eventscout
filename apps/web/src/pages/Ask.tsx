import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, askEvents, deleteConversation, fetchConversation, fetchConversations, type Answer as AnswerData, type Event, type Stage } from '../api'
import { signIn, useSession } from '../auth'
import { Answer } from '../components/Answer'
import { EventCard } from '../components/EventCard'

const stages: Record<Stage, string> = {
  understanding: 'Understanding your question…',
  searching: 'Searching events…',
  writing: 'Writing an answer…',
}
const examples = ['Free jazz this weekend', 'Robotics talks at Georgia Tech', 'Something fun to do with kids tomorrow']

interface Pending {
  requestId: string
  message: string
  conversationId?: string
  turnId?: string
  stage: Stage
  broader?: string
  results: Event[]
  answer?: AnswerData
  error?: string
  resend?: boolean
}

export function Ask() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const session = useSession()
  // Each account's chats are cached apart; undefined until a saved sign-in is restored.
  const account = session === undefined ? undefined : session?.user.id ?? 'guest'
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<Pending | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const streaming = !!pending && !pending.answer && !pending.error
  const conversations = useQuery({
    queryKey: ['conversations', account],
    queryFn: ({ signal }) => fetchConversations(signal),
    enabled: account !== undefined,
  })
  const conversation = useQuery({
    queryKey: ['conversation', account, conversationId],
    queryFn: ({ signal }) => fetchConversation(conversationId!, signal),
    enabled: account !== undefined && !!conversationId,
    // After a refresh mid-answer the server keeps writing; check back until it's saved.
    refetchInterval: query => (!streaming && query.state.data?.turns.some(turn => turn.status === 'running') ? 2000 : false),
  })
  const shown = pending && pending.conversationId === conversationId ? pending : null
  const turns = (conversationId ? conversation.data?.turns ?? [] : []).filter(turn => turn.id !== shown?.turnId)
  const empty = !turns.length && !shown
  const backTo = conversationId ? `/ask/${conversationId}` : '/ask'
  const pendingRequest = pending?.requestId
  const pendingError = pending?.error

  useEffect(() => {
    if (pendingRequest) endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [pendingRequest, pendingError])

  async function send(message: string, requestId: string = crypto.randomUUID()) {
    const text = message.trim()
    if (!text || streaming) return
    setDraft('')
    const target = conversationId
    setPending({ requestId, message: text, conversationId: target, stage: 'understanding', results: [] })
    const update = (changes: Partial<Pending>) => setPending(current => (current?.requestId === requestId ? { ...current, ...changes } : current))
    let saved = target
    try {
      await askEvents({ message: text, request_id: requestId, conversation_id: target }, event => {
        switch (event.type) {
          case 'turn':
            saved = event.conversation_id
            update({ turnId: event.turn_id, conversationId: event.conversation_id })
            if (!target) navigate(`/ask/${event.conversation_id}`, { replace: true })
            break
          case 'status':
            update({ stage: event.stage, broader: event.broader })
            break
          case 'results':
            update({ results: event.events })
            break
          case 'answer':
            update({ answer: { reply: event.reply, cards: event.cards, clarification: event.clarification, note: event.note } })
            break
          case 'error':
            update({ error: event.message })
            break
        }
      })
    } catch (error) {
      // A request ID never gets two answers, so a retry after a dropped connection or a busy
      // conversation reuses it and picks up any answer the server was already writing.
      const resend = error instanceof ApiError && (error.status === 0 || error.status === 409)
      update({ error: error instanceof Error ? error.message : 'Something went wrong. Please try again.', resend })
      return
    }
    await queryClient.invalidateQueries({ queryKey: ['conversations'] })
    if (saved) await queryClient.invalidateQueries({ queryKey: ['conversation', account, saved] })
    setPending(current => (current?.requestId === requestId && !current.error ? null : current))
  }

  async function remove(id: string, title: string) {
    if (!window.confirm(`Delete “${title}”?`)) return
    try {
      await deleteConversation(id)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'That conversation could not be deleted.')
      return
    }
    queryClient.removeQueries({ queryKey: ['conversation', account, id] })
    await queryClient.invalidateQueries({ queryKey: ['conversations'] })
    if (id === conversationId) navigate('/ask')
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void send(draft)
  }

  // Guests sign in first, unless the API lets them chat (local scripts only).
  const guestRefused = conversations.isPending || (conversations.error instanceof ApiError && conversations.error.status === 401)
  if (session === null && guestRefused) {
    return (
      <main id="main" tabIndex={-1} className="flex-1 py-10 focus:outline-none sm:py-14">
        <title>Ask about events · EventScout</title>
        <section aria-labelledby="ask-heading" className="max-w-xl">
          <h1 id="ask-heading" className="font-display text-[clamp(2.2rem,4vw,3.25rem)] leading-tight font-semibold tracking-[-0.035em]">Ask about events</h1>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Say what you’re in the mood for, like “free jazz this weekend,” and get picks from real listings, each linked to its event. Sign in with Google to start chatting. Browsing stays open to everyone.
          </p>
          <button type="button" onClick={() => void signIn()} className="mt-6 min-h-11 cursor-pointer rounded-lg bg-scout px-5 text-sm font-semibold text-white hover:bg-ink">
            Sign in with Google
          </button>
        </section>
      </main>
    )
  }

  return (
    <main id="main" tabIndex={-1} className="flex-1 py-8 focus:outline-none sm:py-10">
      <title>{`${conversation.data?.title ?? 'Ask about events'} · EventScout`}</title>
      <div className="grid items-start gap-6 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-10">
        <aside aria-label="Your conversations" className="rounded-2xl border border-line bg-white lg:sticky lg:top-6 lg:border-0 lg:bg-transparent">
          <div className="flex items-center justify-between gap-3 p-4 lg:p-0 lg:pb-5">
            <Link to="/ask" onClick={() => setHistoryOpen(false)} className="inline-flex min-h-10 items-center rounded-lg bg-ink px-4 text-sm font-semibold text-white hover:bg-scout">New chat</Link>
            <button type="button" className="min-h-10 cursor-pointer text-sm font-semibold text-scout lg:hidden" aria-expanded={historyOpen} aria-controls="past-chats" onClick={() => setHistoryOpen(!historyOpen)}>
              Past chats{conversations.data?.length ? ` (${conversations.data.length})` : ''}
            </button>
          </div>
          <div id="past-chats" className={`${historyOpen ? 'block' : 'hidden'} px-4 pb-4 lg:block lg:px-0`}>
            <h2 className="hidden pb-2 text-xs font-semibold tracking-wider text-muted uppercase lg:block">Past chats</h2>
            {conversations.isLoadingError
              ? <p className="text-xs leading-5 text-muted">{conversations.error.message}</p>
              : conversations.data?.length === 0 && <p className="text-xs leading-5 text-muted">Your conversations will appear here.</p>}
            <ul className="space-y-1">
              {conversations.data?.map(item => (
                <li key={item.id} className="flex items-center gap-1">
                  <Link to={`/ask/${item.id}`} onClick={() => setHistoryOpen(false)} aria-current={item.id === conversationId ? 'page' : undefined} className={`min-h-10 min-w-0 flex-1 truncate rounded-lg px-3 py-2.5 text-sm ${item.id === conversationId ? 'bg-scout-soft font-semibold text-scout' : 'text-ink hover:bg-white'}`}>
                    {item.title}
                  </Link>
                  <button type="button" onClick={() => void remove(item.id, item.title)} className="min-h-10 min-w-10 cursor-pointer rounded-lg text-lg text-muted hover:bg-white hover:text-red-800" aria-label={`Delete “${item.title}”`}>×</button>
                </li>
              ))}
            </ul>
          </div>
        </aside>

        <section aria-labelledby="ask-heading" className="min-w-0">
          <h1 id="ask-heading" className={empty ? 'font-display text-[clamp(2.2rem,4vw,3.25rem)] leading-tight font-semibold tracking-[-0.035em]' : 'sr-only'}>
            Ask about events
          </h1>
          {empty && !conversationId && (
            <>
              <p className="mt-3 max-w-xl text-sm leading-relaxed text-muted">
                Say what you’re in the mood for. Answers only use real listings and link to each event, and you can follow up with things like “only free ones.”
              </p>
              <div className="mt-6 flex flex-wrap gap-2">
                {examples.map(example => (
                  <button key={example} type="button" onClick={() => void send(example)} className="min-h-9 cursor-pointer rounded-full border border-line px-3.5 text-xs font-medium hover:border-scout hover:bg-white hover:text-scout">
                    {example}
                  </button>
                ))}
              </div>
            </>
          )}
          {conversationId && conversation.isLoadingError && (
            <p role="alert" className="mt-6 rounded-xl border border-line bg-white px-5 py-4 text-sm text-muted">{conversation.error.message}</p>
          )}
          <ol className="mt-8 space-y-12" aria-label="Conversation">
            {turns.map(turn => (
              <li key={turn.id} className="space-y-4">
                <Message text={turn.message} />
                {turn.status === 'running'
                  ? <p role="status" className="text-sm text-muted">Still writing this answer…</p>
                  : turn.status === 'failed'
                    ? <p className="text-sm text-muted">This answer didn’t finish. Ask again to retry.</p>
                    : <Answer id={turn.id} answer={turn} backTo={backTo} />}
              </li>
            ))}
            {shown && (
              <li className="space-y-4" aria-busy={streaming}>
                <Message text={shown.message} />
                {shown.answer ? (
                  <Answer id={shown.turnId ?? shown.requestId} answer={shown.answer} backTo={backTo} />
                ) : shown.error ? (
                  <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-5 py-4 text-sm leading-6 text-red-900">
                    {shown.error}
                    <button type="button" onClick={() => void send(shown.message, shown.resend ? shown.requestId : undefined)} className="ml-3 cursor-pointer font-semibold underline underline-offset-4">
                      Try again
                    </button>
                  </div>
                ) : (
                  <>
                    <p role="status" className="flex items-center gap-2 text-sm text-muted">
                      <span className="size-2 rounded-full bg-scout motion-safe:animate-pulse" aria-hidden="true" />
                      {shown.broader ? `Searching more broadly for “${shown.broader}”…` : stages[shown.stage]}
                    </p>
                    {shown.results.length > 0 && (
                      <div>
                        <p className="mb-3 text-xs font-semibold tracking-wider text-muted uppercase">Possible matches</p>
                        <div className="grid gap-4 opacity-80 sm:grid-cols-2">
                          {shown.results.map(event => <EventCard key={event.id} event={event} backTo={backTo} />)}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </li>
            )}
          </ol>
          {/* The thread fades out behind the message box instead of showing around it. */}
          <div className="sticky bottom-0 z-20 -mx-3 mt-4 bg-linear-to-t from-paper from-65% px-3 pt-6 pb-4 sm:pb-6">
            <form onSubmit={submit} className="flex items-center gap-3 rounded-xl border border-line bg-white p-2 pl-4 shadow-sm focus-within:border-scout">
              <label htmlFor="ask-input" className="sr-only">Ask about events</label>
              <input
                id="ask-input"
                value={draft}
                onChange={event => setDraft(event.target.value)}
                maxLength={500}
                autoComplete="off"
                placeholder={empty ? 'What are you in the mood for?' : 'Ask a follow-up'}
                className="min-h-11 min-w-0 flex-1 bg-transparent text-base outline-offset-0 placeholder:text-muted/80"
              />
              <button type="submit" disabled={streaming || !draft.trim()} className="min-h-11 cursor-pointer rounded-lg bg-scout px-5 text-sm font-semibold text-white hover:bg-ink disabled:cursor-default disabled:opacity-50">
                {streaming ? 'Answering…' : 'Send'}
              </button>
            </form>
          </div>
          {/* Scrolling here leaves the newest message just above the message box. */}
          <div ref={endRef} />
        </section>
      </div>
    </main>
  )
}

function Message({ text }: { text: string }) {
  return <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-ink px-4 py-3 text-sm leading-6 break-words text-white">{text}</p>
}
