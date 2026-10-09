import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock, pushMock, routerMock } = vi.hoisted(() => {
  const pushMock = vi.fn()
  return { apiFetchMock: vi.fn(), pushMock, routerMock: { push: pushMock } }
})

vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))
vi.mock('next/navigation', () => ({ useRouter: () => routerMock }))

import SettingsPage from './page'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

describe('SettingsPage', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
  })

  it('shows the connected mailbox', async () => {
    apiFetchMock.mockResolvedValue(
      jsonResponse({ connected: true, graph_me: { mail: 'jane@example.com' } })
    )
    render(<SettingsPage />)

    expect(await screen.findByText('jane@example.com')).toBeInTheDocument()
    expect(screen.getByText(/microsoft · connected/i)).toBeInTheDocument()
  })

  it('prompts a reconnect when the connection has expired', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse({ detail: 'needs_reauth' }, 409))
    render(<SettingsPage />)

    expect(await screen.findByRole('link', { name: /reconnect/i })).toHaveAttribute('href', '/login')
  })

  it('sends the user to login when unauthenticated', async () => {
    apiFetchMock.mockResolvedValue(new Response(null, { status: 401 }))
    render(<SettingsPage />)

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/login'))
  })

  it('reports an error instead of hanging when the request fails', async () => {
    apiFetchMock.mockRejectedValue(new Error('network down'))
    render(<SettingsPage />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
