'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { X } from 'lucide-react'

import { apiFetch } from '@/lib/api'

type Contact = { id: string; display_name: string | null; email_address: string | null }
type Slot = { start: string; end: string }

type ScheduleActionItemPanelProps = {
  itemId: string
  scheduledCalendarEventId: string | null
  scheduledStartTime: string | null
  contact: Contact | null
  onScheduled: () => void
}

export default function ScheduleActionItemPanel({
  itemId,
  scheduledCalendarEventId,
  scheduledStartTime,
  contact,
  onScheduled,
}: ScheduleActionItemPanelProps) {
  const [open, setOpen] = useState(false)
  const [slots, setSlots] = useState<Slot[] | null>(null)
  const [onlineMeeting, setOnlineMeeting] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!contact) return null

  if (scheduledCalendarEventId) {
    return (
      <span className="ml-2 text-[var(--color-muted)]">
        Scheduled: {scheduledStartTime ? new Date(scheduledStartTime).toLocaleString() : 'yes'}
      </span>
    )
  }

  async function openPanel() {
    setOpen(true)
    setError(null)
    setSlots(null)
    try {
      const response = await apiFetch(`/api/action-items/${itemId}/schedule-suggestions`)
      if (!response.ok) {
        // Set slots to [] rather than leaving it null. null also means "still
        // loading", so the panel used to render the error above a spinner that
        // would never resolve.
        setError('Could not load suggested times. Please try again.')
        setSlots([])
        return
      }
      setSlots(await response.json())
    } catch {
      setError('Could not load suggested times. Please try again.')
      setSlots([])
    }
  }

  function closePanel() {
    setOpen(false)
  }

  async function confirm(slot: Slot) {
    setError(null)
    setSubmitting(true)
    try {
      const response = await apiFetch(`/api/action-items/${itemId}/schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: slot.start, end: slot.end, online_meeting: onlineMeeting }),
      })
      if (!response.ok) {
        setError(
          response.status === 409
            ? 'That item was already scheduled. Refresh to see the booking.'
            : 'Could not schedule that meeting. Please try again.'
        )
        return
      }
      setOpen(false)
      onScheduled()
    } catch {
      // Without this, a rejected fetch left `submitting` true forever and every
      // slot stayed disabled, making the panel unusable until a page reload.
      setError('Could not schedule that meeting. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <span className="relative ml-2 inline-block">
      <button
        onClick={open ? closePanel : openPanel}
        aria-expanded={open}
        className="text-sm font-medium text-[var(--color-accent)] hover:underline"
      >
        Schedule
      </button>

      {open && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.2, ease: 'easeOut' }}
          className="absolute right-0 top-full z-10 mt-2 w-80 rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-xl"
        >
          <div className="flex items-center justify-between">
            <span className="text-base font-semibold text-[var(--color-fg)]">Schedule meeting</span>
            <button
              onClick={closePanel}
              aria-label="Close"
              className="text-[var(--color-muted)] transition hover:text-[var(--color-fg)]"
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>

          {error && <p role="alert" className="mt-3 text-sm text-[var(--color-danger)]">{error}</p>}

          {slots === null ? (
            <p className="mt-4 text-sm text-[var(--color-muted)]">Loading suggestions…</p>
          ) : slots.length === 0 ? (
            <p className="mt-4 text-sm text-[var(--color-muted)]">No open slots found.</p>
          ) : (
            <>
              <label className="mt-4 flex items-center justify-between text-sm text-[var(--color-fg)]">
                Online meeting
                <span className="relative inline-flex h-5 w-9 shrink-0">
                  <input
                    type="checkbox"
                    checked={onlineMeeting}
                    onChange={(e) => setOnlineMeeting(e.target.checked)}
                    disabled={submitting}
                    className="peer sr-only"
                  />
                  <span className="absolute inset-0 rounded-full bg-[var(--color-border)] transition-colors peer-checked:bg-[var(--color-accent)]" />
                  <span className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-[var(--color-fg)] transition-transform peer-checked:translate-x-4" />
                </span>
              </label>

              <div className={`mt-4 grid grid-cols-2 gap-2.5 ${submitting ? 'opacity-50' : ''}`}>
                {slots.map((slot) => (
                  <button
                    key={slot.start}
                    onClick={() => confirm(slot)}
                    disabled={submitting}
                    className="rounded-[var(--radius-card)] border border-[var(--color-border)] px-3 py-2.5 text-xs font-medium text-[var(--color-fg)] transition hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/10 disabled:pointer-events-none"
                  >
                    {new Date(slot.start).toLocaleString()}
                  </button>
                ))}
              </div>
            </>
          )}
        </motion.div>
      )}
    </span>
  )
}
