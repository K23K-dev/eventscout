import { CalendarDays } from 'lucide-react'
import { useState } from 'react'
import type { DateRange } from 'react-day-picker'
import { cn } from '@/lib/utils'
import { addDays, formatDate } from '@/lib/events'
import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

// The catalog's dates are plain calendar days; the calendar works in local Date objects.
const toDate = (day: string) => new Date(`${day}T00:00:00`)
const toDay = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`

/** Pick any span up to 90 days; `end` is inclusive here and exclusive in what it applies. */
export function DateRangePicker({
  start,
  end,
  custom,
  apply,
}: {
  start: string
  end: string
  custom: boolean
  apply: (from: string, until: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [range, setRange] = useState<DateRange | undefined>()
  const from = range?.from
  const tooLong = !!(from && range?.to && toDay(range.to) > addDays(toDay(from), 89))

  return (
    <Popover
      open={open}
      onOpenChange={next => {
        setOpen(next)
        if (next) setRange({ from: toDate(start), to: toDate(end) })
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(custom && 'bg-accent text-accent-foreground')}
        >
          <CalendarDays aria-hidden="true" />{' '}
          {custom ? `${formatDate(start)} – ${formatDate(end)}` : 'Dates'}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto p-0">
        <Calendar
          mode="range"
          resetOnSelect
          selected={range}
          onSelect={setRange}
          defaultMonth={from}
          numberOfMonths={1}
        />
        <div className="flex items-center justify-between gap-3 border-t px-3 py-2.5">
          <p className={cn('text-xs', tooLong ? 'text-destructive' : 'text-muted-foreground')}>
            {tooLong ? 'Pick 90 days or fewer.' : 'Up to 90 days'}
          </p>
          <Button
            size="sm"
            disabled={!from || !range?.to || tooLong}
            onClick={() => {
              setOpen(false)
              apply(toDay(from!), addDays(toDay(range!.to!), 1))
            }}
          >
            Apply
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
