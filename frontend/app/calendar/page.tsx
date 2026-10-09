'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Video } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { Card } from '@/app/components/ui/Card'

type CalendarEvent = {
  id: string
  subject: string | null
  organizer: string | null
  start_time: string
  end_time: string | null
  is_online_meeting: boolean
  join_url: string | null
}

type State = { state: 'loading' } | { state: 'error' } | { state: 'ready'; events: CalendarEvent[] }

/** Group by local calendar day, so the headings match the user's own dates. */
function groupByDay(events: CalendarEvent[]): [string, CalendarEvent[]][] {
  const groups = new Map<string, CalendarEvent[]>()
  for (const event of events) {
    const key = new Date(event.start_time).toLocaleDateString(undefined, {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
    })
    groups.set(key, [...(groups.get(key) ?? []), event])
  }
  return [...groups.entries()]
}

function timeRange(event: CalendarEvent): string {
  const opts: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' }
  const start = new Date(event.start_time).toLocaleTimeString(undefined, opts)
  if (!event.end_time) return start
  return `${start} to ${new Date(event.end_time).toLocaleTimeString(undefined, opts)}`
}

export default function CalendarPage() {
  const router = useRouter()
  const [state, setState] = useState<State>({ state: 'loading' })

  const load = useCallback(async () => {
    try {
      const response = await apiFetch('/api/calendar')
      if (response.status === 401) {
        router.push('/login')
        return
      }
      if (!response.ok) {
        setState({ state: 'error' })
        return
      }
      setState({ state: 'ready', events: await response.json() })
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
          Could not load your calendar. Refresh to try again.
        </p>
      </div>
    )
  }

  const days = groupByDay(state.events)

  return (
    <div className="p-8">
      <h1 className="text-xl font-bold text-[var(--color-fg)]">Calendar</h1>
      <p className="mt-1 text-sm text-[var(--color-muted)]">What is coming up.</p>

      {days.length === 0 ? (
        <p className="mt-6 text-sm text-[var(--color-muted)]">Nothing scheduled.</p>
      ) : (
        <div className="mt-6 space-y-6">
          {days.map(([day, events]) => (
            <section key={day}>
              <h2 className="text-sm font-semibold text-[var(--color-fg)]">{day}</h2>
              <div className="mt-2 space-y-2">
                {events.map((event) => (
                  <Card key={event.id} className="flex items-center justify-between gap-4 py-4">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-[var(--color-fg)]">
                        {event.subject ?? 'Untitled event'}
                      </p>
                      <p className="mt-0.5 truncate text-xs text-[var(--color-muted)]">
                        {timeRange(event)}
                        {event.organizer ? ` · ${event.organizer}` : ''}
                      </p>
                    </div>
                    {event.is_online_meeting && event.join_url && (
                      <a
                        href={event.join_url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex shrink-0 items-center gap-1.5 text-sm font-medium text-[var(--color-accent)] hover:underline"
                      >
                        <Video size={14} aria-hidden="true" />
                        Join
                      </a>
                    )}
                  </Card>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
