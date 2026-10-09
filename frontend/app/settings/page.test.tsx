import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock, pushMock, routerMock, linkIdentityMock } = vi.hoisted(() => {
  const pushMock = vi.fn()
  return {
    apiFetchMock: vi.fn(),
    pushMock,
    routerMock: { push: pushMock },
    linkIdentityMock: vi.fn(),
  }
})

vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))
vi.mock('next/navigation', () => ({ useRouter: () => routerMock }))
vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({ auth: { linkIdentity: linkIdentityMock } }),
}))

import SettingsPage from './page'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

const OUTLOOK = {
  id: 'acc-ms',
  provider: 'microsoft',
  email_address: 'jane@work.com',
  display_name: null,
  status: 'connected',
  is_primary: true,
}

const GMAIL = {
  id: 'acc-google',
  provider: 'google',
  email_address: 'jane@gmail.com',
  display_name: null,
  status: 'connected',
  is_primary: false,
}

/** Only the accounts list is requested on mount; later calls are the actions. */
function listOf(accounts: unknown[]) {
  apiFetchMock.mockResolvedValue(jsonResponse(accounts))
}

describe('SettingsPage', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
    linkIdentityMock.mockReset()
    // The real linkIdentity always resolves to { data, error }; a bare
    // undefined would only ever be a mock artefact.
    linkIdentityMock.mockResolvedValue({ data: null, error: null })
    window.location.hash = ''
  })

  it('lists every connected mailbox, not just the first', async () => {
    listOf([OUTLOOK, GMAIL])
    render(<SettingsPage />)

    expect(await screen.findByText('jane@work.com')).toBeInTheDocument()
    expect(screen.getByText('jane@gmail.com')).toBeInTheDocument()
  })

  it('names which provider each mailbox belongs to', async () => {
    listOf([OUTLOOK, GMAIL])
    render(<SettingsPage />)

    await screen.findByText('jane@work.com')
    expect(screen.getByText(/^Microsoft/)).toBeInTheDocument()
    expect(screen.getByText(/^Google/)).toBeInTheDocument()
  })

  it('marks which mailbox calendar and chat follow', async () => {
    listOf([OUTLOOK, GMAIL])
    render(<SettingsPage />)

    const outlookRow = (await screen.findByText('jane@work.com')).closest('li')
    const gmailRow = screen.getByText('jane@gmail.com').closest('li')

    // The badge has to be on the mailbox that actually is primary, not merely
    // present somewhere on the page.
    expect(within(outlookRow as HTMLElement).getByText('Primary')).toBeInTheDocument()
    expect(within(gmailRow as HTMLElement).queryByText('Primary')).not.toBeInTheDocument()
  })

  it('prompts a reconnect on the expired mailbox only', async () => {
    listOf([OUTLOOK, { ...GMAIL, status: 'needs_reauth' }])
    render(<SettingsPage />)

    await screen.findByText('jane@gmail.com')
    // One expired mailbox must not make the working one look broken - that is
    // exactly what the old single per-user flag did.
    const reconnects = screen.getAllByRole('button', { name: /reconnect/i })
    expect(reconnects).toHaveLength(1)
  })

  it('offers to connect a mailbox when there are none', async () => {
    listOf([])
    render(<SettingsPage />)

    expect(await screen.findByText(/no mailbox connected/i)).toBeInTheDocument()
  })

  it('sends you to sign in when the session has gone', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse({ detail: 'unauthorized' }, 401))
    render(<SettingsPage />)

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/login'))
  })

  it('reports a load failure instead of showing an empty list', async () => {
    apiFetchMock.mockRejectedValue(new Error('offline'))
    render(<SettingsPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i)
  })

  describe('adding a mailbox', () => {
    it('links a Google account with the parameters that return a refresh token', async () => {
      listOf([OUTLOOK])
      render(<SettingsPage />)
      await screen.findByText('jane@work.com')

      await userEvent.click(screen.getByRole('button', { name: /add a gmail/i }))

      expect(linkIdentityMock).toHaveBeenCalledTimes(1)
      const [{ provider, options }] = linkIdentityMock.mock.calls[0]
      expect(provider).toBe('google')
      // Google issues a refresh token only when both are sent. Without them the
      // mailbox dies after about an hour with no way to renew it.
      expect(options.queryParams).toMatchObject({
        access_type: 'offline',
        prompt: 'consent',
      })
      expect(options.scopes).toContain('gmail.readonly')
    })

    it('tells the callback which provider was just authorised', async () => {
      listOf([OUTLOOK])
      render(<SettingsPage />)
      await screen.findByText('jane@work.com')

      await userEvent.click(screen.getByRole('button', { name: /add a gmail/i }))

      // The session will hold several identities afterwards, and their order
      // says nothing about which one issued the new token.
      const [{ options }] = linkIdentityMock.mock.calls[0]
      expect(options.redirectTo).toContain('provider=google')
      expect(options.redirectTo).toContain('next=%2Fsettings')
    })

    it('links a second Outlook mailbox', async () => {
      listOf([GMAIL])
      render(<SettingsPage />)
      await screen.findByText('jane@gmail.com')

      await userEvent.click(screen.getByRole('button', { name: /add an outlook/i }))

      const [{ provider, options }] = linkIdentityMock.mock.calls[0]
      expect(provider).toBe('azure')
      expect(options.redirectTo).toContain('provider=azure')
    })

    it('surfaces a linking failure rather than looking like nothing happened', async () => {
      listOf([OUTLOOK])
      linkIdentityMock.mockResolvedValue({ error: { message: 'Manual linking is disabled' } })
      render(<SettingsPage />)
      await screen.findByText('jane@work.com')

      await userEvent.click(screen.getByRole('button', { name: /add a gmail/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent(/manual linking is disabled/i)
    })
  })

  describe('choosing the primary mailbox', () => {
    it('promotes a mailbox and reloads the list', async () => {
      apiFetchMock
        .mockResolvedValueOnce(jsonResponse([OUTLOOK, { ...GMAIL, provider: 'microsoft' }]))
        .mockResolvedValueOnce(new Response(null, { status: 204 }))
        .mockResolvedValueOnce(
          jsonResponse([
            { ...OUTLOOK, is_primary: false },
            { ...GMAIL, provider: 'microsoft', is_primary: true },
          ])
        )
      render(<SettingsPage />)
      await screen.findByText('jane@gmail.com')

      await userEvent.click(screen.getByRole('button', { name: /make primary/i }))

      await waitFor(() =>
        expect(apiFetchMock).toHaveBeenCalledWith(
          '/api/me/accounts/acc-google/primary',
          expect.objectContaining({ method: 'POST' })
        )
      )
    })

    it('does not offer to promote a Gmail mailbox while Outlook is connected', async () => {
      // Calendar and Teams chat are Graph-only; the API refuses this with a 409,
      // so offering the button would only produce an error.
      listOf([OUTLOOK, GMAIL])
      render(<SettingsPage />)

      await screen.findByText('jane@gmail.com')
      expect(screen.queryByRole('button', { name: /make primary/i })).not.toBeInTheDocument()
    })

    it('explains a refused promotion', async () => {
      apiFetchMock
        .mockResolvedValueOnce(jsonResponse([OUTLOOK, { ...GMAIL, provider: 'microsoft' }]))
        .mockResolvedValueOnce(
          jsonResponse({ detail: 'calendar and chat require a Microsoft mailbox' }, 409)
        )
      render(<SettingsPage />)
      await screen.findByText('jane@gmail.com')

      await userEvent.click(screen.getByRole('button', { name: /make primary/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent(/microsoft mailbox/i)
    })
  })

  describe('removing a mailbox', () => {
    it('asks before removing, because the mail goes with it', async () => {
      listOf([OUTLOOK, GMAIL])
      render(<SettingsPage />)
      await screen.findByText('jane@gmail.com')

      await userEvent.click(screen.getAllByRole('button', { name: /^disconnect$/i })[1])

      expect(await screen.findByText(/synced mail will be removed/i)).toBeInTheDocument()
      // Nothing has been sent yet - the first click only asks.
      expect(apiFetchMock).toHaveBeenCalledTimes(1)
    })

    it('removes the mailbox once confirmed', async () => {
      apiFetchMock
        .mockResolvedValueOnce(jsonResponse([OUTLOOK, GMAIL]))
        .mockResolvedValueOnce(new Response(null, { status: 204 }))
        .mockResolvedValueOnce(jsonResponse([OUTLOOK]))
      render(<SettingsPage />)
      await screen.findByText('jane@gmail.com')

      await userEvent.click(screen.getAllByRole('button', { name: /^disconnect$/i })[1])
      await userEvent.click(screen.getByRole('button', { name: /yes, disconnect/i }))

      await waitFor(() =>
        expect(apiFetchMock).toHaveBeenCalledWith(
          '/api/me/accounts/acc-google',
          expect.objectContaining({ method: 'DELETE' })
        )
      )
      await waitFor(() => expect(screen.queryByText('jane@gmail.com')).not.toBeInTheDocument())
    })

    it('can be backed out of', async () => {
      listOf([OUTLOOK, GMAIL])
      render(<SettingsPage />)
      await screen.findByText('jane@gmail.com')

      await userEvent.click(screen.getAllByRole('button', { name: /^disconnect$/i })[1])
      await userEvent.click(screen.getByRole('button', { name: /cancel/i }))

      expect(screen.queryByText(/synced mail will be removed/i)).not.toBeInTheDocument()
      expect(apiFetchMock).toHaveBeenCalledTimes(1)
    })

    it('reports a failed removal and keeps the mailbox listed', async () => {
      apiFetchMock
        .mockResolvedValueOnce(jsonResponse([OUTLOOK, GMAIL]))
        .mockResolvedValueOnce(jsonResponse({ detail: 'boom' }, 500))
      render(<SettingsPage />)
      await screen.findByText('jane@gmail.com')

      await userEvent.click(screen.getAllByRole('button', { name: /^disconnect$/i })[1])
      await userEvent.click(screen.getByRole('button', { name: /yes, disconnect/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent(/could not disconnect/i)
      expect(screen.getByText('jane@gmail.com')).toBeInTheDocument()
    })
  })
})
