import { SlidersHorizontal } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'

const keys = ['region', 'price_status', 'location_kind', 'venue', 'audience'] as const
type Values = Record<(typeof keys)[number], string>

// Select items can't have an empty value, so "any" stands for no filter.
const choices = {
  region: [
    ['any', 'Everywhere'],
    ['gt', 'Georgia Tech'],
    ['atlanta', 'Atlanta area'],
  ],
  price_status: [
    ['any', 'Any price'],
    ['free', 'Free'],
    ['paid', 'Paid'],
    ['conditional', 'Price varies'],
    ['unknown', 'Not listed'],
  ],
  location_kind: [
    ['any', 'In person or online'],
    ['in_person', 'In person'],
    ['online', 'Online'],
    ['hybrid', 'Hybrid'],
    ['unknown', 'Not listed'],
  ],
} as const

function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  )
}

/** Everything beyond the quick toggles: area, price, format, venue, and audience. */
export function Filters({
  params,
  apply,
}: {
  params: URLSearchParams
  apply: (values: Record<string, string>) => void
}) {
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState<Values>(
    () => Object.fromEntries(keys.map(key => [key, params.get(key) ?? ''])) as Values,
  )
  const active = keys.filter(key => params.has(key)).length
  const set = (key: keyof Values) => (value: string) =>
    setValues(current => ({ ...current, [key]: value === 'any' ? '' : value }))

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setOpen(false)
    apply(values)
  }

  function reset() {
    setOpen(false)
    apply(Object.fromEntries(keys.map(key => [key, ''])))
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          <SlidersHorizontal aria-hidden="true" /> Filters
          {active > 0 && (
            <Badge variant="secondary" className="h-5 min-w-5 justify-center px-1 tabular-nums">
              {active}
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <form onSubmit={submit}>
          <p className="border-b px-4 py-3 text-sm font-medium">Filters</p>
          <div className="grid gap-4 p-4">
            {(['region', 'price_status', 'location_kind'] as const).map(key => (
              <Field
                key={key}
                id={`filter-${key}`}
                label={{ region: 'Area', price_status: 'Price', location_kind: 'Format' }[key]}
              >
                <Select value={values[key] || 'any'} onValueChange={set(key)}>
                  <SelectTrigger id={`filter-${key}`} size="sm" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {choices[key].map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            ))}
            <Field id="filter-venue" label="Venue">
              <Input
                id="filter-venue"
                className="h-8"
                value={values.venue}
                onChange={event => set('venue')(event.target.value)}
                placeholder="e.g. Piedmont Park"
                maxLength={200}
              />
            </Field>
            <Field id="filter-audience" label="Audience">
              <Input
                id="filter-audience"
                className="h-8"
                value={values.audience}
                onChange={event => set('audience')(event.target.value)}
                placeholder="e.g. Students"
                maxLength={100}
                aria-describedby="audience-hint"
              />
              <p id="audience-hint" className="text-xs text-muted-foreground">
                Matches an event’s exact audience label.
              </p>
            </Field>
          </div>
          <div className="flex items-center justify-between border-t px-4 py-3">
            <Button type="button" variant="ghost" size="sm" onClick={reset}>
              Reset
            </Button>
            <Button type="submit" size="sm">
              Apply
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  )
}
