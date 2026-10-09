'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Mail, Plus, Star } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { createClient } from '@/lib/supabase/client'
import {
  GOOGLE_OAUTH_QUERY_PARAMS,
  GOOGLE_RESOURCE_SCOPES,
  OAUTH_SCOPES,
} from '@/lib/graph-scopes'
import { Button } from '@/app/components/ui/Button'
import { Card } from '@/app/components/ui/Card'

type Account = {
  id: string
  provider: 'microsoft' | 'google'
  email_address: string
  display_name: string | null
  status: 'connected' | 'needs_reauth' | 'disconnected'
  is_primary: boolean
}

type Load =
  | { state: 'loading' }
  | { state: 'ready'; accounts: Account[] }
  | { state: 'error' }

const PROVIDER_LABEL: Record<Account['provider'], string> = {
  microsoft: 'Microsoft',
  google: 'Google',
}

const STATUS_LABEL: Record<Account['status'], string> = {
  connected: 'Connected',
  needs_reauth: 'Needs reconnecting',
  disconnected: 'Disconnected',
}

/**
 * Where the OAuth redirect lands, carrying which provider it authorised.
 *
 * After linking, the session holds several identities and their order says
 * nothing about which one issued the new token - so the callback cannot work it
 * out on its own, and guessing would file a Google token under the Microsoft
 * mailbox.
 */
function callbackUrl(provider: 'azure' | 'google') {
  const params = new URLSearchParams({ provider, next: '/settings' })
  return `${window.location.origin}/callback?${params.toString()}`
}

