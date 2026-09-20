import { useState, type FormEvent, type ReactNode } from 'react'
import { addDays, dateWindow } from '../events'

const control = 'mt-2 min-h-11 w-full rounded-lg border border-line bg-white px-3 py-2.5 text-sm text-ink'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block text-xs font-semibold text-muted">{label}{children}</label>
}

export function Filters({ params, apply }: { params: URLSearchParams; apply: (values: Record<string, string>) => void }) {
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const { start, end } = dateWindow(params)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget)) as Record<string, string>
    const through = values.through!
    const span = (Date.parse(through) - Date.parse(values.date_from!)) / 86_400_000
    if (span < 0 || span > 89) {
      setError('Choose an end date on or after the start, within 90 days.')
      return
    }
    delete values.through
    setError('')
    apply({ ...values, date_to: addDays(through, 1) })
  }

  return (
    <aside className="self-start rounded-2xl border border-line bg-white lg:border-0 lg:bg-transparent" aria-label="Event filters">
      <button className="flex min-h-12 w-full cursor-pointer items-center justify-between p-4 text-sm font-semibold lg:hidden" type="button" aria-expanded={open} aria-controls="event-filters" onClick={() => setOpen(!open)}>
        Filter events <span aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      <h2 className="hidden pb-5 text-sm font-semibold lg:block">Filter events</h2>
      <form id="event-filters" className={`${open ? 'block' : 'hidden'} space-y-5 px-4 pb-5 lg:block lg:px-0`} onSubmit={submit}>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-1">
          <Field label="From"><input className={control} name="date_from" type="date" defaultValue={start} min="1900-01-01" max="2100-01-01" required /></Field>
          <Field label="Through"><input className={control} name="through" type="date" defaultValue={end} min="1900-01-01" max="2100-03-31" required /></Field>
        </div>
        <Field label="Area">
          <select className={control} name="region" defaultValue={params.get('region') || ''}>
            <option value="">All areas</option><option value="gt">Georgia Tech</option><option value="atlanta">Atlanta area</option>
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-1 lg:gap-5">
          <Field label="Price">
            <select className={control} name="price_status" defaultValue={params.get('price_status') || ''}>
              <option value="">Any price</option><option value="free">Free</option><option value="paid">Paid</option><option value="conditional">Conditional pricing</option><option value="unknown">Not listed</option>
            </select>
          </Field>
          <Field label="Format">
            <select className={control} name="location_kind" defaultValue={params.get('location_kind') || ''}>
              <option value="">Any format</option><option value="in_person">In person</option><option value="online">Online</option><option value="hybrid">Hybrid</option><option value="unknown">Not listed</option>
            </select>
          </Field>
        </div>
        <Field label="Venue"><input className={control} name="venue" defaultValue={params.get('venue') || ''} placeholder="e.g. Piedmont Park" maxLength={200} /></Field>
        <Field label="Audience">
          <input className={control} name="audience" defaultValue={params.get('audience') || ''} placeholder="e.g. Students" maxLength={100} aria-describedby="audience-hint" />
        </Field>
        <p id="audience-hint" className="-mt-3 text-[11px] leading-relaxed text-muted">Use an exact audience label from an event.</p>
        {error && <p className="text-sm text-red-800" role="alert">{error}</p>}
        <button className="min-h-11 w-full cursor-pointer rounded-lg bg-ink px-4 py-3 text-sm font-semibold text-white hover:bg-scout" type="submit">Apply filters</button>
      </form>
    </aside>
  )
}
