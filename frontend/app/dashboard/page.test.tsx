import { render, screen, waitFor, within } from '@testing-library/react'
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

  it('shows connection status, stats, and activity feed', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ graph_me: { mail: 'user@example.com' } }))
      }
      if (path === '/api/dashboard') {
        return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      throw new Error(`Unexpected path: ${path}`)
    })

    render(<DashboardPage />)

    await waitFor(() => expect(screen.getByText('Connected as user@example.com')).toBeInTheDocument())
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText(/Send the deck/)).toBeInTheDocument()
    expect(screen.getByText(/Helen/)).toBeInTheDocument()
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

  it('shows the tasks-remaining gauge with the real open/total counts', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ graph_me: { mail: 'user@example.com' } }))
      }
      if (path === '/api/dashboard') {
        return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      throw new Error(`Unexpected path: ${path}`)
    })

    render(<DashboardPage />)

    await waitFor(() => expect(screen.getByText('2 open of 3 total')).toBeInTheDocument())
  })

  it('shows an upcoming-tasks preview sorted by nearest due date', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ graph_me: { mail: 'user@example.com' } }))
      }
      if (path === '/api/dashboard') {
        return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(jsonResponse(ACTION_ITEMS_BODY))
      }
      throw new Error(`Unexpected path: ${path}`)
    })

    render(<DashboardPage />)

    await waitFor(() => expect(screen.getByText('Upcoming tasks')).toBeInTheDocument())
    const list = screen.getByText('Upcoming tasks').closest('div') as HTMLElement
    const items = within(list).getAllByText(/Send the proposal|Confirm the migration|Already done/)
    expect(items.map((el) => el.textContent)).toEqual(['Confirm the migration', 'Send the proposal'])
  })

  it('leaves the gauge section blank when the action-items fetch fails', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/me/graph-status') {
        return Promise.resolve(jsonResponse({ graph_me: { mail: 'user@example.com' } }))
      }
      if (path === '/api/dashboard') {
        return Promise.resolve(jsonResponse(DASHBOARD_BODY))
      }
      if (path === '/api/action-items?include_done=true') {
        return Promise.resolve(new Response(null, { status: 500 }))
      }
      throw new Error(`Unexpected path: ${path}`)
    })

    render(<DashboardPage />)

    await waitFor(() => expect(screen.getByText('Connected as user@example.com')).toBeInTheDocument())
    expect(screen.queryByText(/open of .* total/)).not.toBeInTheDocument()
  })
})

describe('DashboardPage trigger failures', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
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