export default function SettingsPage() {
  const router = useRouter()
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    try {
      const response = await apiFetch('/api/me/accounts')

      if (response.status === 401) {
        router.push('/login')
        return
      }
      if (!response.ok) {
        setLoad({ state: 'error' })
        return
      }

      setLoad({ state: 'ready', accounts: await response.json() })
    } catch {
      setLoad({ state: 'error' })
    }
  }, [router])

  useEffect(() => {
    // Wrapped rather than calling reload() directly: the lint rule treats a bare
    // call as a synchronous setState in an effect, which it is not.
    void (async () => {
      await reload()
    })()
  }, [reload])

  async function addMailbox(provider: 'azure' | 'google') {
    setError(null)
    const supabase = createClient()
    const { error: linkError } = await supabase.auth.linkIdentity({
      provider,
      options: {
        scopes: provider === 'google' ? GOOGLE_RESOURCE_SCOPES.join(' ') : OAUTH_SCOPES,
        redirectTo: callbackUrl(provider),
        // Google issues a refresh token only when both of these are sent.
        ...(provider === 'google' ? { queryParams: GOOGLE_OAUTH_QUERY_PARAMS } : {}),
      },
    })

    if (linkError) {
      // Manual identity linking is off by default in a Supabase project, and
      // the failure is otherwise completely silent - the button just does
      // nothing and the user has no idea why.
      setError(linkError.message)
    }
  }

  async function makePrimary(accountId: string) {
    setError(null)
    setBusy(true)
    try {
      const response = await apiFetch(`/api/me/accounts/${accountId}/primary`, {
        method: 'POST',
      })
      if (response.status === 409) {
        const body = await response.json().catch(() => null)
        setError(body?.detail ?? 'That mailbox cannot be the primary one.')
        return
      }
      if (!response.ok) {
        setError('Could not change the primary mailbox. Please try again.')
        return
      }
      await reload()
    } catch {
      setError('Could not change the primary mailbox. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  async function disconnect(accountId: string) {
    setError(null)
    setBusy(true)
    try {
      const response = await apiFetch(`/api/me/accounts/${accountId}`, { method: 'DELETE' })
      if (!response.ok) {
        setError('Could not disconnect that mailbox. Please try again.')
        return
      }
      setConfirming(null)
      await reload()
    } catch {
      setError('Could not disconnect that mailbox. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const accounts = load.state === 'ready' ? load.accounts : []
  const hasMicrosoft = accounts.some((a) => a.provider === 'microsoft')

  return (
    <div className="p-8">
      <h1 className="text-xl font-bold text-[var(--color-fg)]">Settings</h1>
      <p className="mt-1 text-sm text-[var(--color-muted)]">
        Manage the mailboxes this assistant reads from. Each one syncs on its own, so a
        mailbox that needs reconnecting does not stop the others.
      </p>

      <h2 className="mt-8 text-sm font-semibold text-[var(--color-fg)]">Connected mailboxes</h2>

      {error && (
        <p role="alert" className="mt-2 text-sm text-[var(--color-danger)]">
          {error}
        </p>
      )}

      {load.state === 'loading' && (
        <p className="mt-2 text-sm text-[var(--color-muted)]">Loading…</p>
      )}

      {load.state === 'error' && (
        <p role="alert" className="mt-2 text-sm text-[var(--color-danger)]">
          Could not load your connected mailboxes. Refresh to try again.
        </p>
      )}

      {load.state === 'ready' && accounts.length === 0 && (
        <Card className="mt-2">
          <p className="text-sm text-[var(--color-fg)]">No mailbox connected yet.</p>
        </Card>
      )}

      {load.state === 'ready' && accounts.length > 0 && (
        <ul className="mt-2 space-y-3">
          {accounts.map((account) => {
            const expired = account.status === 'needs_reauth'
            // Calendar and chat are Graph-only, so the API refuses a Google
            // primary while an Outlook mailbox exists. Offering the button
            // would only produce an error the user cannot act on.
            const canPromote =
              !account.is_primary && (account.provider === 'microsoft' || !hasMicrosoft)

            return (
              <li key={account.id}>
                <Card
                  className={
                    expired
                      ? 'border-[var(--color-danger-border)] bg-[var(--color-danger-surface)]'
                      : undefined
                  }
                >
                  <div className="flex flex-wrap items-center gap-4">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)]/10 text-[var(--color-accent)]">
                      <Mail size={18} aria-hidden="true" />
                    </span>

                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 truncate text-sm font-medium text-[var(--color-fg)]">
                        {account.email_address}
                        {account.is_primary && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-[var(--color-accent)]/10 px-2 py-0.5 text-xs font-medium text-[var(--color-accent)]">
                            <Star size={11} aria-hidden="true" />
                            Primary
                          </span>
                        )}
                      </p>
                      <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                        {PROVIDER_LABEL[account.provider]} · {STATUS_LABEL[account.status]}
                      </p>
                    </div>

                    <div className="flex items-center gap-3">
                      {expired && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void addMailbox(account.provider === 'google' ? 'google' : 'azure')
                          }
                          className="text-sm font-medium text-[var(--color-accent)] hover:underline"
                        >
                          Reconnect
                        </button>
                      )}
                      {canPromote && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void makePrimary(account.id)}
                          className="text-sm font-medium text-[var(--color-accent)] hover:underline"
                        >
                          Make primary
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setConfirming(account.id)}
                        className="text-sm font-medium text-[var(--color-danger)] hover:underline"
                      >
                        Disconnect
                      </button>
                    </div>
                  </div>

                  {confirming === account.id && (
                    <div className="mt-4 border-t border-[var(--color-border)] pt-4">
                      <p className="text-sm text-[var(--color-fg)]">
                        Disconnect {account.email_address}? Its synced mail will be removed
                        along with it.
                      </p>
                      <div className="mt-3 flex gap-3">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void disconnect(account.id)}
                          className="text-sm font-medium text-[var(--color-danger)] hover:underline"
                        >
                          Yes, disconnect
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => setConfirming(null)}
                          className="text-sm font-medium text-[var(--color-muted)] hover:underline"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </Card>
              </li>
            )
          })}
        </ul>
      )}

      <h2 className="mt-8 text-sm font-semibold text-[var(--color-fg)]">Add a mailbox</h2>
      <p className="mt-1 text-sm text-[var(--color-muted)]">
        Connect as many as you like. Contacts and action items are unified across all of
        them; calendar and Teams chat follow the primary mailbox.
      </p>
      <div className="mt-3 flex flex-wrap gap-3">
        <Button onClick={() => void addMailbox('azure')} disabled={busy}>
          <Plus size={15} aria-hidden="true" className="mr-1.5 inline" />
          Add an Outlook mailbox
        </Button>
        <Button onClick={() => void addMailbox('google')} disabled={busy}>
          <Plus size={15} aria-hidden="true" className="mr-1.5 inline" />
          Add a Gmail mailbox
        </Button>
      </div>
    </div>
  )
}
