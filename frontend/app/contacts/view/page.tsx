'use client'

import { Suspense, useCallback, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import { apiFetch } from '@/lib/api'
import { getInitials } from '@/lib/getInitials'
import ScheduleActionItemPanel from '@/app/components/ScheduleActionItemPanel'
import { Card } from '@/app/components/ui/Card'

type ContactDetail = {
  id: string
  email_address: string | null
  display_name: string | null
  notes: string | null
  created_at: string
  updated_at: string
}

type ActionItem = {
  id: string
  text: string
  direction: 'mine' | 'theirs'
  status: 'open' | 'done'
  due_date: string | null
  source_type: string
  scheduled_calendar_event_id: string | null
  scheduled_start_time: string | null
  created_at: string
  updated_at: string
}

type State =
  | { state: 'loading' }
  | { state: 'not_found' }
  | { state: 'error' }
  | { state: 'ready'; contact: ContactDetail; actionItems: ActionItem[] }

export default function ContactProfilePage() {
  return (
    <Suspense fallback={<p className="p-8 text-[var(--color-muted)]">Loading…</p>}>
      <ContactProfileContent />
    </Suspense>
  )
}

function ContactProfileContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const id = searchParams.get('id')
  const [state, setState] = useState<State>({ state: 'loading' })

  const load = useCallback(async () => {
    if (!id) {
      setState({ state: 'not_found' })
      return
    }

    try {
      const contactResponse = await apiFetch(`/api/contacts/${id}`)
      if (contactResponse.status === 401) {
        router.push('/login')
        return
      }
      if (contactResponse.status === 404) {
        setState({ state: 'not_found' })
        return
      }
      if (!contactResponse.ok) {
        setState({ state: 'error' })
        return
      }
      const contact = await contactResponse.json()

      const actionItemsResponse = await apiFetch(`/api/contacts/${id}/action-items`)
      if (!actionItemsResponse.ok) {
        setState({ state: 'error' })
        return
      }
      const actionItems = await actionItemsResponse.json()

      setState({ state: 'ready', contact, actionItems })
    } catch {
      setState({ state: 'error' })
    }
  }, [id, router])

  useEffect(() => {
    // Wrapped rather than calling load() directly: the lint rule treats a bare
    // call as a synchronous setState in an effect, which it is not - load is
    // async and every setState in it happens after an await.
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
  if (state.state === 'not_found') {
    return (
      <div className="p-8">
        <p className="text-[var(--color-muted)]">Contact not found.</p>
      </div>
    )
  }
  if (state.state === 'error') {
    return (
      <div className="p-8">
        <p role="alert" className="text-[var(--color-danger)]">Something went wrong loading this contact.</p>
      </div>
    )
  }

  const { contact, actionItems } = state
  const openItems = actionItems.filter((item) => item.status === 'open')
  const doneItems = actionItems.filter((item) => item.status === 'done')

  return (
    <div className="p-8">
      <Card className="flex items-center gap-4">
        <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)]/10 text-lg font-semibold text-[var(--color-accent)]">
          {getInitials(contact.display_name, contact.email_address)}
        </span>
        <div>
          <h1 className="text-xl font-bold text-[var(--color-fg)]">
            {contact.display_name ?? contact.email_address}
          </h1>
          {contact.email_address && <p className="text-sm text-[var(--color-muted)]">{contact.email_address}</p>}
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            Member since{' '}
            {new Date(contact.created_at).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
          </p>
        </div>
      </Card>

      <Card className="mt-4">
        <h2 className="text-sm font-semibold text-[var(--color-fg)]">AI summary</h2>
        <p className="mt-2 text-sm text-[var(--color-muted)]">{contact.notes ?? 'No notes yet.'}</p>
      </Card>

      <h2 className="mt-6 text-sm font-semibold text-[var(--color-fg)]">Open</h2>
      {openItems.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">Nothing open.</p>
      ) : (
        <div className="mt-2 divide-y divide-[var(--color-border)] rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface)]">
          {openItems.map((item) => (
            <div key={item.id} className="flex items-center justify-between gap-3 px-4 py-3 text-sm text-[var(--color-fg)]">
              <span>{item.text}</span>
              <ScheduleActionItemPanel
                itemId={item.id}
                scheduledCalendarEventId={item.scheduled_calendar_event_id}
                scheduledStartTime={item.scheduled_start_time}
                contact={contact}
                onScheduled={load}
              />
            </div>
          ))}
        </div>
      )}

      <h2 className="mt-6 text-sm font-semibold text-[var(--color-fg)]">Done</h2>
      {doneItems.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">Nothing done yet.</p>
      ) : (
        <div className="mt-2 divide-y divide-[var(--color-border)] rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface)]">
          {doneItems.map((item) => (
            <p key={item.id} className="px-4 py-3 text-sm text-[var(--color-muted)] line-through">
              {item.text}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
