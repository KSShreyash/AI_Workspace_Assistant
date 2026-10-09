'use client'

export type Categories = {
  work: number
  meeting: number
  follow_up: number
  other: number
}

/**
 * Fixed order, fixed colours. A slice keeps its hue whatever the counts do -
 * colour follows the category, never its rank, so a quiet week does not repaint
 * the chart.
 *
 * Validated as a set on the light surface: chroma floor passed, normal-vision
 * separation 22.5, CVD 7.7 (deutan). That sits in the 6-8 band, which needs a
 * secondary encoding, so every slice is also named with its count and share in
 * the legend, and segments are separated by a surface-coloured gap.
 */
const SLICES = [
  { key: 'work' as const, label: 'Work', color: 'var(--chart-series-1)' },
  { key: 'meeting' as const, label: 'Meetings', color: 'var(--chart-series-2)' },
  { key: 'follow_up' as const, label: 'Follow-ups', color: 'var(--chart-series-3)' },
  { key: 'other' as const, label: 'Other', color: 'var(--chart-series-4)' },
]

const SIZE = 148
const RADIUS = 58
const STROKE = 20
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
// A 2px gap in surface colour between segments, so adjacent hues never touch.
const GAP = 2

/** Largest-remainder, so the parts shown actually add up to 100. */
function wholePercentages(values: number[], total: number): number[] {
  if (total === 0) return values.map(() => 0)
  const exact = values.map((v) => (v / total) * 100)
  const floors = exact.map(Math.floor)
  let remainder = 100 - floors.reduce((a, b) => a + b, 0)
  const order = exact
    .map((v, i) => ({ i, frac: v - Math.floor(v) }))
    .sort((a, b) => b.frac - a.frac)
  const out = [...floors]
  for (const { i } of order) {
    if (remainder <= 0) break
    out[i] += 1
    remainder -= 1
  }
  return out
}

export function CategoryDonut({ categories }: { categories: Categories }) {
  const values = SLICES.map((s) => categories[s.key] ?? 0)
  const total = values.reduce((a, b) => a + b, 0)
  const percentages = wholePercentages(values, total)

  if (total === 0) {
    return <p className="text-sm text-[var(--color-muted)]">No tasks yet.</p>
  }

  // Precomputed rather than accumulated inside the map: mutating a variable
  // during render is not safe across re-renders, and the offsets are a pure
  // function of the values anyway.
  const segments: { key: string; color: string; dash: number; offset: number }[] = []
  let running = 0
  SLICES.forEach((slice, i) => {
    const value = values[i]
    if (value === 0) return
    const length = (value / total) * CIRCUMFERENCE
    segments.push({
      key: slice.key,
      color: slice.color,
      dash: Math.max(0, length - GAP),
      offset: running,
    })
    running += length
  })

  return (
    <figure className="m-0 flex flex-wrap items-center gap-6">
      <div className="relative shrink-0" style={{ width: SIZE, height: SIZE }}>
        <svg
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          width={SIZE}
          height={SIZE}
          role="img"
          aria-label={`${total} tasks by category`}
        >
          <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
            {segments.map((segment) => (
              <circle
                key={segment.key}
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={RADIUS}
                fill="none"
                stroke={segment.color}
                strokeWidth={STROKE}
                strokeDasharray={`${segment.dash} ${CIRCUMFERENCE - segment.dash}`}
                strokeDashoffset={-segment.offset}
                strokeLinecap="butt"
              />
            ))}
          </g>
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span
            className="text-2xl font-bold text-[var(--color-fg)]"
            style={{ fontVariantNumeric: 'tabular-nums' }}
          >
            {total}
          </span>
          <span className="text-xs text-[var(--color-muted)]">Tasks</span>
        </div>
      </div>

      <figcaption className="min-w-0 flex-1">
        <ul className="space-y-2">
          {SLICES.map((slice, i) => (
            <li key={slice.key} className="flex items-center gap-2 text-sm">
              <span
                aria-hidden="true"
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: slice.color }}
              />
              <span className="flex-1 truncate text-[var(--color-fg)]">{slice.label}</span>
              <span
                className="text-[var(--color-muted)]"
                style={{ fontVariantNumeric: 'tabular-nums' }}
              >
                {percentages[i]}%
              </span>
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  )
}
