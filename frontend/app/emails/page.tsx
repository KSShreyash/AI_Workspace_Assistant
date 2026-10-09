'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Mail, Sparkles } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { Card } from '@/app/components/ui/Card'

const PAGE_SIZE = 25

type Mailbox = {
  id: string
  email_address: string
  provider: 'microsoft' | 'google'
}

type Message = {
  id: string
  provider_message_id: string
  subject: string | null
  from_address: string | null
  from_name: string | null
  received_at: string | null
  preview: string
  extracted: boolean
  mailbox: Mailbox
}

type State =
  | { state: 'loading' }
  | { state: 'error' }
  | { state: 'ready'; messages: Message[]; hasMore: boolean }

/** received_at is nullable, and `new Date(null)` renders as "Invalid Date". */
function receivedLabel(received: string | null): string {
  if (!received) return 'No date'
  return new Date(received).toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export default function EmailsPage() {
  const router = useRouter()
  const [state, setState] = useState<State>({ state: 'loading' })
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([])
  const [filter, setFilter] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  const fetchPage = useCallback(
    async (offset: number, accountId: string | null) => {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(offset),
      })
      if (accountId) params.set('account_id', accountId)
      return apiFetch(`/api/emails?${params.toString()}`)
    },
    []
  )

  const load = useCallback(
    async (accountId: string | null) => {
      try {
        const accountsResponse = await apiFetch('/api/me/accounts')
        if (accountsResponse.status === 401) {
          router.push('/login')
          return
        }
        if (accountsResponse.ok) {
          setMailboxes(await accountsResponse.json())
        }

        const response = await fetchPage(0, accountId)
        if (response.status === 401) {
          router.push('/login')
          return
        }
        if (!response.ok) {
          setState({ state: 'error' })
          return
        }

        const body = await response.json()
        setState({ state: 'ready', messages: body.emails, hasMore: body.has_more })
      } catch {
        setState({ state: 'error' })
      }
    },
    [fetchPage, router]
  )

  useEffect(() => {
    // Wrapped rather than calling load() directly: the lint rule treats a bare
    // call as a synchronous setState in an effect, which it is not.
    void (async () => {
      await load(filter)
    })()
  }, [load, filter])

  async function loadMore() {
    if (state.state !== 'ready') return
    setLoadingMore(true)
    try {
      const response = await fetchPage(state.messages.length, filter)
      if (!response.ok) {
        setState({ state: 'error' })
        return
      }
      const body = await response.json()
      // Appended, not replaced: a paged list that discards what has already
      // been read is worse than no paging at all.
      setState({
        state: 'ready',
        messages: [...state.messages, ...body.emails],
        hasMore: body.has_more,
      })
    } catch {
      setState({ state: 'error' })
    } finally {
      setLoadingMore(false)
    }
  }

  function filterButton(label: string, value: string | null) {
    const active = filter === value
    return (
      <button
        key={value ?? 'all'}
        type="button"
        aria-pressed={active}
        onClick={() => setFilter(value)}
        className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
          active
            ? 'bg-[var(--color-accent)] text-[var(--color-accent-fg)]'
            : 'bg-[var(--color-surface)] text-[var(--color-muted)] hover:text-[var(--color-fg)]'
        }`}
      >
        {label}
      </button>
    )
  }

  return (
    <div className="p-8">
      <h1 className="text-xl font-bold text-[var(--color-fg)]">Emails</h1>
      <p className="mt-1 text-sm text-[var(--color-muted)]">
        Everything synced from your connected mailboxes, newest first.
      </p>

      {/* A filter with one option is a control that cannot do anything. */}
      {mailboxes.length > 1 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {filterButton('All mailboxes', null)}
          {mailboxes.map((mailbox) => filterButton(mailbox.email_address, mailbox.id))}
        </div>
      )}

      {state.state === 'loading' && (
        <p className="mt-6 text-sm text-[var(--color-muted)]">Loading…</p>
      )}

      {state.state === 'error' && (
        <p role="alert" className="mt-6 text-sm text-[var(--color-danger)]">
          Could not load your mail. Refresh to try again.
        </p>
      )}

      {state.state === 'ready' && state.messages.length === 0 && (
        <Card className="mt-6">
          <p className="text-sm text-[var(--color-fg)]">No mail synced yet.</p>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            Connect a mailbox in Settings, or wait for the next sync to finish.
          </p>
        </Card>
      )}

      {state.state === 'ready' && state.messages.length > 0 && (
        <>
          <ul className="mt-6 space-y-3">
            {state.messages.map((message) => (
              <li key={message.id}>
                <Card>
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                    <p className="text-sm font-semibold text-[var(--color-fg)]">
                      {message.subject ?? '(no subject)'}
                    </p>
                    <p className="text-xs text-[var(--color-muted)]">
                      {receivedLabel(message.received_at)}
                    </p>
                  </div>

                  <p className="mt-1 text-xs text-[var(--color-muted)]">
                    {message.from_name ?? message.from_address ?? 'Unknown sender'}
                  </p>

                  {message.preview && (
                    <p className="mt-2 line-clamp-2 text-sm text-[var(--color-fg)]">
                      {message.preview}
                    </p>
                  )}

                  <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-[var(--color-muted)]">
                    <span className="inline-flex items-center gap-1.5">
                      <Mail size={12} aria-hidden="true" />
                      {message.mailbox.email_address}
                    </span>
                    {/* Without this a message that simply had no action items
                        looks identical to one the pipeline has not reached. */}
                    {!message.extracted && (
                      <span className="inline-flex items-center gap-1.5">
                        <Sparkles size={12} aria-hidden="true" />
                        Not analysed yet
                      </span>
                    )}
                  </div>
                </Card>
              </li>
            ))}
          </ul>

          {state.hasMore && (
            <button
              type="button"
              onClick={() => void loadMore()}
              disabled={loadingMore}
              className="mt-6 text-sm font-medium text-[var(--color-accent)] hover:underline disabled:opacity-60"
            >
              {loadingMore ? 'Loading…' : 'Load more'}
            </button>
          )}
        </>
      )}
    </div>
  )
}
