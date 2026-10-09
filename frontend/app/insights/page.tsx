'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowDownLeft, ArrowUpRight, Inbox } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { Card } from '@/app/components/ui/Card'

type ContactCount = { contact: string; count: number }
type SenderCount = { sender: string; count: number }
type Pipeline = { extracted: number; skipped: number; failed: number; pending: number }

type Insights = {
  you_owe: ContactCount[]
  owed_to_you: ContactCount[]
  top_senders: SenderCount[]
  pipeline: Pipeline
}

type State = { state: 'loading' } | { state: 'error' } | { state: 'ready'; insights: Insights }

function RankedList({ rows, empty }: { rows: { label: string; count: number }[]; empty: string }) {
  if (rows.length === 0) {
    return <p className="mt-2 text-sm text-[var(--color-muted)]">{empty}</p>
  }
  const max = Math.max(...rows.map((r) => r.count))
  return (
    <ul className="mt-4 space-y-3">
      {rows.map((row) => (
        <li key={row.label} className="text-sm">
          <div className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate text-[var(--color-fg)]">{row.label}</span>
            <span
              className="shrink-0 text-[var(--color-muted)]"
              style={{ fontVariantNumeric: 'tabular-nums' }}
            >
              {row.count}
            </span>
          </div>
          {/* A bar as well as the number: relative weight is the point here. */}
          <div
            aria-hidden="true"
            className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--color-border)]"
          >
            <div
              className="h-full rounded-full bg-[var(--color-accent)]"
              style={{ width: `${(row.count / max) * 100}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}

export default function InsightsPage() {
  const router = useRouter()
  const [state, setState] = useState<State>({ state: 'loading' })

  const load = useCallback(async () => {
    try {
      const response = await apiFetch('/api/insights')
      if (response.status === 401) {
        router.push('/login')
        return
      }
      if (!response.ok) {
        setState({ state: 'error' })
        return
      }
      setState({ state: 'ready', insights: await response.json() })
    } catch {
      setState({ state: 'error' })
    }
  }, [router])

  useEffect(() => {
    void (async () => {
      await load()
    })()
  }, [load])

  if (state.state === 'loading') {
    return (
      <div className="p-8">
        <p className="text-[var(--color-muted)]">Loading…</p>
      </div>
    )
  }

  if (state.state === 'error') {
    return (
      <div className="p-8">
        <p role="alert" className="text-[var(--color-danger)]">
          Could not load your insights. Refresh to try again.
        </p>
      </div>
    )
  }

  const { insights } = state
  const { extracted, skipped, failed, pending } = insights.pipeline
  const pipelineTotal = extracted + skipped + failed + pending

  const nothingYet =
    insights.you_owe.length === 0 &&
    insights.owed_to_you.length === 0 &&
    insights.top_senders.length === 0 &&
    pipelineTotal === 0

  return (
    <div className="p-8">
      <h1 className="text-xl font-bold text-[var(--color-fg)]">Insights</h1>
      <p className="mt-1 text-sm text-[var(--color-muted)]">
        Where your attention is going, and whether anything is stuck.
      </p>

      {nothingYet ? (
        <p className="mt-6 text-sm text-[var(--color-muted)]">
          Nothing to show yet. Sync your mailbox and run an extraction to get started.
        </p>
      ) : (
        <>
          <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-fg)]">
                <ArrowUpRight size={16} aria-hidden="true" />
                You owe them
              </h2>
              <RankedList
                rows={insights.you_owe.map((r) => ({ label: r.contact, count: r.count }))}
                empty="You are not holding anything up."
              />
            </Card>
            <Card>
              <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-fg)]">
                <ArrowDownLeft size={16} aria-hidden="true" />
                They owe you
              </h2>
              <RankedList
                rows={insights.owed_to_you.map((r) => ({ label: r.contact, count: r.count }))}
                empty="Nobody owes you anything right now."
              />
            </Card>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-fg)]">
                <Inbox size={16} aria-hidden="true" />
                Busiest correspondents
              </h2>
              <RankedList
                rows={insights.top_senders.map((r) => ({ label: r.sender, count: r.count }))}
                empty="No mail synced yet."
              />
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-[var(--color-fg)]">Extraction pipeline</h2>
              <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                Of {pipelineTotal} synced emails
              </p>
              <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                <div className="flex items-baseline justify-between gap-2">
                  <dt className="text-[var(--color-muted)]">Extracted</dt>
                  <dd
                    className="font-medium text-[var(--color-fg)]"
                    style={{ fontVariantNumeric: 'tabular-nums' }}
                  >
                    {extracted}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-2">
                  <dt className="text-[var(--color-muted)]">Skipped</dt>
                  <dd
                    className="font-medium text-[var(--color-fg)]"
                    style={{ fontVariantNumeric: 'tabular-nums' }}
                  >
                    {skipped}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-2">
                  <dt className="text-[var(--color-muted)]">Failed</dt>
                  <dd
                    className={`font-medium ${
                      failed > 0 ? 'text-[var(--color-danger)]' : 'text-[var(--color-fg)]'
                    }`}
                    style={{ fontVariantNumeric: 'tabular-nums' }}
                  >
                    {failed}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-2">
                  <dt className="text-[var(--color-muted)]">Waiting</dt>
                  <dd
                    className="font-medium text-[var(--color-fg)]"
                    style={{ fontVariantNumeric: 'tabular-nums' }}
                  >
                    {pending}
                  </dd>
                </div>
              </dl>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
