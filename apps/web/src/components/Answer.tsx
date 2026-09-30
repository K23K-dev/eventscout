import type { Answer as AnswerData } from '../api'
import { EventCard } from './EventCard'

export function Answer({ id, answer, backTo }: { id: string; answer: AnswerData; backTo: string }) {
  return (
    <div className="space-y-5">
      {answer.clarification && <p className="text-[15px] leading-7">{answer.clarification}</p>}
      {answer.reply && <p className="text-[15px] leading-7 break-words whitespace-pre-line">{renderReply(answer.reply, id)}</p>}
      {answer.note && <p className="rounded-xl border border-line bg-white px-4 py-3 text-sm leading-6 text-muted">{answer.note}</p>}
      {answer.cards.length > 0 && (
        <ol className="grid gap-4 sm:grid-cols-2" aria-label="Recommended events">
          {answer.cards.map((event, index) => (
            <li key={event.id} id={`${id}-${index + 1}`} className="relative scroll-mt-24">
              <span className="absolute -top-2 -left-2 z-10 flex size-6 items-center justify-center rounded-full bg-scout text-xs font-bold text-white" aria-hidden="true">{index + 1}</span>
              <EventCard event={event} backTo={backTo} />
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

/** Plain text with [n] citations linked to their cards and **bold** event names; never HTML. */
function renderReply(text: string, id: string) {
  return text.split(/(\[\d+\]|\*\*[^*]+\*\*)/g).map((part, index) => {
    const citation = /^\[(\d+)\]$/.exec(part)
    if (citation) {
      return (
        <a key={index} href={`#${id}-${citation[1]}`} className="mx-0.5 rounded bg-scout-soft px-1 text-xs font-semibold text-scout no-underline hover:bg-scout hover:text-white" aria-label={`Event ${citation[1]}`}>
          {citation[1]}
        </a>
      )
    }
    const bold = /^\*\*([^*]+)\*\*$/.exec(part)
    return bold ? <strong key={index} className="font-semibold">{bold[1]}</strong> : part
  })
}
