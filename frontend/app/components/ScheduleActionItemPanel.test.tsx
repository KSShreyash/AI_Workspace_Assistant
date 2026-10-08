import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock } = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))

import ScheduleActionItemPanel from './ScheduleActionItemPanel'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

const CONTACT = { id: 'c1', display_name: 'Gina', email_address: 'gina@example.com' }

describe('ScheduleActionItemPanel', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
  })

  it('renders nothing when there is no linked contact', () => {
    const { container } = render(
      <ScheduleActionItemPanel
        itemId="item-1" scheduledCalendarEventId={null} scheduledStartTime={null}
        contact={null} onScheduled={vi.fn()}
      />
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('shows a scheduled indicator instead of a button when already scheduled', () => {
    render(
      <ScheduleActionItemPanel
        itemId="item-1" scheduledCalendarEventId="evt-1" scheduledStartTime="2026-07-20T14:00:00Z"
        contact={CONTACT} onScheduled={vi.fn()}
      />
    )

    expect(screen.getByText(/scheduled/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /schedule/i })).not.toBeInTheDocument()
  })

  it('fetches and shows suggested slots when Schedule is clicked', async () => {
    apiFetchMock.mockResolvedValue(
      jsonResponse([{ start: '2026-07-20T14:00:00Z', end: '2026-07-20T14:30:00Z' }])
    )

    render(
      <ScheduleActionItemPanel
        itemId="item-1" scheduledCalendarEventId={null} scheduledStartTime={null}
        contact={CONTACT} onScheduled={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /schedule/i }))

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith('/api/action-items/item-1/schedule-suggestions'))
    expect(await screen.findByRole('button', { name: /2026/i })).toBeInTheDocument()
  })

  it('confirms a slot and calls onScheduled on success', async () => {
    const onScheduled = vi.fn()
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === 'POST') return Promise.resolve(jsonResponse({ status: 'ok' }))
      return Promise.resolve(jsonResponse([{ start: '2026-07-20T14:00:00Z', end: '2026-07-20T14:30:00Z' }]))
    })

    render(
      <ScheduleActionItemPanel
        itemId="item-1" scheduledCalendarEventId={null} scheduledStartTime={null}
        contact={CONTACT} onScheduled={onScheduled}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /schedule/i }))
    const slotButton = await screen.findByRole('button', { name: /2026/i })
    fireEvent.click(slotButton)

    await waitFor(() => expect(onScheduled).toHaveBeenCalled())
    expect(apiFetchMock).toHaveBeenCalledWith('/api/action-items/item-1/schedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start: '2026-07-20T14:00:00Z', end: '2026-07-20T14:30:00Z', online_meeting: true }),
    })
  })

  it('disables the slot buttons while a schedule request is in flight', async () => {
    let resolvePost: (value: Response) => void = () => {}
    const postPromise = new Promise<Response>((resolve) => {
      resolvePost = resolve
    })
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === 'POST') return postPromise
      return Promise.resolve(jsonResponse([{ start: '2026-07-20T14:00:00Z', end: '2026-07-20T14:30:00Z' }]))
    })

    render(
      <ScheduleActionItemPanel
        itemId="item-1" scheduledCalendarEventId={null} scheduledStartTime={null}
        contact={CONTACT} onScheduled={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /schedule/i }))
    const slotButton = await screen.findByRole('button', { name: /2026/i })
    fireEvent.click(slotButton)

    await waitFor(() => expect(slotButton).toBeDisabled())

    resolvePost(jsonResponse({ status: 'ok' }))
  })

  it('shows an inline error and keeps the panel open when confirming fails', async () => {
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === 'POST') return Promise.resolve(new Response(null, { status: 502 }))
      return Promise.resolve(jsonResponse([{ start: '2026-07-20T14:00:00Z', end: '2026-07-20T14:30:00Z' }]))
    })

    render(
      <ScheduleActionItemPanel
        itemId="item-1" scheduledCalendarEventId={null} scheduledStartTime={null}
        contact={CONTACT} onScheduled={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /schedule/i }))
    const slotButton = await screen.findByRole('button', { name: /2026/i })
    fireEvent.click(slotButton)

    await waitFor(() => expect(screen.getByText(/could not schedule/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /2026/i })).toBeInTheDocument()
  })

  it('reflects the open state via aria-expanded on the trigger', async () => {
    apiFetchMock.mockResolvedValue(
      jsonResponse([{ start: '2026-07-20T14:00:00Z', end: '2026-07-20T14:30:00Z' }])
    )

    render(
      <ScheduleActionItemPanel
        itemId="item-1" scheduledCalendarEventId={null} scheduledStartTime={null}
        contact={CONTACT} onScheduled={vi.fn()}
      />
    )
    const trigger = screen.getByRole('button', { name: /schedule/i })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(trigger)

    await waitFor(() => expect(trigger).toHaveAttribute('aria-expanded', 'true'))
  })

  it('closes the panel when the close button is clicked', async () => {
    apiFetchMock.mockResolvedValue(
      jsonResponse([{ start: '2026-07-20T14:00:00Z', end: '2026-07-20T14:30:00Z' }])
    )

    render(
      <ScheduleActionItemPanel
        itemId="item-1" scheduledCalendarEventId={null} scheduledStartTime={null}
        contact={CONTACT} onScheduled={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /schedule/i }))
    await screen.findByRole('button', { name: /2026/i })

    fireEvent.click(screen.getByRole('button', { name: /close/i }))

    expect(screen.queryByRole('button', { name: /2026/i })).not.toBeInTheDocument()
  })
})

describe('ScheduleActionItemPanel network failures', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
  })

  const SLOTS = [
    { start: '2026-07-20T09:00:00Z', end: '2026-07-20T09:30:00Z' },
    { start: '2026-07-20T10:00:00Z', end: '2026-07-20T10:30:00Z' },
  ]

  it('does not leave the slots permanently disabled when booking rejects', async () => {
    // `submitting` was never cleared on a rejected fetch, so every slot stayed
    // disabled and the panel became unusable without a page reload.
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (init?.method === 'POST') return Promise.reject(new Error('network down'))
      return Promise.resolve(jsonResponse(SLOTS))
    })

    render(
      <ScheduleActionItemPanel
        itemId="i1"
        scheduledCalendarEventId={null}
        scheduledStartTime={null}
        contact={CONTACT}
        onScheduled={() => {}}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /schedule/i }))
    const slots = await screen.findAllByRole('button', { name: /:\d\d/ })
    fireEvent.click(slots[0])

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
    const slotsAfter = screen.getAllByRole('button').filter((b) => /:\d\d/.test(b.textContent ?? ''))
    expect(slotsAfter.some((b) => !(b as HTMLButtonElement).disabled)).toBe(true)
  })

  it('stops showing "Loading suggestions" once loading has failed', async () => {
    // slots stayed null on failure, and null also means "still loading", so the
    // panel rendered the error above a spinner that would never resolve.
    apiFetchMock.mockImplementation(() => Promise.reject(new Error('network down')))

    render(
      <ScheduleActionItemPanel
        itemId="i1"
        scheduledCalendarEventId={null}
        scheduledStartTime={null}
        contact={CONTACT}
        onScheduled={() => {}}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /schedule/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
    expect(screen.queryByText(/loading suggestions/i)).not.toBeInTheDocument()
  })
})
