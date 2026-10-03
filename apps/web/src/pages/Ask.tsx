import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUp, LoaderCircle, MessageCircle, PanelLeft, Plus, Trash2 } from 'lucide-react'
import { createElement, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import {
  ApiError,
  askEvents,
  deleteConversation,
  fetchConversation,
  fetchConversations,
  type Answer as AnswerData,
  type Event,
  type Stage,
} from '@/lib/api'
import { signIn, useSession } from '@/lib/auth'
import { Answer } from '@/components/Answer'
import { EventCard } from '@/components/EventCard'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button, buttonVariants } from '@/components/ui/button'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { topics } from '@/lib/topics'

const stages: Record<Stage, string> = {
  understanding: 'Understanding your question…',
  searching: 'Searching events…',
  writing: 'Writing an answer…',
}
const suggestions: [string, string][] = [
  ['Free jazz this weekend', 'music'],
  ['Robotics talks at Georgia Tech', 'technology'],
  ['Something fun to do with kids tomorrow', 'family and kids'],
  ['Volunteering outdoors this Saturday', 'volunteering'],
]

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
  const account = session === undefined ? undefined : (session?.user.id ?? 'guest')
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<Pending | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [deleting, setDeleting] = useState<{ id: string; title: string } | null>(null)
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
    refetchInterval: query =>
      !streaming && query.state.data?.turns.some(turn => turn.status === 'running') ? 2000 : false,
  })
  const shown = pending && pending.conversationId === conversationId ? pending : null
  const turns = (conversationId ? (conversation.data?.turns ?? []) : []).filter(
    turn => turn.id !== shown?.turnId,
  )
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
    setPending({
      requestId,
      message: text,
      conversationId: target,
      stage: 'understanding',
      results: [],
    })
    const update = (changes: Partial<Pending>) =>
      setPending(current =>
        current?.requestId === requestId ? { ...current, ...changes } : current,
      )
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
            update({
              answer: {
                reply: event.reply,
                cards: event.cards,
                clarification: event.clarification,
                note: event.note,
              },
            })
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
      update({
        error: error instanceof Error ? error.message : 'Something went wrong. Please try again.',
        resend,
      })
      return
    }
    await queryClient.invalidateQueries({ queryKey: ['conversations'] })
    if (saved) await queryClient.invalidateQueries({ queryKey: ['conversation', account, saved] })
    setPending(current => (current?.requestId === requestId && !current.error ? null : current))
  }

  async function remove(id: string) {
    try {
      await deleteConversation(id)
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : 'That conversation could not be deleted.',
      )
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
  const guestRefused =
    conversations.isPending ||
    (conversations.error instanceof ApiError && conversations.error.status === 401)
  if (session === null && guestRefused) {
    return (
      <main
        id="main"
        tabIndex={-1}
        className="flex flex-1 items-center justify-center py-16 focus:outline-none"
      >
        <title>Ask about events · EventScout</title>
        <section
          aria-labelledby="ask-heading"
          className="w-full max-w-sm rounded-xl border bg-card p-6 text-center shadow-sm"
        >
          <div className="mx-auto flex size-10 items-center justify-center rounded-lg border bg-background">
            <MessageCircle className="size-5" aria-hidden="true" />
          </div>
          <h1 id="ask-heading" className="mt-4 text-xl font-semibold tracking-tight">
            Ask about events
          </h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Describe what you’re in the mood for and get picks from real listings, each linked to
            its event.
          </p>
          <Button variant="outline" className="mt-6 w-full" onClick={() => void signIn()}>
            <GoogleMark /> Continue with Google
          </Button>
          <p className="mt-3 text-xs text-muted-foreground">Browsing stays open to everyone.</p>
        </section>
      </main>
    )
  }

  const history = (
    <div className="flex min-h-0 flex-1 flex-col">
      <Button variant="outline" size="sm" asChild className="w-full justify-start">
        <Link to="/ask" onClick={() => setHistoryOpen(false)}>
          <Plus aria-hidden="true" /> New chat
        </Link>
      </Button>
      <p className="mt-6 mb-1 px-2 text-xs font-medium text-muted-foreground">Recent</p>
      {conversations.isLoadingError ? (
        <p className="px-2 text-xs text-muted-foreground">{conversations.error.message}</p>
      ) : (
        conversations.data?.length === 0 && (
          <p className="px-2 text-xs text-muted-foreground">Your conversations will appear here.</p>
        )
      )}
      <ul id="past-chats" className="min-h-0 flex-1 space-y-px overflow-y-auto">
        {conversations.data?.map(item => (
          <li
            key={item.id}
            className={cn(
              'group flex items-center rounded-md',
              item.id === conversationId ? 'bg-accent' : 'hover:bg-accent/50',
            )}
          >
            <Link
              to={`/ask/${item.id}`}
              onClick={() => setHistoryOpen(false)}
              aria-current={item.id === conversationId ? 'page' : undefined}
              className={cn(
                'min-w-0 flex-1 truncate px-2 py-1.5 text-sm',
                item.id === conversationId
                  ? 'text-foreground'
                  : 'text-muted-foreground group-hover:text-foreground',
              )}
            >
              {item.title}
            </Link>
            <Button
              variant="ghost"
              size="icon-xs"
              className="mr-1 text-muted-foreground opacity-100 hover:text-destructive focus-visible:opacity-100 lg:opacity-0 lg:group-hover:opacity-100"
              onClick={() => setDeleting({ id: item.id, title: item.title })}
              aria-label={`Delete “${item.title}”`}
            >
              <Trash2 aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )

  return (
    <main id="main" tabIndex={-1} className="flex flex-1 focus:outline-none">
      <title>{`${conversation.data?.title ?? 'Ask about events'} · EventScout`}</title>
      <aside
        aria-label="Your conversations"
        className="hidden w-60 shrink-0 border-r pr-4 lg:block"
      >
        <div className="sticky top-14 flex h-[calc(100svh-3.5rem)] flex-col py-5">{history}</div>
      </aside>
      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent side="left" className="w-72 gap-0 p-4">
          <SheetHeader className="p-0 pb-4">
            <SheetTitle>Chats</SheetTitle>
          </SheetHeader>
          {history}
        </SheetContent>
      </Sheet>
      <AlertDialog
        open={!!deleting}
        onOpenChange={open => {
          if (!open) setDeleting(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this chat?</AlertDialogTitle>
            <AlertDialogDescription>
              “{deleting?.title}” and its answers will be removed for good.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className={buttonVariants({ variant: 'destructive' })}
              onClick={() => {
                if (deleting) void remove(deleting.id)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <section aria-labelledby="ask-heading" className="flex min-w-0 flex-1 flex-col lg:pl-8">
        <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col">
          <div className="flex items-center justify-between pt-4 lg:hidden">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setHistoryOpen(true)}
              aria-haspopup="dialog"
            >
              <PanelLeft aria-hidden="true" /> Chats
              {conversations.data?.length ? ` (${conversations.data.length})` : ''}
            </Button>
            {!empty && (
              <Button variant="ghost" size="sm" asChild>
                <Link to="/ask">
                  <Plus aria-hidden="true" /> New chat
                </Link>
              </Button>
            )}
          </div>

          {empty && !conversationId ? (
            <div className="flex flex-1 flex-col items-center justify-center py-12 text-center">
              <div className="flex size-10 items-center justify-center rounded-lg border bg-card">
                <img src="/favicon.svg?v=3" alt="" className="size-5" width="20" height="20" />
              </div>
              <h1
                id="ask-heading"
                className="mt-5 text-2xl font-semibold tracking-tight sm:text-3xl"
              >
                What are you in the mood for?
              </h1>
              <p className="mt-2 max-w-md text-sm text-muted-foreground">
                Ask in plain words. Answers come from real listings with a link to every event, and
                you can follow up with things like “only free ones.”
              </p>
              <div className="mt-8 grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
                {suggestions.map(([text, key]) => {
                  const { icon, color } = topics[key]!
                  return (
                    <Button
                      key={text}
                      variant="outline"
                      className="h-auto justify-start gap-2.5 px-3 py-2.5 text-left font-normal whitespace-normal text-muted-foreground hover:text-foreground"
                      onClick={() => void send(text)}
                    >
                      {createElement(icon, { style: { color }, 'aria-hidden': true })} {text}
                    </Button>
                  )
                })}
              </div>
            </div>
          ) : (
            <h1 id="ask-heading" className="sr-only">
              Ask about events
            </h1>
          )}

          {conversationId && conversation.isPending && !shown && (
            <p role="status" className="flex items-center gap-2 pt-8 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Loading
              conversation…
            </p>
          )}
          {conversationId && conversation.isLoadingError && (
            <p
              role="alert"
              className="mt-8 rounded-lg border bg-card px-4 py-3 text-sm text-muted-foreground"
            >
              {conversation.error.message}
            </p>
          )}

          <ol className="space-y-8 pt-6 pb-4" aria-label="Conversation">
            {turns.map(turn => (
              <li key={turn.id} className="space-y-4">
                <Message text={turn.message} />
                <Reply>
                  {turn.status === 'running' ? (
                    <Status text="Still writing this answer…" />
                  ) : turn.status === 'failed' ? (
                    <p className="text-sm text-muted-foreground">
                      This answer didn’t finish. Ask again to retry.
                    </p>
                  ) : (
                    <Answer id={turn.id} answer={turn} backTo={backTo} />
                  )}
                </Reply>
              </li>
            ))}
            {shown && (
              <li className="space-y-4" aria-busy={streaming}>
                <Message text={shown.message} />
                <Reply>
                  {shown.answer ? (
                    <Answer
                      id={shown.turnId ?? shown.requestId}
                      answer={shown.answer}
                      backTo={backTo}
                    />
                  ) : shown.error ? (
                    <div
                      role="alert"
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-sm text-red-300"
                    >
                      {shown.error}
                      <button
                        type="button"
                        onClick={() =>
                          void send(shown.message, shown.resend ? shown.requestId : undefined)
                        }
                        className="cursor-pointer font-medium text-red-200 underline underline-offset-4"
                      >
                        Try again
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <Status
                        text={
                          shown.broader
                            ? `Searching more broadly for “${shown.broader}”…`
                            : stages[shown.stage]
                        }
                      />
                      {shown.results.length > 0 && (
                        <div>
                          <p className="mb-2 text-xs text-muted-foreground">Possible matches</p>
                          <div className="grid grid-cols-1 gap-2 opacity-60">
                            {shown.results.map(event => (
                              <EventCard key={event.id} event={event} backTo={backTo} />
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </Reply>
              </li>
            )}
          </ol>

          {/* The thread fades out behind the message box instead of showing around it. */}
          <div className="sticky bottom-0 z-20 -mx-2 mt-auto bg-linear-to-t from-background from-70% px-2 pt-6 pb-4">
            <form
              onSubmit={submit}
              className="flex items-center gap-2 rounded-xl border bg-card p-1.5 pl-4 shadow-lg shadow-black/20 transition-[color,box-shadow] focus-within:border-ring/60 focus-within:ring-[3px] focus-within:ring-ring/20"
            >
              <label htmlFor="ask-input" className="sr-only">
                Ask about events
              </label>
              <input
                id="ask-input"
                value={draft}
                onChange={event => setDraft(event.target.value)}
                maxLength={500}
                autoComplete="off"
                placeholder={empty ? 'Ask about events…' : 'Ask a follow-up…'}
                className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
              <Button
                type="submit"
                size="icon-sm"
                disabled={streaming || !draft.trim()}
                aria-label={streaming ? 'Answering' : 'Send'}
              >
                {streaming ? (
                  <LoaderCircle className="animate-spin" aria-hidden="true" />
                ) : (
                  <ArrowUp aria-hidden="true" />
                )}
              </Button>
            </form>
            <p className="mt-2 hidden text-center text-xs text-muted-foreground sm:block">
              Answers only use listed events. Check the details before you go.
            </p>
          </div>
          {/* Scrolling here leaves the newest message just above the message box. */}
          <div ref={endRef} />
        </div>
      </section>
    </main>
  )
}

function Message({ text }: { text: string }) {
  return (
    <p className="ml-auto w-fit max-w-[85%] rounded-lg bg-secondary px-3.5 py-2 text-sm leading-6 wrap-break-word">
      {text}
    </p>
  )
}

function Reply({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <div
        className="mt-px flex size-6 shrink-0 items-center justify-center rounded-md border bg-card"
        aria-hidden="true"
      >
        <img src="/favicon.svg?v=3" alt="" className="size-3.5" width="14" height="14" />
      </div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

function Status({ text }: { text: string }) {
  return (
    <p role="status" className="flex h-6 items-center gap-2 text-sm text-muted-foreground">
      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> {text}
    </p>
  )
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  )
}
