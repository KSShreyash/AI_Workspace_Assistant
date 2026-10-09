import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock, pushMock, routerMock } = vi.hoisted(() => {
  const pushMock = vi.fn()
  return { apiFetchMock: vi.fn(), pushMock, routerMock: { push: pushMock } }
})

vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))
vi.mock('next/navigation', () => ({ useRouter: () => routerMock }))

import DashboardPage from './page'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

const DASHBOARD_BODY = {
  contact_count: 3,
  open_action_item_count: 2,
  activity: [
    { type: 'action_item_created', id: 'a1', timestamp: '2026-07-17T10:00:00Z', text: 'Send the deck', direction: 'mine' },
    { type: 'contact_updated', id: 'c1', timestamp: '2026-07-17T09:00:00Z', display_name: 'Helen', email_address: 'helen@example.com' },
  ],
}

const ACTION_ITEMS_BODY = [
  { id: 'i1', status: 'open', text: 'Send the proposal', due_date: '2026-07-20', contact: null },
  { id: 'i2', status: 'open', text: 'Confirm the migration', due_date: '2026-07-18', contact: null },
  { id: 'i3', status: 'done', text: 'Already done', due_date: '2026-07-01', contact: null },
]

describe('DashboardPage', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
  })

  it('shows an error instead of hanging on Loading when the fetch throws', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.reject(new Error('network error'))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      return Promise.resolve(jsonResponse(DASHBOARD_BODY))
    })

    render(<DashboardPage />)

    await waitFor(() => expect(screen.getByText(/something went wrong/i)).toBeInTheDocument())
  })

  it('redirects to login on a 401 (no session)', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(new Response(null, { status: 401 }))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      return Promise.resolve(jsonResponse(DASHBOARD_BODY))
    })

    render(<DashboardPage />)

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/login'))
  })

  it('renders connection status and the activity feed together', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ graph_me: { mail: 'user@example.com' } }))
      }
      if (path === '/api/dashboard') return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      return Promise.resolve(jsonResponse({}))
    })

    render(<DashboardPage />)
    expect(await screen.findByText(/connected as user@example.com/i)).toBeInTheDocument()
    expect(screen.getByText(/send the deck/i)).toBeInTheDocument()
    expect(screen.getByText(/updated contact: helen/i)).toBeInTheDocument()
  })

  it('shows a reconnect prompt on 409 needs_reauth', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(new Response(null, { status: 409 }))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      return Promise.resolve(jsonResponse(DASHBOARD_BODY))
    })

    render(<DashboardPage />)

    await waitFor(() =>
      expect(screen.getByText(/reconnect your microsoft account/i)).toBeInTheDocument()
    )
  })

  it('triggers a sync and refetches the dashboard on success', async () => {
    const user = userEvent.setup()
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ graph_me: { mail: 'user@example.com' } }))
      }
      if (path === '/api/dashboard') {
        return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      if (path === '/api/sync/run/me' && init?.method === 'POST') {
        return Promise.resolve(jsonResponse({ status: 'ok' }))
      }
      throw new Error(`Unexpected path: ${path}`)
    })

    render(<DashboardPage />)
    await waitFor(() => expect(screen.getByText('Connected as user@example.com')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /sync now/i }))

    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith('/api/sync/run/me', { method: 'POST' })
    )
  })

  it('shows an inline error and re-enables the button when sync fails', async () => {
    const user = userEvent.setup()
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ graph_me: { mail: 'user@example.com' } }))
      }
      if (path === '/api/dashboard') {
        return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      if (path === '/api/sync/run/me' && init?.method === 'POST') {
        return Promise.resolve(new Response(null, { status: 500 }))
      }
      throw new Error(`Unexpected path: ${path}`)
    })

    render(<DashboardPage />)
    await waitFor(() => expect(screen.getByText('Connected as user@example.com')).toBeInTheDocument())

    const button = screen.getByRole('button', { name: /sync now/i })
    await user.click(button)

    await waitFor(() => expect(screen.getByText(/something went wrong/i)).toBeInTheDocument())
    expect(button).not.toBeDisabled()
  })

  function mockLoadedDashboard(onTrigger: (path: string) => Promise<Response>) {
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === 'POST') return onTrigger(path)
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ connected: true, graph_me: { mail: 'me@example.com' } }))
      }
      if (path === '/api/dashboard') return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      return Promise.resolve(jsonResponse({}))
    })
  }

  it('re-enables the buttons when a sync request rejects', async () => {
    // A rejected fetch left `pending` set forever, so both buttons stayed
    // disabled until a full page reload. The slowest call in the app is the one
    // most likely to hit this: /api/sync/run/me does a full Graph sync plus
    // extraction inline on a free-tier dyno.
    mockLoadedDashboard(() => Promise.reject(new Error('network down')))
    render(<DashboardPage />)

    const syncButton = await screen.findByRole('button', { name: /sync now/i })
    await userEvent.click(syncButton)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /sync now/i })).not.toBeDisabled()
    })
  })

  it('tells the user when a trigger fails instead of failing silently', async () => {
    mockLoadedDashboard(() => Promise.reject(new Error('network down')))
    render(<DashboardPage />)

    const syncButton = await screen.findByRole('button', { name: /sync now/i })
    await userEvent.click(syncButton)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})

