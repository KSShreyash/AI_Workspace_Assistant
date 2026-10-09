import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getSessionMock = vi.fn()
const { pushMock, routerMock } = vi.hoisted(() => {
  const pushMock = vi.fn()
  return { pushMock, routerMock: { push: pushMock } }
})

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({ auth: { getSession: getSessionMock } }),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => routerMock,
}))

import CallbackPage from './page'

describe('CallbackPage', () => {
  beforeEach(() => {
    getSessionMock.mockReset()
    pushMock.mockReset()
    process.env.NEXT_PUBLIC_API_BASE_URL = 'https://api.example.com'
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  })

  it('forwards Graph tokens to the backend and redirects to the dashboard', async () => {
    getSessionMock.mockResolvedValue({
      data: {
        session: {
          access_token: 'supabase-jwt',
          provider_token: 'graph-access',
          provider_refresh_token: 'graph-refresh',
          user: {
            email: 'work@example.com',
            identities: [
              {
                id: 'ms-abc',
                provider: 'azure',
                identity_data: { email: 'work@example.com' },
              },
            ],
          },
        },
      },
      error: null,
    })

    render(<CallbackPage />)

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/dashboard'))
    expect(global.fetch).toHaveBeenCalledWith(
      'https://api.example.com/api/auth/accounts',
      expect.objectContaining({ method: 'POST' })
    )

    const [, requestInit] = (global.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0]
    const body = JSON.parse(requestInit.body as string)
    expect(body.scopes).toEqual([
      'User.Read',
      'Mail.Read',
      'Chat.Read',
      'Calendars.ReadWrite',
      'OnlineMeetings.ReadWrite',
    ])
  })

  it('shows an error message when Microsoft does not return Graph tokens', async () => {
    getSessionMock.mockResolvedValue({
      data: { session: { access_token: 'supabase-jwt' } },
      error: null,
    })

    render(<CallbackPage />)

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })

  it('shows an error instead of hanging when the graph-tokens request fails', async () => {
    getSessionMock.mockResolvedValue({
      data: {
        session: {
          access_token: 'supabase-jwt',
          provider_token: 'graph-access',
          provider_refresh_token: 'graph-refresh',
          user: {
            email: 'work@example.com',
            identities: [
              {
                id: 'ms-abc',
                provider: 'azure',
                identity_data: { email: 'work@example.com' },
              },
            ],
          },
        },
      },
      error: null,
    })
    global.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))

    render(<CallbackPage />)

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(pushMock).not.toHaveBeenCalled()
  })
})

describe('CallbackPage account identity', () => {
  beforeEach(() => {
    pushMock.mockReset()
    process.env.NEXT_PUBLIC_API_BASE_URL = 'https://api.example.com'
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  })

  function sessionWith(identity: unknown) {
    return {
      data: {
        session: {
          access_token: 'supabase-jwt',
          provider_token: 'access',
          provider_refresh_token: 'refresh',
          user: { email: 'me@example.com', identities: identity ? [identity] : [] },
        },
      },
      error: null,
    }
  }

  it('sends the provider identity so a reconnect is not mistaken for a new mailbox', async () => {
    getSessionMock.mockResolvedValue(
      sessionWith({ id: 'ms-abc', provider: 'azure', identity_data: { email: 'work@example.com' } })
    )

    render(<CallbackPage />)
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/dashboard'))

    const [, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    const body = JSON.parse(init.body as string)
    expect(body.provider_account_id).toBe('ms-abc')
    expect(body.email_address).toBe('work@example.com')
  })

  it("translates Supabase's azure into the product's microsoft", async () => {
    getSessionMock.mockResolvedValue(
      sessionWith({ id: 'ms-abc', provider: 'azure', identity_data: {} })
    )

    render(<CallbackPage />)
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/dashboard'))

    const [, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(JSON.parse(init.body as string).provider).toBe('microsoft')
  })

  it('sends Gmail scopes for a Google mailbox', async () => {
    getSessionMock.mockResolvedValue(
      sessionWith({ id: 'g-xyz', provider: 'google', identity_data: { email: 'me@gmail.com' } })
    )

    render(<CallbackPage />)
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/dashboard'))

    const [, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    const body = JSON.parse(init.body as string)
    expect(body.provider).toBe('google')
    expect(body.scopes.join(' ')).toMatch(/gmail\.readonly/)
  })

  it('reports a problem rather than posting an unidentifiable account', async () => {
    getSessionMock.mockResolvedValue(sessionWith(null))

    render(<CallbackPage />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(global.fetch).not.toHaveBeenCalled()
  })
})
