import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock, pushMock, routerMock } = vi.hoisted(() => {
  const pushMock = vi.fn()
  return { apiFetchMock: vi.fn(), pushMock, routerMock: { push: pushMock } }
})

vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))
vi.mock('next/navigation', () => ({ useRouter: () => routerMock }))

import EmailsPage from './page'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

type Mailbox = { id: string; email_address: string; provider: 'microsoft' | 'google' }

const WORK_MAILBOX: Mailbox = {
  id: 'acc-ms',
  email_address: 'jane@work.com',
  provider: 'microsoft',
}
const PERSONAL_MAILBOX: Mailbox = {
  id: 'acc-google',
  email_address: 'jane@gmail.com',
  provider: 'google',
}

function message(overrides: Record<string, unknown> = {}) {
  return {
    id: 'e1',
    provider_message_id: 'm1',
    subject: 'Q3 numbers',
    from_address: 'dana@example.com',
    from_name: 'Dana',
    received_at: '2026-07-01T09:30:00+00:00',
    preview: 'Could you review the draft before Thursday?',
    extracted: true,
    mailbox: WORK_MAILBOX,
    ...overrides,
  }
}

/** The page loads mailboxes for the filter, then the first page of mail. */
function respondWith(
  emails: unknown[],
  { has_more = false, mailboxes = [WORK_MAILBOX] }: { has_more?: boolean; mailboxes?: Mailbox[] } = {}
) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith('/api/me/accounts')) {
      return Promise.resolve(
        jsonResponse(
          mailboxes.map((m) => ({ ...m, status: 'connected', is_primary: false, display_name: null }))
        )
      )
    }
    return Promise.resolve(jsonResponse({ emails, has_more }))
  })
}

describe('EmailsPage', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    pushMock.mockReset()
  })

  it('lists synced mail', async () => {
    respondWith([message(), message({ id: 'e2', subject: 'Lunch?' })])
    render(<EmailsPage />)

    expect(await screen.findByText('Q3 numbers')).toBeInTheDocument()
    expect(screen.getByText('Lunch?')).toBeInTheDocument()
  })

  it('shows who each message is from and what it says', async () => {
    respondWith([message()])
    render(<EmailsPage />)

    expect(await screen.findByText('Dana')).toBeInTheDocument()
    expect(screen.getByText(/review the draft before thursday/i)).toBeInTheDocument()
  })

  it('names the mailbox each message arrived in', async () => {
    // Two inboxes in one list are indistinguishable otherwise, and which address
    // a message came to is the first thing needed to reply from the right one.
    respondWith([
      message(),
      message({ id: 'e2', subject: 'Lunch?', mailbox: PERSONAL_MAILBOX }),
    ])
    render(<EmailsPage />)

    const work = (await screen.findByText('Q3 numbers')).closest('li') as HTMLElement
    const personal = screen.getByText('Lunch?').closest('li') as HTMLElement

    expect(within(work).getByText('jane@work.com')).toBeInTheDocument()
    expect(within(personal).getByText('jane@gmail.com')).toBeInTheDocument()
  })

  it('offers a filter for each connected mailbox', async () => {
    respondWith([message()], { mailboxes: [WORK_MAILBOX, PERSONAL_MAILBOX] })
    render(<EmailsPage />)

    await screen.findByText('Q3 numbers')
    expect(screen.getByRole('button', { name: /all mailboxes/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'jane@work.com' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'jane@gmail.com' })).toBeInTheDocument()
  })

  it('requests only the chosen mailbox when filtered', async () => {
    respondWith([message()], { mailboxes: [WORK_MAILBOX, PERSONAL_MAILBOX] })
    render(<EmailsPage />)
    await screen.findByText('Q3 numbers')

    await userEvent.click(screen.getByRole('button', { name: 'jane@gmail.com' }))

    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        expect.stringContaining('account_id=acc-google')
      )
    )
  })

  it('hides the filter when only one mailbox is connected', async () => {
    // A filter with a single option is a control that cannot do anything.
    respondWith([message()])
    render(<EmailsPage />)

    await screen.findByText('Q3 numbers')
    expect(screen.queryByRole('button', { name: /all mailboxes/i })).not.toBeInTheDocument()
  })

  it('loads the next page on request', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path.startsWith('/api/me/accounts')) return Promise.resolve(jsonResponse([]))
      if (path.includes('offset=1')) {
        return Promise.resolve(
          jsonResponse({ emails: [message({ id: 'e2', subject: 'Older' })], has_more: false })
        )
      }
      return Promise.resolve(jsonResponse({ emails: [message()], has_more: true }))
    })
    render(<EmailsPage />)
    await screen.findByText('Q3 numbers')

    await userEvent.click(screen.getByRole('button', { name: /load more/i }))

    // Appended, not replaced - a paged list that drops what you already read is
    // worse than no paging.
    expect(await screen.findByText('Older')).toBeInTheDocument()
    expect(screen.getByText('Q3 numbers')).toBeInTheDocument()
  })

  it('stops offering more once the last page is in', async () => {
    respondWith([message()], { has_more: false })
    render(<EmailsPage />)

    await screen.findByText('Q3 numbers')
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  it('says when a message has not been read by the assistant yet', async () => {
    // Otherwise a message with no action items looks identical to one the
    // pipeline has not reached, and the user cannot tell which.
    respondWith([message({ extracted: false })])
    render(<EmailsPage />)

    expect(await screen.findByText(/not analysed yet/i)).toBeInTheDocument()
  })

  it('explains an empty inbox rather than showing a blank page', async () => {
    respondWith([])
    render(<EmailsPage />)

    expect(await screen.findByText(/no mail synced yet/i)).toBeInTheDocument()
  })

  it('sends you to sign in when the session has gone', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse({ detail: 'unauthorized' }, 401))
    render(<EmailsPage />)

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/login'))
  })

  it('reports a load failure', async () => {
    apiFetchMock.mockRejectedValue(new Error('offline'))
    render(<EmailsPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i)
  })

  it('renders a message with no subject without leaving a blank row', async () => {
    respondWith([message({ subject: null })])
    render(<EmailsPage />)

    expect(await screen.findByText('(no subject)')).toBeInTheDocument()
  })

  it('falls back to the address when a sender has no display name', async () => {
    respondWith([message({ from_name: null })])
    render(<EmailsPage />)

    expect(await screen.findByText('dana@example.com')).toBeInTheDocument()
  })

  it('renders a message with no received time without showing Invalid Date', async () => {
    // received_at is nullable, and `new Date(null)` formats as "Invalid Date".
    respondWith([message({ received_at: null })])
    render(<EmailsPage />)

    await screen.findByText('Q3 numbers')
    expect(screen.queryByText(/invalid date/i)).not.toBeInTheDocument()
  })
})