const STATS_BODY = {
  emails: { this_week: 128, previous_week: 114 },
  tasks: { this_week: 36, previous_week: 29 },
  meetings: { this_week: 8, previous_week: 5 },
  categories: { work: 18, meeting: 9, follow_up: 7, other: 2 },
  important_people: 24,
  daily: [
    { date: '2026-10-01', emails: 12, tasks: 4 },
    { date: '2026-10-02', emails: 18, tasks: 6 },
    { date: '2026-10-03', emails: 9, tasks: 2 },
    { date: '2026-10-04', emails: 22, tasks: 7 },
    { date: '2026-10-05', emails: 14, tasks: 5 },
    { date: '2026-10-06', emails: 25, tasks: 9 },
    { date: '2026-10-07', emails: 20, tasks: 6 },
  ],
  upcoming: [
    { id: 'u1', text: 'Review phase 2 requirements', due_date: '2026-10-09', category: 'work', contact: 'Priya Sharma' },
    { id: 'u2', text: 'Prepare presentation slides', due_date: '2026-10-10', category: 'work', contact: 'Team Lead' },
  ],
}

describe('DashboardPage stats', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ connected: true, graph_me: { mail: 'me@example.com' } }))
      }
      if (path === '/api/dashboard') return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      if (path === '/api/dashboard/stats') return Promise.resolve(jsonResponse(STATS_BODY))
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      return Promise.resolve(jsonResponse({}))
    })
  })

  it('shows the headline counts from the aggregate endpoint', async () => {
    render(<DashboardPage />)
    expect(await screen.findByText('128')).toBeInTheDocument()
    expect(screen.getByText('24')).toBeInTheDocument()
    // 36 appears twice on purpose: the Tasks tile and the donut total, which
    // are the same number computed from different fields.
    expect(screen.getAllByText('36')).toHaveLength(2)
  })

  it('shows the direction of travel against last week', async () => {
    // A bare count says nothing about whether the week is busier than usual.
    render(<DashboardPage />)
    expect(await screen.findByText(/12%/)).toBeInTheDocument()
  })

  it('stops downloading every action item just to count them', async () => {
    // The counts are computed in SQL now, so the page has no reason to pull the
    // whole collection down and tally it in the browser.
    render(<DashboardPage />)
    await screen.findByText('128')
    const paths = apiFetchMock.mock.calls.map((c) => c[0])
    expect(paths).not.toContain('/api/action-items?include_done=true')
  })

  it('renders the category breakdown', async () => {
    render(<DashboardPage />)
    expect(await screen.findByText(/follow-ups/i)).toBeInTheDocument()
  })
})
