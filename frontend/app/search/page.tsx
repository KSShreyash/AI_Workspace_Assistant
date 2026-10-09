'use client'

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { ListChecks, Search as SearchIcon } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { getInitials } from '@/lib/getInitials'
import { Card } from '@/app/components/ui/Card'
import { EmptyState } from '@/app/components/ui/EmptyState'
import { Input } from '@/app/components/ui/Input'

type Contact = {
  id: string
  display_name: string | null
  email_address: string | null
  notes: string | null
}

type ActionItem = {
  id: string
  text: string
  direction: 'mine' | 'theirs'
  status: 'open' | 'done'
  due_date: string | null
  contact: { id: string; display_name: string | null; email_address: string | null } | null
}

type Results = { contacts: Contact[]; action_items: ActionItem[] }
type SearchFilter = 'all' | 'people' | 'tasks'

const FILTERS: { key: SearchFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'people', label: 'People' },
  { key: 'tasks', label: 'Tasks' },
]

const RECENT_SEARCHES_KEY = 'recent-searches'
const MAX_RECENT_SEARCHES = 5

function loadRecentSearches(): string[] {
  if (typeof window === 'undefined') return []
  try {
    const stored = window.localStorage.getItem(RECENT_SEARCHES_KEY)
    if (!stored) return []
    const parsed = JSON.parse(stored)
    if (Array.isArray(parsed) && parsed.every((item) => typeof item === 'string')) {
      return parsed
    }
    return []
  } catch {
    return []
  }
}

function highlightMatch(text: string, query: string): ReactNode {
  if (!query) return text
  const index = text.toLowerCase().indexOf(query.toLowerCase())
  if (index === -1) return text
  return (
    <>
      {text.slice(0, index)}
      <mark className="rounded bg-[var(--color-accent)]/20 text-[var(--color-accent)]">
        {text.slice(index, index + query.length)}
      </mark>
      {text.slice(index + query.length)}
    </>
  )
}

export default function SearchPage() {
  const router = useRouter()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Results | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<SearchFilter>('all')
  const [recentSearches, setRecentSearches] = useState<string[]>(loadRecentSearches)
  const requestId = useRef(0)

  function recordRecentSearch(term: string) {
    setRecentSearches((prev) => {
      const next = [term, ...prev.filter((item) => item !== term)].slice(0, MAX_RECENT_SEARCHES)
      window.localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(next))
      return next
    })
  }

  function clearRecentSearches() {
    setRecentSearches([])
    window.localStorage.removeItem(RECENT_SEARCHES_KEY)
  }

  useEffect(() => {
    if (!query) {
      // Invalidate any in-flight request, but do not clear state here: with an
      // empty query the rendered result is derived below, so clearing would be
      // a synchronous setState in an effect for no gain.
      requestId.current++
      return
    }

    const timer = setTimeout(() => {
      const thisRequest = ++requestId.current
      apiFetch(`/api/search?q=${encodeURIComponent(query)}`)
        .then(async (response) => {
          if (thisRequest !== requestId.current) return
          if (response.status === 401) {
            router.push('/login')
            return
          }
          if (!response.ok) {
            setError('Something went wrong searching. Please try again.')
            return
          }
          setError(null)
          setResults(await response.json())
          recordRecentSearch(query)
        })
        .catch(() => {
          if (thisRequest === requestId.current) {
            setError('Something went wrong searching. Please try again.')
          }
        })
    }, 300)

    return () => clearTimeout(timer)
  }, [query, router])

  // Derived, not stored: with no query there is nothing to show, regardless of
  // what the last search returned.
  const visibleResults = query ? results : null
  const visibleError = query ? error : null

  const showContacts = filter === 'all' || filter === 'people'
  const showTasks = filter === 'all' || filter === 'tasks'

  return (
    <div className="p-8">
      <h1 className="text-xl font-bold text-[var(--color-fg)]">Search</h1>

      <Input
        value={query}
        onChange={setQuery}
        placeholder="Search contacts and action items…"
        aria-label="Search"
        className="mt-4 max-w-md"
      />

      <div className="mt-4 flex flex-wrap gap-2">
        {FILTERS.map((f) => {
          const active = filter === f.key
          return (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              aria-pressed={active}
              className={`rounded-[var(--radius-card)] px-3 py-1.5 text-sm font-medium transition ${
                active
                  ? 'bg-[var(--color-accent)] text-[var(--color-accent-fg)]'
                  : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:text-[var(--color-fg)]'
              }`}
            >
              {f.label}
            </button>
          )
        })}
      </div>

      {recentSearches.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-[var(--color-muted)]">Recent:</span>
          {recentSearches.map((term) => (
            <button
              key={term}
              type="button"
              onClick={() => setQuery(term)}
              className="rounded-full border border-[var(--color-border)] px-3 py-1 text-xs text-[var(--color-muted)] transition hover:border-[var(--color-accent)] hover:text-[var(--color-fg)]"
            >
              {term}
            </button>
          ))}
          <button
            type="button"
            onClick={clearRecentSearches}
            className="text-xs font-medium text-[var(--color-accent)] hover:underline"
          >
            Clear all
          </button>
        </div>
      )}

      {visibleError && <p role="alert" className="mt-3 text-sm text-[var(--color-danger)]">{visibleError}</p>}

      {visibleResults === null ? (
        <EmptyState
          icon={SearchIcon}
          title="Type to search your contacts and action items."
          className="mt-6"
        />
      ) : (
        <>
          {showContacts && (
            <>
              <h2 className="mt-6 text-sm font-semibold text-[var(--color-fg)]">Contacts</h2>
              {visibleResults.contacts.length === 0 ? (
                <p className="mt-2 text-sm text-[var(--color-muted)]">No matching contacts.</p>
              ) : (
                <div className="mt-2 space-y-3">
                  {visibleResults.contacts.map((contact) => (
                    <Card key={contact.id} className="flex items-center gap-4">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface)] text-sm font-semibold text-[var(--color-accent)]">
                        {getInitials(contact.display_name, contact.email_address)}
                      </span>
                      <div className="min-w-0">
                        <a
                          href={`/contacts/view?id=${contact.id}`}
                          className="text-sm font-medium text-[var(--color-fg)] hover:underline"
                        >
                          {highlightMatch(contact.display_name ?? contact.email_address ?? '', query)}
                        </a>
                        {contact.notes && (
                          <p className="truncate text-xs text-[var(--color-muted)]">{contact.notes}</p>
                        )}
                      </div>
                    </Card>
                  ))}
                </div>
              )}
            </>
          )}

          {showTasks && (
            <>
              <h2 className="mt-6 text-sm font-semibold text-[var(--color-fg)]">Action Items</h2>
              {visibleResults.action_items.length === 0 ? (
                <p className="mt-2 text-sm text-[var(--color-muted)]">No matching action items.</p>
              ) : (
                <div className="mt-2 space-y-3">
                  {visibleResults.action_items.map((item) => (
                    <Card key={item.id} className="flex items-center justify-between gap-4">
                      <div className="flex min-w-0 items-center gap-4">
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface)] text-[var(--color-accent)]">
                          <ListChecks size={18} aria-hidden="true" />
                        </span>
                        <p className="min-w-0 truncate text-sm text-[var(--color-fg)]">
                          {highlightMatch(item.text, query)}
                        </p>
                      </div>
                      {item.contact && (
                        <a
                          href={`/contacts/view?id=${item.contact.id}`}
                          className="shrink-0 text-xs font-medium text-[var(--color-accent)] hover:underline"
                        >
                          {item.contact.display_name ?? item.contact.email_address}
                        </a>
                      )}
                    </Card>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
