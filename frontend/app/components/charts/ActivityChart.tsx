'use client'

import { useId, useState } from 'react'

export type ActivityPoint = {
  date: string
  emails: number
  tasks: number
}

const WIDTH = 560
const HEIGHT = 180
const PAD = { top: 12, right: 16, bottom: 24, left: 32 }

const PLOT_W = WIDTH - PAD.left - PAD.right
const PLOT_H = HEIGHT - PAD.top - PAD.bottom

/**
 * Series colours are chart tokens, not the UI accent.
 *
 * The app accent (#2E6B4F) measures 0.079 chroma, below the 0.1 floor a
 * categorical palette needs to stay discriminable as a thin mark - it reads as
 * grey at 2px. These two were validated together: CVD separation 7.7 (deutan),
 * normal-vision 22.5, both above 3:1 on the surface.
 *
 * 7.7 sits in the 6-8 band, which is only legal alongside a secondary encoding,
 * so both series are also directly labelled at their endpoint and listed in the
 * legend. Identity never rests on colour alone.
 */
const SERIES = [
  { key: 'emails' as const, label: 'Emails', color: 'var(--chart-series-1)' },
  { key: 'tasks' as const, label: 'Tasks', color: 'var(--chart-series-2)' },
]

function dayLabel(iso: string) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export function ActivityChart({ data }: { data: ActivityPoint[] }) {
  const gradientId = useId()
  const [hovered, setHovered] = useState<number | null>(null)

  if (data.length === 0) return null

  const max = Math.max(1, ...data.flatMap((p) => [p.emails, p.tasks]))
  const allZero = data.every((p) => p.emails === 0 && p.tasks === 0)

  const x = (i: number) =>
    PAD.left + (data.length === 1 ? PLOT_W / 2 : (i / (data.length - 1)) * PLOT_W)
  const y = (v: number) => PAD.top + PLOT_H - (v / max) * PLOT_H

  const linePath = (key: 'emails' | 'tasks') =>
    data.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(p[key])}`).join(' ')

  const areaPath = (key: 'emails' | 'tasks') =>
    `${linePath(key)} L ${x(data.length - 1)} ${PAD.top + PLOT_H} L ${x(0)} ${PAD.top + PLOT_H} Z`

  return (
    <figure className="m-0">
      <div className="flex flex-wrap items-center gap-4">
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: s.color }}
            />
            {s.label}
          </span>
        ))}
      </div>

      {allZero ? (
        <p className="mt-6 text-sm text-[var(--color-muted)]">No activity in the last 7 days.</p>
      ) : (
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="mt-3 w-full"
          role="img"
          aria-label="Emails received and tasks created per day over the last seven days"
          onMouseLeave={() => setHovered(null)}
        >
          <defs>
            {SERIES.map((s, si) => (
              <linearGradient key={s.key} id={`${gradientId}-${si}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity="0.18" />
                <stop offset="100%" stopColor={s.color} stopOpacity="0" />
              </linearGradient>
            ))}
          </defs>

          {/* Recessive grid: three lines, enough to read level without competing. */}
          {[0, 0.5, 1].map((t) => (
            <line
              key={t}
              x1={PAD.left}
              x2={PAD.left + PLOT_W}
              y1={PAD.top + PLOT_H * t}
              y2={PAD.top + PLOT_H * t}
              stroke="var(--color-border)"
              strokeWidth="1"
            />
          ))}

          {SERIES.map((s, si) => (
            <g key={s.key}>
              <path d={areaPath(s.key)} fill={`url(#${gradientId}-${si})`} />
              <path
                d={linePath(s.key)}
                fill="none"
                stroke={s.color}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {/* Emphasised endpoint doubles as the direct label anchor. */}
              <circle
                cx={x(data.length - 1)}
                cy={y(data[data.length - 1][s.key])}
                r="4"
                fill={s.color}
                stroke="var(--color-surface)"
                strokeWidth="2"
              />
            </g>
          ))}

          {hovered !== null && (
            <line
              x1={x(hovered)}
              x2={x(hovered)}
              y1={PAD.top}
              y2={PAD.top + PLOT_H}
              stroke="var(--color-muted)"
              strokeWidth="1"
              strokeDasharray="3 3"
            />
          )}

          {data.map((p, i) => (
            <g key={p.date}>
              {/* Hit target far wider than the mark it selects. */}
              <rect
                x={x(i) - PLOT_W / (data.length * 2)}
                y={PAD.top}
                width={PLOT_W / data.length}
                height={PLOT_H}
                fill="transparent"
                onMouseEnter={() => setHovered(i)}
              />
              {hovered === i &&
                SERIES.map((s) => (
                  <circle
                    key={s.key}
                    cx={x(i)}
                    cy={y(p[s.key])}
                    r="4"
                    fill={s.color}
                    stroke="var(--color-surface)"
                    strokeWidth="2"
                  />
                ))}
            </g>
          ))}

          {data.map((p, i) => (
            <text
              key={p.date}
              x={x(i)}
              y={HEIGHT - 6}
              textAnchor="middle"
              className="fill-[var(--color-muted)]"
              style={{ fontSize: 10 }}
            >
              {dayLabel(p.date)}
            </text>
          ))}
        </svg>
      )}

      {hovered !== null && (
        <p className="mt-2 text-xs text-[var(--color-muted)]" role="status">
          {dayLabel(data[hovered].date)} · {data[hovered].emails} emails ·{' '}
          {data[hovered].tasks} tasks
        </p>
      )}

      {/* The same numbers, reachable without reading the plot. */}
      <figcaption className="sr-only">
        <table>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Emails</th>
              <th scope="col">Tasks</th>
            </tr>
          </thead>
          <tbody>
            {data.map((p) => (
              <tr key={p.date}>
                <th scope="row">{dayLabel(p.date)}</th>
                <td>{p.emails}</td>
                <td>{p.tasks}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </figcaption>
    </figure>
  )
}
