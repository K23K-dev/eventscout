import type { Answer as AnswerData } from '@/lib/api'
import { EventCard } from '@/components/EventCard'

export function Answer({ id, answer, backTo }: { id: string; answer: AnswerData; backTo: string }) {
  return (
    <div className="space-y-3">
      {answer.clarification && <p className="text-sm leading-6">{answer.clarification}</p>}
      {answer.reply && (
        <p className="text-sm leading-6 wrap-break-word whitespace-pre-line">
          {renderReply(answer.reply, id)}
        </p>
      )}
      {answer.note && (
        <p className="rounded-lg border bg-card px-3.5 py-2.5 text-sm text-muted-foreground">
          {answer.note}
        </p>
      )}
      {answer.cards.length > 0 && (
        <ol className="grid grid-cols-1 gap-2" aria-label="Recommended events">
          {answer.cards.map((event, index) => (
            <li key={event.id} id={`${id}-${index + 1}`} className="scroll-mt-20">
              <EventCard event={event} backTo={backTo} number={index + 1} />
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
        <a
          key={index}
          href={`#${id}-${citation[1]}`}
          className="mx-0.5 inline-flex size-4.5 items-center justify-center rounded-sm bg-secondary align-[1px] text-[10px] font-semibold text-foreground no-underline hover:bg-primary hover:text-primary-foreground"
          aria-label={`Event ${citation[1]}`}
        >
          {citation[1]}
        </a>
      )
    }
    const bold = /^\*\*([^*]+)\*\*$/.exec(part)
    return bold ? (
      <strong key={index} className="font-medium">
        {bold[1]}
      </strong>
    ) : (
      part
    )
  })
}
