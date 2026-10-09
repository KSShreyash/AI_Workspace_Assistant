import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock, pushMock, routerMock } = vi.hoisted(() => {
  const pushMock = vi.fn()
  return { apiFetchMock: vi.fn(), pushMock, routerMock: { push: pushMock } }
})
vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))
vi.mock('next/navigation', () => ({ useRouter: () => routerMock }))

import CalendarPage from './page'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

const EVENTS = [
  {
    id: 'e1', subject: 'Project sync', organizer: 'lead@example.com',
    start_time: '2026-10-12T09:00:00Z', end_time: '2026-10-12T09:30:00Z',
    is_online_meeting: true, join_url: 'https://teams.example/join',
  },
  {
    id: 'e2', subject: 'Design review', organizer: null,
    start_time: '2026-10-13T14:00:00Z', end_time: null,
    is_online_meeting: false, join_url: null,
  },
]

describe('CalendarPage', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
  })

  it('lists upcoming events', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(EVENTS))
    render(<CalendarPage />)
    expect(await screen.findByText('Project sync')).toBeInTheDocument()
    expect(screen.getByText('Design review')).toBeInTheDocument()
  })

  it('offers a join link only for online meetings', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(EVENTS))
    render(<CalendarPage />)
    const links = await screen.findAllByRole('link', { name: /join/i })
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', 'https://teams.example/join')
  })

  it('says so when the calendar is empty', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse([]))
    render(<CalendarPage />)
    expect(await screen.findByText(/nothing scheduled/i)).toBeInTheDocument()
  })

  it('reports an error rather than hanging', async () => {
    apiFetchMock.mockRejectedValue(new Error('down'))
    render(<CalendarPage />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
