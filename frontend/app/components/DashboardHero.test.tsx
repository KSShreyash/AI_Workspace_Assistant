import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DashboardHero } from './DashboardHero'

describe('DashboardHero', () => {
  it('greets the person by name', () => {
    render(<DashboardHero name="Shreyash" now={new Date('2026-10-09T14:00:00')} />)
    expect(screen.getByRole('heading', { name: /shreyash/i })).toBeInTheDocument()
  })

  it('greets by time of day', () => {
    const at = (h: number) => new Date(`2026-10-09T${String(h).padStart(2, '0')}:00:00`)
    const { rerender } = render(<DashboardHero name="Sam" now={at(8)} />)
    expect(screen.getByRole('heading')).toHaveTextContent(/good morning/i)

    rerender(<DashboardHero name="Sam" now={at(14)} />)
    expect(screen.getByRole('heading')).toHaveTextContent(/good afternoon/i)

    rerender(<DashboardHero name="Sam" now={at(20)} />)
    expect(screen.getByRole('heading')).toHaveTextContent(/good evening/i)
  })

  it('falls back to a greeting without a name', () => {
    render(<DashboardHero name={null} now={new Date('2026-10-09T09:00:00')} />)
    expect(screen.getByRole('heading')).toHaveTextContent(/good morning/i)
  })

  it('treats the artwork as decorative, not content', () => {
    // It carries no information the heading does not, so announcing it would
    // only add noise for a screen reader.
    const { container } = render(<DashboardHero name="Sam" now={new Date()} />)
    const img = container.querySelector('img')
    expect(img).toHaveAttribute('alt', '')
    expect(img).toHaveAttribute('aria-hidden', 'true')
  })

  it('uses the first name only, so long display names do not crowd the hero', () => {
    render(<DashboardHero name="Shreyash Kumar Singh" now={new Date('2026-10-09T09:00:00')} />)
    expect(screen.getByRole('heading')).toHaveTextContent('Shreyash')
    expect(screen.getByRole('heading')).not.toHaveTextContent('Singh')
  })
})
