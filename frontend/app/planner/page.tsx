'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'

import { apiFetch } from '@/lib/api'
import { getInitials } from '@/lib/getInitials'
import { parseDueDate } from '@/lib/parseDueDate'
import ScheduleActionItemPanel from '@/app/components/ScheduleActionItemPanel'
import { Badge, type BadgeVariant } from '@/app/components/ui/Badge'
import { Checkbox } from '@/app/components/ui/Checkbox'

type ActionItem = {
  id: string
  text: string
  direction: 'mine' | 'theirs'
  status: 'open' | 'done'
  due_date: string | null
  contact: { id: string; display_name: string | null; email_address: string | null } | null
  scheduled_calendar_event_id: string | null
  scheduled_start_time: string | null
  created_at: string
  updated_at: string
}

type Direction = 'all' | 'mine' | 'theirs'
type TabKey = 'overdue' | 'today' | 'tomorrow' | 'thisWeek' | 'nextWeek' | 'noDate' | 'completed'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'overdue', label: 'Overdue' },
  { key: 'today', label: 'Today' },
  { key: 'tomorrow', label: 'Tomorrow' },
  { key: 'thisWeek', label: 'This week' },
  { key: 'nextWeek', label: 'Next week' },
  { key: 'noDate', label: 'No date' },
  { key: 'completed', label: 'Completed' },
]

/**
 * Whole days between today and a due date, both on the user's local calendar.
 *
 * Previously both sides were computed in UTC. For anyone east of UTC that put a
 * task due today into Tomorrow once local time passed midnight, and for anyone
 * west of UTC it did the reverse - while the date badge on the same row used a
 * different basis again, so the bucket and the badge could disagree.
 */
function daysFromNow(dateStr: string): number {
  const due = parseDueDate(dateStr)
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((due.getTime() - startOfToday.getTime()) / (1000 * 60 * 60 * 24))
}

function bucketOf(item: ActionItem): TabKey {
  if (item.status === 'done') return 'completed'
  if (!item.due_date) return 'noDate'
  const days = daysFromNow(item.due_date)
  if (days < 0) return 'overdue'
  if (days === 0) return 'today'
  if (days === 1) return 'tomorrow'
  if (days <= 7) return 'thisWeek'
  return 'nextWeek'
}

function badgeVariantForBucket(bucket: TabKey): BadgeVariant {
  if (bucket === 'overdue') return 'danger'
  if (bucket === 'today' || bucket === 'tomorrow' || bucket === 'thisWeek') return 'accent'
  return 'muted'
}

