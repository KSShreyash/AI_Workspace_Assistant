import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock, pushMock, routerMock } = vi.hoisted(() => {
  const pushMock = vi.fn()
  return { apiFetchMock: vi.fn(), pushMock, routerMock: { push: pushMock } }
})

vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))
vi.mock('next/navigation', () => ({ useRouter: () => routerMock }))

import PlannerPage from './page'

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 })
}

const TODAY = new Date('2026-07-17T12:00:00Z')

const ITEMS = [
  { id: '1', text: 'Overdue task', direction: 'mine', status: 'open', due_date: '2026-07-10', contact: null, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
  { id: '2', text: 'Due this week', direction: 'theirs', status: 'open', due_date: '2026-07-19', contact: null, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
  { id: '3', text: 'No due date task', direction: 'mine', status: 'open', due_date: null, contact: null, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
]

describe('PlannerPage', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(TODAY)
    apiFetchMock.mockReset()
    pushMock.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows an inline error instead of failing silently when the fetch throws', async () => {
    apiFetchMock.mockRejectedValue(new Error('network error'))

    render(<PlannerPage />)

    await waitFor(() => expect(screen.getByText(/something went wrong/i)).toBeInTheDocument())
  })

  it('redirects to login on a 401 (no session)', async () => {
    apiFetchMock.mockResolvedValue(new Response(null, { status: 401 }))

    render(<PlannerPage />)

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/login'))
  })

  it('always fetches with include_done=true so the Completed tab has data available', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(ITEMS))

    render(<PlannerPage />)

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/api/action-items?include_done=true'))
  })

  it('buckets items into tabs by due date and shows each tab\'s count', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(ITEMS))

    render(<PlannerPage />)

    await waitFor(() => expect(screen.getByRole('button', { name: /^overdue/i })).toHaveTextContent('1'))
    expect(screen.getByRole('button', { name: /^this week/i })).toHaveTextContent('1')
    expect(screen.getByRole('button', { name: /^no date/i })).toHaveTextContent('1')
    expect(screen.getByRole('button', { name: /^today/i })).toHaveTextContent('0')

    // Default tab is "Today", which has no items yet.
    expect(screen.getByText('Nothing here.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^overdue/i }))
    expect(screen.getByText('Overdue task')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^this week/i }))
    expect(screen.getByText('Due this week')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^no date/i }))
    expect(screen.getByText('No due date task')).toBeInTheDocument()
  })

  it('marks an item done and refetches the list', async () => {
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return Promise.resolve(jsonResponse({ ...ITEMS[0], status: 'done' }))
      }
      return Promise.resolve(jsonResponse(ITEMS))
    })

    render(<PlannerPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: /^overdue/i })).toHaveTextContent('1'))
    fireEvent.click(screen.getByRole('button', { name: /^overdue/i }))
    expect(screen.getByText('Overdue task')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('checkbox', { name: /mark done/i }))

    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith('/api/action-items/1', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'done' }),
      })
    )
  })

  it('shows an inline error and leaves the item unchanged when the PATCH fails', async () => {
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return Promise.resolve(new Response(null, { status: 500 }))
      }
      return Promise.resolve(jsonResponse(ITEMS))
    })

    render(<PlannerPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: /^overdue/i })).toHaveTextContent('1'))
    fireEvent.click(screen.getByRole('button', { name: /^overdue/i }))
    expect(screen.getByText('Overdue task')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('checkbox', { name: /mark done/i }))

    await waitFor(() => expect(screen.getByText(/something went wrong/i)).toBeInTheDocument())
    expect(screen.getByText('Overdue task')).toBeInTheDocument()
  })

  it('shows the Next week tab, the Completed tab, and a contact avatar/name when present', async () => {
    const EXTENDED_ITEMS = [
      { id: '4', text: 'Later task', direction: 'mine', status: 'open', due_date: '2026-08-01', contact: null, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
      { id: '5', text: 'Done task', direction: 'mine', status: 'done', due_date: '2026-07-15', contact: null, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
      { id: '6', text: 'Contact task', direction: 'theirs', status: 'open', due_date: null, contact: { id: 'c1', display_name: 'Dana', email_address: 'dana@example.com' }, created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
    ]
    apiFetchMock.mockImplementation(() => Promise.resolve(jsonResponse(EXTENDED_ITEMS)))

    render(<PlannerPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: /^next week/i })).toHaveTextContent('1'))

    fireEvent.click(screen.getByRole('button', { name: /^next week/i }))
    expect(screen.getByText('Later task')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^completed/i }))
    expect(screen.getByText('Done task')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^no date/i }))
    expect(screen.getByText('Contact task')).toBeInTheDocument()
    expect(screen.getByText(/Dana/)).toBeInTheDocument()
    expect(screen.getByText('D')).toBeInTheDocument()
  })

  it('shows a Schedule control on open items with a contact and hides it once scheduled', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse([
      {
        id: '10', text: 'Call Gina', direction: 'mine', status: 'open', due_date: null,
        contact: { id: 'c1', display_name: 'Gina', email_address: 'gina@example.com' },
        scheduled_calendar_event_id: null, scheduled_start_time: null,
        created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z',
      },
      {
        id: '11', text: 'Already booked', direction: 'mine', status: 'open', due_date: null,
        contact: { id: 'c2', display_name: 'Bob', email_address: 'bob@example.com' },
        scheduled_calendar_event_id: 'evt-1', scheduled_start_time: '2026-07-22T14:00:00Z',
        created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z',
      },
    ]))

    render(<PlannerPage />)

    await waitFor(() => expect(screen.getByRole('button', { name: /^no date/i })).toHaveTextContent('2'))
    fireEvent.click(screen.getByRole('button', { name: /^no date/i }))

    expect(screen.getByText(/Call Gina/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^schedule$/i })).toBeInTheDocument()
    expect(screen.getByText(/scheduled:/i)).toBeInTheDocument()
  })
})

