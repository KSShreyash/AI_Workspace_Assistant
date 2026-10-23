type TasksRemainingBarProps = {
  open: number
  total: number
}

/**
 * Progress through the task backlog.
 *
 * The bar fills with what is *done*, not what is open. Filling with open work
 * means the bar grows as you fall behind and empties as you finish, which reads
 * backwards — people expect a progress bar to advance toward completion.
 *
 * Colour tracks how much is left rather than how much is done, so a nearly
 * empty backlog reads calm and a large one reads urgent.
 */
const CALM_MAX_REMAINING = 0.33
const BUSY_MAX_REMAINING = 0.66

function toneFor(remainingRatio: number): { fill: string; label: string } {
  if (remainingRatio <= CALM_MAX_REMAINING) {
    return { fill: 'var(--color-accent)', label: 'on top of it' }
  }
  if (remainingRatio <= BUSY_MAX_REMAINING) {
    return { fill: 'var(--color-warning)', label: 'building up' }
  }
  return { fill: 'var(--color-danger)', label: 'piling up' }
}

export function TasksRemainingBar({ open, total }: TasksRemainingBarProps) {
  const done = Math.max(0, total - open)
  const remainingRatio = total > 0 ? open / total : 0
  const donePercent = total > 0 ? (done / total) * 100 : 0
  const tone = toneFor(remainingRatio)

  if (total === 0) {
    return (
      <div className="w-full max-w-[420px] text-center">
        <p className="text-sm font-medium text-[var(--color-muted)]">No tasks yet</p>
        <div
          role="img"
          aria-label="0 open of 0 total tasks"
          className="mt-3 h-2 w-full rounded-full bg-[var(--color-border)]"
        />
      </div>
    )
  }

  return (
    <div className="w-full max-w-[420px]">
      <div className="flex items-baseline justify-between gap-4">
        <p className="text-2xl font-bold leading-none text-[var(--color-fg)]">
          {open}
          <span className="ml-1.5 text-sm font-medium text-[var(--color-muted)]">
            {open === 1 ? 'task open' : 'tasks open'}
          </span>
        </p>
        <p className="text-xs font-medium uppercase tracking-wide" style={{ color: tone.fill }}>
          {tone.label}
        </p>
      </div>

      <div
        role="img"
        aria-label={`${open} open of ${total} total tasks`}
        className="relative mt-3 h-2 w-full overflow-hidden rounded-full bg-[var(--color-border)]"
      >
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${donePercent}%`,
            backgroundColor: tone.fill,
            transition: 'width 420ms cubic-bezier(0.22, 1, 0.36, 1), background-color 300ms ease',
          }}
        />
      </div>

      <div className="mt-2 flex items-baseline justify-between text-xs text-[var(--color-muted)]">
        <span>
          {done} of {total} done
        </span>
        <span className="tabular-nums">{Math.round(donePercent)}%</span>
      </div>
    </div>
  )
}
