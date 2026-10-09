import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ActivityChart } from './ActivityChart'
import { CategoryDonut } from './CategoryDonut'

const SERIES = [
  { date: '2026-10-01', emails: 12, tasks: 4 },
  { date: '2026-10-02', emails: 18, tasks: 6 },
  { date: '2026-10-03', emails: 9, tasks: 2 },
  { date: '2026-10-04', emails: 22, tasks: 7 },
  { date: '2026-10-05', emails: 14, tasks: 5 },
  { date: '2026-10-06', emails: 25, tasks: 9 },
  { date: '2026-10-07', emails: 20, tasks: 6 },
]

describe('ActivityChart', () => {
  it('names both series, so identity is never colour alone', () => {
    render(<ActivityChart data={SERIES} />)
    // Both the legend and the accessible table name the series; one of each is
    // the point, so assert on the count rather than on a unique match.
    expect(screen.getAllByText(/^emails$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^tasks$/i).length).toBeGreaterThanOrEqual(1)
  })

  it('exposes the numbers as a table for screen readers', () => {
    render(<ActivityChart data={SERIES} />)
    const table = screen.getByRole('table')
    // Seven days plus a header row.
    expect(within(table).getAllByRole('row')).toHaveLength(8)
  })

  it('renders without a plot when there is no activity at all', () => {
    const empty = SERIES.map((point) => ({ ...point, emails: 0, tasks: 0 }))
    render(<ActivityChart data={empty} />)
    expect(screen.getByText(/no activity/i)).toBeInTheDocument()
  })

  it('does not crash on an empty series', () => {
    const { container } = render(<ActivityChart data={[]} />)
    expect(container).toBeInTheDocument()
  })
})

describe('CategoryDonut', () => {
  const CATEGORIES = { work: 18, meeting: 10, follow_up: 8, other: 4 }

  it('labels every slice with its share, not colour alone', () => {
    render(<CategoryDonut categories={CATEGORIES} />)
    expect(screen.getByText(/work/i)).toBeInTheDocument()
    expect(screen.getByText(/meetings/i)).toBeInTheDocument()
    expect(screen.getByText(/follow-ups/i)).toBeInTheDocument()
    expect(screen.getByText(/other/i)).toBeInTheDocument()
  })

  it('shows the total in the middle', () => {
    render(<CategoryDonut categories={CATEGORIES} />)
    expect(screen.getByText('40')).toBeInTheDocument()
  })

  it('reports percentages that sum to a whole', () => {
    render(<CategoryDonut categories={CATEGORIES} />)
    const shown = screen.getAllByText(/^\d+%$/).map((n) => Number(n.textContent!.replace('%', '')))
    expect(shown.reduce((a, b) => a + b, 0)).toBe(100)
  })

  it('says so rather than drawing an empty ring when there are no tasks', () => {
    render(<CategoryDonut categories={{ work: 0, meeting: 0, follow_up: 0, other: 0 }} />)
    expect(screen.getByText(/no tasks yet/i)).toBeInTheDocument()
  })

  it('handles a single category occupying the whole ring', () => {
    render(<CategoryDonut categories={{ work: 5, meeting: 0, follow_up: 0, other: 0 }} />)
    expect(screen.getByText('100%')).toBeInTheDocument()
  })
})
