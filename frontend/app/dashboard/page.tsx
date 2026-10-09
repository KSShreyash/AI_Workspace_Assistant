'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { CalendarDays, ListChecks, ListPlus, Mail, TrendingDown, TrendingUp, UserRound, Users } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { parseDueDate } from '@/lib/parseDueDate'
import { Button } from '@/app/components/ui/Button'
import { Card } from '@/app/components/ui/Card'
import { TasksRemainingBar } from '@/app/components/TasksRemainingBar'
import { ActivityChart, type ActivityPoint } from '@/app/components/charts/ActivityChart'
import { CategoryDonut, type Categories } from '@/app/components/charts/CategoryDonut'

type GraphStatus =
  | { state: 'loading' }
  | { state: 'connected'; email: string }
  | { state: 'needs_reauth' }
  | { state: 'error' }

type ActivityEntry =
  | { type: 'contact_updated'; id: string; timestamp: string; display_name: string | null; email_address: string | null }
  | { type: 'action_item_created'; id: string; timestamp: string; text: string; direction: 'mine' | 'theirs' }

type DashboardData = {
  contact_count: number
  open_action_item_count: number
  activity: ActivityEntry[]
}

type WeekCounts = { this_week: number; previous_week: number }

type UpcomingTask = {
  id: string
  text: string
  due_date: string
  category: string
  contact: string | null
}

type Stats = {
  emails: WeekCounts
  tasks: WeekCounts
  meetings: WeekCounts
  categories: Categories
  important_people: number
  daily: ActivityPoint[]
  upcoming: UpcomingTask[]
}

/**
 * Accept the payload only if it carries what the tiles and charts read.
 *
 * Without this, a partial or unexpected response reached the render and threw
 * on the first missing field, taking the whole dashboard down - including the
 * connection status and activity feed, which do not depend on it at all.
 */
function isStats(value: unknown): value is Stats {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  const week = (w: unknown) =>
    typeof w === 'object' && w !== null && typeof (w as WeekCounts).this_week === 'number'
  return (
    week(v.emails) &&
    week(v.tasks) &&
    week(v.meetings) &&
    typeof v.important_people === 'number' &&
    typeof v.categories === 'object' &&
    v.categories !== null &&
    Array.isArray(v.daily) &&
    Array.isArray(v.upcoming)
  )
}

/** Percentage change, or null when last week had nothing to compare against. */
function weekDelta(counts: WeekCounts): number | null {
  if (counts.previous_week === 0) return null
  return Math.round(((counts.this_week - counts.previous_week) / counts.previous_week) * 100)
}

function StatTile({
  icon: Icon,
  label,
  value,
  delta,
}: {
  icon: typeof Mail
  label: string
  value: number
  delta?: number | null
}) {
  return (
    <Card className="flex items-center gap-4">
      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)]/10 text-[var(--color-accent)]">
        <Icon size={20} aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <p
            className="text-2xl font-bold text-[var(--color-fg)]"
            style={{ fontVariantNumeric: 'tabular-nums' }}
          >
            {value}
          </p>
          {delta !== null && delta !== undefined && (
            <span
              className={`flex items-center gap-0.5 text-xs font-medium ${
                delta >= 0 ? 'text-[var(--color-accent)]' : 'text-[var(--color-danger)]'
              }`}
            >
              {delta >= 0 ? (
                <TrendingUp size={12} aria-hidden="true" />
              ) : (
                <TrendingDown size={12} aria-hidden="true" />
              )}
              {Math.abs(delta)}%
            </span>
          )}
        </div>
        <p className="mt-1 truncate text-sm text-[var(--color-muted)]">{label}</p>
      </div>
    </Card>
  )
}

