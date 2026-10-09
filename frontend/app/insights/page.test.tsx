import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock, pushMock, routerMock } = vi.hoisted(() => {
  const pushMock = vi.fn()
  return { apiFetchMock: vi.fn(), pushMock, routerMock: { push: pushMock } }
})
vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))
vi.mock('next/navigation', () => ({ useRouter: () => routerMock }))

import InsightsPage from './page'

const BODY = {
  you_owe: [{ contact: 'Ann', count: 2 }],
  owed_to_you: [{ contact: 'Bob', count: 1 }],
  top_senders: [{ sender: 'loud@example.com', count: 3 }],
  pipeline: { extracted: 10, skipped: 4, failed: 1, pending: 2 },
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

describe('InsightsPage', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
  })

  it('separates what you owe from what you are owed', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(BODY))
    render(<InsightsPage />)
    expect(await screen.findByText('Ann')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
  })

  it('shows pipeline health so a stalled extraction is visible', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(BODY))
    render(<InsightsPage />)
    expect(await screen.findByText(/skipped/i)).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('reports an error rather than hanging', async () => {
    apiFetchMock.mockRejectedValue(new Error('down'))
    render(<InsightsPage />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })

  it('invites action when there is nothing to show yet', async () => {
    apiFetchMock.mockResolvedValue(
      jsonResponse({ you_owe: [], owed_to_you: [], top_senders: [], pipeline: { extracted: 0, skipped: 0, failed: 0, pending: 0 } })
    )
    render(<InsightsPage />)
    expect(await screen.findByText(/nothing to show yet/i)).toBeInTheDocument()
  })
})