export default function PlannerPage() {
  const router = useRouter()
  const [items, setItems] = useState<ActionItem[]>([])
  const [direction, setDirection] = useState<Direction>('all')
  const [activeTab, setActiveTab] = useState<TabKey>('today')
  const [toggleError, setToggleError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ include_done: 'true' })
      if (direction !== 'all') params.set('direction', direction)
      const response = await apiFetch(`/api/action-items?${params.toString()}`)
      if (response.status === 401) {
        router.push('/login')
        return
      }
      if (!response.ok) {
        setToggleError('Something went wrong loading your action items. Please try again.')
        return
      }
      setItems(await response.json())
    } catch {
      setToggleError('Something went wrong loading your action items. Please try again.')
    }
  }, [direction, router])

  useEffect(() => {
    // Wrapped rather than calling load() directly: the lint rule treats a bare
    // call as a synchronous setState in an effect, which it is not - load is
    // async and every setState in it happens after an await.
    void (async () => {
      await load()
    })()
  }, [load])

  async function toggleDone(item: ActionItem) {
    setToggleError(null)
    const nextStatus = item.status === 'open' ? 'done' : 'open'
    try {
      const response = await apiFetch(`/api/action-items/${item.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: nextStatus }),
      })
      if (!response.ok) {
        setToggleError('Something went wrong updating that item. Please try again.')
        return
      }
      await load()
    } catch {
      // Previously the rejection escaped past React entirely and the user saw
      // nothing, despite toggleError existing for exactly this case.
      setToggleError('Something went wrong updating that item. Please try again.')
    }
  }

  const buckets: Record<TabKey, ActionItem[]> = {
    overdue: [],
    today: [],
    tomorrow: [],
    thisWeek: [],
    nextWeek: [],
    noDate: [],
    completed: [],
  }
  for (const item of items) {
    buckets[bucketOf(item)].push(item)
  }

  function renderItem(item: ActionItem) {
    const isDone = item.status === 'done'
    const badgeVariant = badgeVariantForBucket(bucketOf(item))
    return (
      <div key={item.id} className="flex items-center gap-3 px-4 py-3">
        <Checkbox
          checked={isDone}
          onChange={() => toggleDone(item)}
          aria-label={isDone ? 'Reopen' : 'Mark done'}
        />
        <div className="min-w-0 flex-1">
          <p className={`text-sm ${isDone ? 'text-[var(--color-muted)] line-through' : 'text-[var(--color-fg)]'}`}>
            {item.text}
          </p>
          {item.contact && (
            <div className="mt-1 flex items-center gap-2">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)]/10 text-[10px] font-semibold text-[var(--color-accent)]">
                {getInitials(item.contact.display_name, item.contact.email_address)}
              </span>
              <span className="text-xs text-[var(--color-muted)]">
                {item.contact.display_name ?? item.contact.email_address}
              </span>
            </div>
          )}
        </div>
        {item.due_date && (
          <Badge variant={badgeVariant}>
            {parseDueDate(item.due_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
          </Badge>
        )}
        {!isDone && (
          <ScheduleActionItemPanel
            itemId={item.id}
            scheduledCalendarEventId={item.scheduled_calendar_event_id}
            scheduledStartTime={item.scheduled_start_time}
            contact={item.contact}
            onScheduled={load}
          />
        )}
      </div>
    )
  }

  const activeItems = buckets[activeTab]

  return (
    <div className="p-8">
      <h1 className="text-xl font-bold text-[var(--color-fg)]">Tasks</h1>

      <div className="mt-4 flex flex-wrap items-center gap-4">
        <div className="flex flex-wrap gap-2">
          {TABS.map((tab) => {
            const active = activeTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                aria-pressed={active}
                className={`flex items-center gap-2 rounded-[var(--radius-card)] px-3 py-1.5 text-sm font-medium transition ${
                  active
                    ? 'bg-[var(--color-accent)] text-[var(--color-accent-fg)]'
                    : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:text-[var(--color-fg)]'
                }`}
              >
                {tab.label}
                <span
                  className={`rounded-full px-1.5 py-0.5 text-xs ${
                    active ? 'bg-[var(--color-bg)]/20' : 'bg-[var(--color-surface)] text-[var(--color-muted)]'
                  }`}
                >
                  {buckets[tab.key].length}
                </span>
              </button>
            )
          })}
        </div>

        <select
          value={direction}
          aria-label="Who the task is for"
          onChange={(e) => setDirection(e.target.value as Direction)}
          className="rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-fg)] focus:border-[var(--color-accent)] focus:outline-none"
        >
          <option value="all">All</option>
          <option value="mine">Mine</option>
          <option value="theirs">Theirs</option>
        </select>
      </div>

      {toggleError && <p role="alert" className="mt-3 text-sm text-[var(--color-danger)]">{toggleError}</p>}

      {items.length === 0 ? (
        <p className="mt-4 text-sm text-[var(--color-muted)]">Nothing due.</p>
      ) : (
        <div className="mt-4 divide-y divide-[var(--color-border)] rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface)]">
          {activeItems.length === 0 ? (
            <p className="px-4 py-6 text-sm text-[var(--color-muted)]">Nothing here.</p>
          ) : (
            activeItems.map((item) => renderItem(item))
          )}
        </div>
      )}
    </div>
  )
}
