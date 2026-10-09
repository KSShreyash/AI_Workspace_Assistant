'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Users } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { formatRelativeTime } from '@/lib/formatRelativeTime'
import { getInitials } from '@/lib/getInitials'
import { Badge } from '@/app/components/ui/Badge'
import { Button } from '@/app/components/ui/Button'
import { EmptyState } from '@/app/components/ui/EmptyState'
import { Input } from '@/app/components/ui/Input'

type Contact = {
  id: string
  email_address: string | null
  display_name: string | null
  open_action_item_count: number
  updated_at: string
}

type SortMode = 'recent' | 'name'

function sortContacts(contacts: Contact[], mode: SortMode): Contact[] {
  const copy = [...contacts]
  if (mode === 'name') {
    copy.sort((a, b) =>
      (a.display_name ?? a.email_address ?? '').localeCompare(b.display_name ?? b.email_address ?? '')
    )
  } else {
    copy.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
  }
  return copy
}

export default function ContactsPage() {
  const router = useRouter()
  const [contacts, setContacts] = useState<Contact[] | null>(null)
  const [error, setError] = useState(false)
  const [search, setSearch] = useState('')
  const [sortMode, setSortMode] = useState<SortMode>('recent')
  const requestId = useRef(0)

  useEffect(() => {
    const timer = setTimeout(() => {
      const thisRequest = ++requestId.current
      const query = search ? `?q=${encodeURIComponent(search)}` : ''
      apiFetch(`/api/contacts${query}`)
        .then(async (response) => {
          if (response.status === 401) {
            router.push('/login')
            return
          }
          if (!response.ok) {
            if (thisRequest === requestId.current) setError(true)
            return
          }
          const body = await response.json()
          if (thisRequest === requestId.current) {
            setError(false)
            setContacts(body)
          }
        })
        .catch(() => {
          if (thisRequest === requestId.current) setError(true)
        })
    }, 300)

    return () => clearTimeout(timer)
  }, [search, router])

  const sortedContacts = contacts ? sortContacts(contacts, sortMode) : null

  return (
    <div className="p-8">
      <h1 className="text-xl font-bold text-[var(--color-fg)]">People</h1>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Input
          value={search}
          onChange={setSearch}
          placeholder="Search contacts…"
          aria-label="Search contacts"
          className="max-w-sm"
        />
        <div className="flex gap-2">
          <Button variant={sortMode === 'recent' ? 'primary' : 'secondary'} onClick={() => setSortMode('recent')}>
            Recent
          </Button>
          <Button variant={sortMode === 'name' ? 'primary' : 'secondary'} onClick={() => setSortMode('name')}>
            Name
          </Button>
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-4 text-sm text-[var(--color-danger)]">
          Something went wrong loading your contacts.
        </p>
      )}

      {sortedContacts === null ? (
        error ? null : <p className="mt-4 text-sm text-[var(--color-muted)]">Loading…</p>
      ) : sortedContacts.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No contacts yet"
          description="Sync and extract to get started."
          className="mt-4"
        />
      ) : (
        <div className="mt-4 overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-border)]">
          <table className="w-full text-left">
            <thead className="bg-[var(--color-bg-alt)]">
              <tr>
                <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  Contact
                </th>
                <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  Open Items
                </th>
                <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  Last Interaction
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {sortedContacts.map((contact) => (
                <tr key={contact.id} className="bg-[var(--color-surface)] transition hover:bg-[var(--color-bg-alt)]">
                  <td className="px-4 py-3">
                    <a
                      href={`/contacts/view?id=${contact.id}`}
                      aria-label={contact.display_name ?? contact.email_address ?? undefined}
                      className="flex items-center gap-3"
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-alt)] text-xs font-semibold text-[var(--color-accent)]">
                        {getInitials(contact.display_name, contact.email_address)}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-[var(--color-fg)]">
                          {contact.display_name ?? contact.email_address}
                        </span>
                        {contact.email_address && (
                          <span className="block truncate text-xs text-[var(--color-muted)]">{contact.email_address}</span>
                        )}
                      </span>
                    </a>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={contact.open_action_item_count > 0 ? 'accent' : 'muted'}>
                      {contact.open_action_item_count} open
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-[var(--color-muted)]">
                    {formatRelativeTime(contact.updated_at, new Date())}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