export default function DashboardPage() {
  const router = useRouter()
  const [status, setStatus] = useState<GraphStatus>({ state: 'loading' })
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [triggerError, setTriggerError] = useState<string | null>(null)
  const [pending, setPending] = useState<'sync' | 'extract' | null>(null)

  const loadStatus = useCallback(async () => {
    try {
      const response = await apiFetch('/api/me/graph-status')

      if (response.status === 401) {
        router.push('/login')
        return
      }
      if (response.status === 409) {
        setStatus({ state: 'needs_reauth' })
        return
      }
      if (!response.ok) {
        setStatus({ state: 'error' })
        return
      }

      const body = await response.json()
      setStatus({
        state: 'connected',
        email: body.graph_me?.mail ?? body.graph_me?.userPrincipalName,
      })
    } catch {
      setStatus({ state: 'error' })
    }
  }, [router])

  const loadDashboard = useCallback(async () => {
    try {
      const response = await apiFetch('/api/dashboard')
      if (!response.ok) return
      setDashboard(await response.json())
    } catch {
      // Non-fatal: the connection status (loadStatus) already surfaces a
      // page-level error if the backend is unreachable. A stats-fetch
      // failure alone just leaves the stats section blank.
    }
  }, [])

  const [stats, setStats] = useState<Stats | null>(null)

  const loadStats = useCallback(async () => {
    try {
      const response = await apiFetch('/api/dashboard/stats')
      if (!response.ok) return
      const body = await response.json()
      if (isStats(body)) setStats(body)
    } catch {
      // Non-fatal: the connection status already surfaces a page-level error if
      // the backend is unreachable. A stats failure alone leaves the tiles and
      // charts out rather than blocking the page.
    }
  }, [])

  useEffect(() => {
    // Wrapped rather than calling these directly: the lint rule treats a bare
    // call as a synchronous setState in an effect, which it is not - each is
    // async and every setState happens after an await.
    void (async () => {
      await Promise.all([loadStatus(), loadDashboard(), loadStats()])
    })()
  }, [loadStatus, loadDashboard, loadStats])

  async function runTrigger(kind: 'sync' | 'extract') {
    setPending(kind)
    setTriggerError(null)
    const path = kind === 'sync' ? '/api/sync/run/me' : '/api/extraction/run/me'
    try {
      const response = await apiFetch(path, { method: 'POST' })
      if (!response.ok) {
        setTriggerError('Something went wrong running that. Please try again.')
        return
      }
      await loadDashboard()
    } catch {
      // A rejected fetch previously skipped setPending(null) entirely, leaving
      // both buttons disabled until a full page reload. Sync is the slowest
      // call in the app - a full Graph sync plus extraction, inline - so it is
      // the one most likely to time out and trigger this.
      setTriggerError('Something went wrong running that. Please try again.')
    } finally {
      setPending(null)
    }
  }

  if (status.state === 'loading') {
    return (
      <div className="p-8">
        <p className="text-[var(--color-muted)]">Loading…</p>
      </div>
    )
  }

  if (status.state === 'needs_reauth') {
    return (
      <div className="p-8">
        <p className="text-[var(--color-fg)]">
          Your Microsoft connection expired.{' '}
          <a href="/login" className="text-[var(--color-accent)] hover:underline">
            Reconnect your Microsoft account
          </a>
          .
        </p>
      </div>
    )
  }

  if (status.state === 'error') {
    return (
      <div className="p-8">
        <p role="alert" className="text-[var(--color-danger)]">Something went wrong loading your account.</p>
      </div>
    )
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--color-fg)]">Dashboard</h1>
          <p className="mt-1 text-sm text-[var(--color-muted)]">Connected as {status.email}</p>
        </div>

        <div className="flex gap-3">
          <Button onClick={() => runTrigger('sync')} disabled={pending !== null}>
            Sync now
          </Button>
          <Button variant="secondary" onClick={() => runTrigger('extract')} disabled={pending !== null}>
            Extract now
          </Button>
        </div>
      </div>

      {triggerError && <p role="alert" className="mt-3 text-sm text-[var(--color-danger)]">{triggerError}</p>}

      {dashboard && (
        <>
          {stats && (
            <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatTile
                icon={Mail}
                label="Emails this week"
                value={stats.emails.this_week}
                delta={weekDelta(stats.emails)}
              />
              <StatTile
                icon={ListChecks}
                label="Tasks extracted"
                value={stats.tasks.this_week}
                delta={weekDelta(stats.tasks)}
              />
              <StatTile
                icon={CalendarDays}
                label="Meetings"
                value={stats.meetings.this_week}
                delta={weekDelta(stats.meetings)}
              />
              <StatTile
                icon={Users}
                label="People you owe work"
                value={stats.important_people}
              />
            </div>
          )}

          <Card className="mt-6 flex items-center justify-center p-8">
            <TasksRemainingBar
              open={dashboard.open_action_item_count}
              total={dashboard.contact_count + dashboard.open_action_item_count}
            />
          </Card>

          {stats && (
            <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-5">
              <Card className="lg:col-span-3">
                <h2 className="text-sm font-semibold text-[var(--color-fg)]">Email to task flow</h2>
                <p className="mt-0.5 mb-3 text-xs text-[var(--color-muted)]">Last 7 days</p>
                <ActivityChart data={stats.daily} />
              </Card>
              <Card className="lg:col-span-2">
                <h2 className="mb-4 text-sm font-semibold text-[var(--color-fg)]">Task categories</h2>
                <CategoryDonut categories={stats.categories} />
              </Card>
            </div>
          )}

          <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="text-sm font-semibold text-[var(--color-fg)]">Recent activity</h2>
              {dashboard.activity.length === 0 ? (
                <p className="mt-2 text-sm text-[var(--color-muted)]">No recent activity.</p>
              ) : (
                <ul className="mt-4 space-y-4">
                  {dashboard.activity.map((entry) => {
                    const Icon = entry.type === 'contact_updated' ? UserRound : ListPlus
                    return (
                      <li key={`${entry.type}-${entry.id}`} className="flex gap-3 text-sm">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface)] text-[var(--color-accent)]">
                          <Icon size={16} aria-hidden="true" />
                        </span>
                        <div className="min-w-0">
                          <p className="text-[var(--color-fg)]">
                            {entry.type === 'contact_updated'
                              ? `Updated contact: ${entry.display_name ?? entry.email_address}`
                              : `New action item (${entry.direction}): ${entry.text}`}
                          </p>
                          <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                            {new Date(entry.timestamp).toLocaleString()}
                          </p>
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
            </Card>

            {stats && (
              <Card>
                <h2 className="text-sm font-semibold text-[var(--color-fg)]">Upcoming tasks</h2>
                {stats.upcoming.length === 0 ? (
                  <p className="mt-2 text-sm text-[var(--color-muted)]">No upcoming tasks.</p>
                ) : (
                  <ul className="mt-4 space-y-4">
                    {stats.upcoming.map((item) => (
                      <li key={item.id} className="flex items-center justify-between gap-3 text-sm">
                        <div className="min-w-0">
                          <p className="truncate text-[var(--color-fg)]">{item.text}</p>
                          {item.contact && (
                            <p className="mt-0.5 truncate text-xs text-[var(--color-muted)]">
                              From: {item.contact}
                            </p>
                          )}
                        </div>
                        <span className="shrink-0 text-xs text-[var(--color-muted)]">
                          {parseDueDate(item.due_date).toLocaleDateString(undefined, {
                            month: 'short',
                            day: 'numeric',
                          })}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            )}
          </div>
        </>
      )}
    </div>
  )
}
