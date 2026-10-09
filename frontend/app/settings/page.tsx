'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Mail } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { Card } from '@/app/components/ui/Card'

type ConnectionState =
  | { state: 'loading' }
  | { state: 'connected'; email: string | null }
  | { state: 'needs_reauth' }
  | { state: 'not_connected' }
  | { state: 'error' }

export default function SettingsPage() {
  const router = useRouter()
  const [connection, setConnection] = useState<ConnectionState>({ state: 'loading' })

  const load = useCallback(async () => {
    try {
      const response = await apiFetch('/api/me/graph-status')

      if (response.status === 401) {
        router.push('/login')
        return
      }
      if (response.status === 409) {
        setConnection({ state: 'needs_reauth' })
        return
      }
      if (response.status === 404) {
        setConnection({ state: 'not_connected' })
        return
      }
      if (!response.ok) {
        setConnection({ state: 'error' })
        return
      }

      const body = await response.json()
      setConnection({
        state: 'connected',
        email: body.graph_me?.mail ?? body.graph_me?.userPrincipalName ?? null,
      })
    } catch {
      setConnection({ state: 'error' })
    }
  }, [router])

  useEffect(() => {
    // Wrapped rather than calling load() directly: the lint rule treats a bare
    // call as a synchronous setState in an effect, which it is not - load is
    // async and every setState in it happens after an await.
    void (async () => {
      await load()
    })()
  }, [load])

  return (
    <div className="p-8">
      <h1 className="text-xl font-bold text-[var(--color-fg)]">Settings</h1>
      <p className="mt-1 text-sm text-[var(--color-muted)]">
        Manage the mailboxes this assistant reads from.
      </p>

      <h2 className="mt-8 text-sm font-semibold text-[var(--color-fg)]">Connected mailboxes</h2>

      {connection.state === 'loading' && (
        <p className="mt-2 text-sm text-[var(--color-muted)]">Loading…</p>
      )}

      {connection.state === 'error' && (
        <p role="alert" className="mt-2 text-sm text-[var(--color-danger)]">
          Could not load your connected mailboxes. Refresh to try again.
        </p>
      )}

      {connection.state === 'not_connected' && (
        <Card className="mt-2">
          <p className="text-sm text-[var(--color-fg)]">No mailbox connected yet.</p>
          <Link
            href="/login"
            className="mt-2 inline-block text-sm font-medium text-[var(--color-accent)] hover:underline"
          >
            Connect a Microsoft account
          </Link>
        </Card>
      )}

      {connection.state === 'needs_reauth' && (
        <Card className="mt-2 border-[var(--color-danger-border)] bg-[var(--color-danger-surface)]">
          <p className="text-sm text-[var(--color-fg)]">
            Your Microsoft connection expired, so syncing has stopped.
          </p>
          <Link
            href="/login"
            className="mt-2 inline-block text-sm font-medium text-[var(--color-accent)] hover:underline"
          >
            Reconnect your Microsoft account
          </Link>
        </Card>
      )}

      {connection.state === 'connected' && (
        <Card className="mt-2 flex items-center gap-4">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)]/10 text-[var(--color-accent)]">
            <Mail size={18} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-[var(--color-fg)]">
              {connection.email ?? 'Microsoft account'}
            </p>
            <p className="mt-0.5 text-xs text-[var(--color-muted)]">Microsoft · Connected</p>
          </div>
        </Card>
      )}
    </div>
  )
}