describe('PlannerPage failure handling and local dates', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('surfaces an error when toggling an item rejects', async () => {
    // toggleDone had no try/catch, so a rejected fetch threw past React as an
    // unhandled rejection while the UI showed nothing at all - even though a
    // toggleError state exists for exactly this.
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.reject(new Error('network down'))
      return Promise.resolve(
        jsonResponse([
          {
            id: 'i1', text: 'Send the deck', direction: 'mine', status: 'open',
            due_date: null, contact: null, scheduled_calendar_event_id: null,
            scheduled_start_time: null, created_at: '2026-07-17T10:00:00Z',
            updated_at: '2026-07-17T10:00:00Z',
          },
        ])
      )
    })

    render(<PlannerPage />)
    fireEvent.click(await screen.findByRole('button', { name: /no date/i }))
    const checkbox = await screen.findByRole('checkbox', { name: /mark done/i })
    fireEvent.click(checkbox)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })

  it('buckets by the local calendar date, not the UTC one', async () => {
    // "Today" was derived from Date.UTC, so for any user east of UTC a task due
    // on their today landed under Tomorrow (and west of UTC, the reverse).
    // 2026-07-17 22:00 UTC-5 is still the 17th locally, but the 18th in UTC.
    // Pick an instant where the local calendar date and the UTC one disagree,
    // whichever side of UTC this machine sits on. East of UTC, late-evening UTC
    // is already tomorrow locally; west of UTC, early-morning UTC is still
    // yesterday. Without this the test silently passes on machines whose dates
    // happen to line up, proving nothing.
    const offsetMinutes = -new Date('2026-07-17T12:00:00Z').getTimezoneOffset()
    if (offsetMinutes === 0) return // UTC machine: the bug cannot manifest
    const instant = offsetMinutes > 0 ? '2026-07-17T23:30:00Z' : '2026-07-17T00:30:00Z'

    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(instant))

    const localDate = new Date().toLocaleDateString('en-CA') // YYYY-MM-DD, local
    const utcDate = new Date().toISOString().slice(0, 10)
    expect(localDate).not.toBe(utcDate) // the divergence this test depends on

    apiFetchMock.mockImplementation(() =>
      Promise.resolve(
        jsonResponse([
          {
            id: 'i1', text: 'Due today locally', direction: 'mine', status: 'open',
            due_date: localDate, contact: null, scheduled_calendar_event_id: null,
            scheduled_start_time: null, created_at: '2026-07-17T10:00:00Z',
            updated_at: '2026-07-17T10:00:00Z',
          },
        ])
      )
    )

    render(<PlannerPage />)
    const todayTab = await screen.findByRole('button', { name: /^today/i })

    await waitFor(() => {
      expect(todayTab.textContent).toMatch(/1/)
    })
  })
})
