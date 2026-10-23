import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { TasksRemainingBar } from './TasksRemainingBar'

const fillOf = () => screen.getByRole('img').firstChild as HTMLElement

describe('TasksRemainingBar', () => {
  it('shows "No tasks yet" when total is 0', () => {
    render(<TasksRemainingBar open={0} total={0} />)
    expect(screen.getByText('No tasks yet')).toBeInTheDocument()
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', '0 open of 0 total tasks')
  })

  it('leads with the open count, because that is the number you act on', () => {
    render(<TasksRemainingBar open={2} total={10} />)
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('tasks open')).toBeInTheDocument()
  })

  it('says "task open" in the singular', () => {
    render(<TasksRemainingBar open={1} total={10} />)
    expect(screen.getByText('task open')).toBeInTheDocument()
  })

  it('fills with work DONE, not work remaining', () => {
    // Filling with open work makes the bar grow as you fall behind and empty as
    // you finish, which reads backwards for a progress bar.
    render(<TasksRemainingBar open={3} total={10} />)
    expect(fillOf().style.width).toBe('70%')
  })

  it('states the done count and percentage explicitly', () => {
    render(<TasksRemainingBar open={3} total={10} />)
    expect(screen.getByText('7 of 10 done')).toBeInTheDocument()
    expect(screen.getByText('70%')).toBeInTheDocument()
  })

  it('still describes itself to screen readers in open/total terms', () => {
    render(<TasksRemainingBar open={3} total={10} />)
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', '3 open of 10 total tasks')
  })

  it('reads calm when a third or less of the backlog is open', () => {
    render(<TasksRemainingBar open={3} total={10} />)
    expect(fillOf().style.backgroundColor).toBe('var(--color-accent)')
    expect(screen.getByText('on top of it')).toBeInTheDocument()
  })

  it('reads busy between a third and two thirds open', () => {
    render(<TasksRemainingBar open={5} total={10} />)
    expect(fillOf().style.backgroundColor).toBe('var(--color-warning)')
    expect(screen.getByText('building up')).toBeInTheDocument()
  })

  it('reads urgent above two thirds open', () => {
    render(<TasksRemainingBar open={8} total={10} />)
    expect(fillOf().style.backgroundColor).toBe('var(--color-danger)')
    expect(screen.getByText('piling up')).toBeInTheDocument()
  })

  it('does not go negative if open somehow exceeds total', () => {
    // Defensive: the two numbers come from separate queries, so a race could
    // briefly make open larger. A negative width would break the layout.
    render(<TasksRemainingBar open={12} total={10} />)
    expect(fillOf().style.width).toBe('0%')
  })
})
