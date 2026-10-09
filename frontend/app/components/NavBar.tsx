'use client'

import { useEffect, useState, useSyncExternalStore } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import {
  ChevronsLeft,
  ChevronsRight,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Search,
  Settings,
  Users,
} from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { createClient } from '@/lib/supabase/client'
import { Button } from '@/app/components/ui/Button'

const NAV_LINKS = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/planner', label: 'Tasks', icon: ListTodo },
  { href: '/contacts', label: 'People', icon: Users },
  { href: '/search', label: 'Search', icon: Search },
  { href: '/settings', label: 'Settings', icon: Settings },
]

const CHROME_HIDDEN_PATHS = new Set(['/', '/login', '/callback'])
const COLLAPSE_STORAGE_KEY = 'nav-collapsed'

/**
 * The collapsed flag lives in localStorage, which is external state, so it is
 * read through useSyncExternalStore rather than copied into component state.
 *
 * getServerSnapshot returns false, which is what makes this hydration-safe:
 * next.config sets output: "export", so pages are prerendered with no window,
 * and reading localStorage during the first render would make the client
 * disagree with the prerendered HTML whenever the stored value is not default.
 */
const collapseListeners = new Set<() => void>()

function subscribeToCollapse(onChange: () => void) {
  collapseListeners.add(onChange)
  window.addEventListener('storage', onChange)
  return () => {
    collapseListeners.delete(onChange)
    window.removeEventListener('storage', onChange)
  }
}

function getCollapseSnapshot() {
  return window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === 'true'
}

function getCollapseServerSnapshot() {
  return false
}

function writeCollapsed(next: boolean) {
  window.localStorage.setItem(COLLAPSE_STORAGE_KEY, String(next))
  // `storage` only fires in *other* tabs, so notify this one explicitly.
  collapseListeners.forEach((listener) => listener())
}

export default function NavBar() {
  const pathname = usePathname()
  const router = useRouter()

  const collapsed = useSyncExternalStore(
    subscribeToCollapse,
    getCollapseSnapshot,
    getCollapseServerSnapshot
  )

  const [email, setEmail] = useState<string | null>(null)

  useEffect(() => {
    if (CHROME_HIDDEN_PATHS.has(pathname)) return
    let cancelled = false
    apiFetch('/api/me/graph-status')
      .then(async (response) => {
        if (!response.ok || cancelled) return
        const body = await response.json()
        const address = body.graph_me?.mail ?? body.graph_me?.userPrincipalName
        if (address && !cancelled) setEmail(address)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [pathname])

  if (CHROME_HIDDEN_PATHS.has(pathname)) return null

  function toggleCollapsed() {
    writeCollapsed(!collapsed)
  }

  async function handleSignOut() {
    const supabase = createClient()
    await supabase.auth.signOut()
    router.push('/login')
  }

  return (
    <motion.nav
      animate={{ width: collapsed ? 72 : 224 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className="flex shrink-0 flex-col overflow-hidden border-r border-[var(--color-border)] bg-[var(--color-bg-alt)] px-3 py-6"
    >
      <div className="mb-8 flex items-center justify-between px-1">
        {!collapsed && (
          <span className="truncate text-sm font-semibold tracking-wide text-[var(--color-fg)]">
            AI Workspace Assistant
          </span>
        )}
        <Button
          variant="ghost"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          onClick={toggleCollapsed}
        >
          {collapsed ? <ChevronsRight size={16} aria-hidden="true" /> : <ChevronsLeft size={16} aria-hidden="true" />}
        </Button>
      </div>

      <div className="flex flex-1 flex-col gap-1">
        {NAV_LINKS.map((link) => {
          const active = pathname === link.href || pathname.startsWith(`${link.href}/`)
          const Icon = link.icon
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-current={active ? 'page' : undefined}
              className={`relative flex items-center gap-3 rounded-[var(--radius-card)] px-3 py-2 text-sm transition ${
                active ? 'text-[var(--color-fg)]' : 'text-[var(--color-muted)] hover:text-[var(--color-fg)]'
              }`}
            >
              {active && (
                <motion.span
                  layoutId="nav-active-bg"
                  transition={{ duration: 0.2, ease: 'easeOut' }}
                  className="absolute inset-0 rounded-[var(--radius-card)] bg-[var(--color-accent)]/10"
                />
              )}
              {active && (
                <motion.span
                  layoutId="nav-active-bar"
                  transition={{ duration: 0.2, ease: 'easeOut' }}
                  className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-full bg-[var(--color-accent)]"
                />
              )}
              <Icon size={18} className="relative shrink-0" aria-hidden="true" />
              {!collapsed && <span className="relative truncate">{link.label}</span>}
            </Link>
          )
        })}
      </div>

      {email && (
        <div className="mt-4 flex items-center gap-3 border-t border-[var(--color-border)] px-1 pt-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface)] text-xs font-semibold text-[var(--color-accent)]">
            {email.charAt(0).toUpperCase()}
          </span>
          {!collapsed && <span className="truncate text-sm text-[var(--color-fg)]">{email}</span>}
        </div>
      )}

      <button
        onClick={handleSignOut}
        aria-label="Sign out"
        className="mt-6 flex items-center gap-3 rounded-[var(--radius-card)] px-3 py-2 text-left text-sm text-[var(--color-muted)] transition hover:text-[var(--color-fg)]"
      >
        <LogOut size={18} aria-hidden="true" />
        {!collapsed && <span>Sign out</span>}
      </button>
    </motion.nav>
  )
}
