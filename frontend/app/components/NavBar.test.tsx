import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetchMock } = vi.hoisted(() => ({ apiFetchMock: vi.fn() }))
const signOutMock = vi.fn()

vi.mock('@/lib/api', () => ({ apiFetch: apiFetchMock }))
vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({ auth: { signOut: signOutMock } }),
}))

let mockPathname = '/dashboard'
const pushMock = vi.fn()

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} data-client-nav="true" {...rest}>
      {children}
    </a>
  ),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: pushMock }),
}))

import NavBar from './NavBar'

describe('NavBar', () => {
  beforeEach(() => {
    signOutMock.mockReset()
    pushMock.mockReset()
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue(new Response(null, { status: 401 }))
    window.localStorage.clear()
    mockPathname = '/dashboard'
  })

  it('renders nothing on chrome-hidden paths', () => {
    mockPathname = '/login'
    const { container } = render(<NavBar />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders all nav links with the correct hrefs', () => {
    render(<NavBar />)
    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/dashboard')
    expect(screen.getByRole('link', { name: /people/i })).toHaveAttribute('href', '/contacts')
    expect(screen.getByRole('link', { name: /tasks/i })).toHaveAttribute('href', '/planner')
    expect(screen.getByRole('link', { name: /search/i })).toHaveAttribute('href', '/search')
  })

  it('marks only the active link with aria-current', () => {
    mockPathname = '/contacts'
    render(<NavBar />)
    expect(screen.getByRole('link', { name: /people/i })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: /dashboard/i })).not.toHaveAttribute('aria-current')
  })

  it('signs out and redirects to login on click', async () => {
    render(<NavBar />)
    fireEvent.click(screen.getByRole('button', { name: /sign out/i }))
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/login'))
    expect(signOutMock).toHaveBeenCalledTimes(1)
  })

  it('toggles collapsed state and persists it to localStorage', () => {
    render(<NavBar />)
    const toggle = screen.getByRole('button', { name: /collapse sidebar/i })

    fireEvent.click(toggle)

    expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument()
    expect(window.localStorage.getItem('nav-collapsed')).toBe('true')
  })

  it('restores collapsed state from localStorage on mount', () => {
    window.localStorage.setItem('nav-collapsed', 'true')
    render(<NavBar />)
    expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument()
  })

  it('shows the connected user email and initial in the footer', async () => {
    apiFetchMock.mockResolvedValue(
      new Response(JSON.stringify({ graph_me: { mail: 'jane@example.com' } }), { status: 200 })
    )
    render(<NavBar />)
    await waitFor(() => expect(screen.getByText('jane@example.com')).toBeInTheDocument())
    expect(screen.getByText('J')).toBeInTheDocument()
  })

  it('does not fetch graph-status on chrome-hidden paths', () => {
    mockPathname = '/login'
    render(<NavBar />)
    expect(apiFetchMock).not.toHaveBeenCalled()
  })
})

describe('NavBar client-side navigation and labels', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue(new Response(null, { status: 401 }))
    window.localStorage.clear()
    mockPathname = '/dashboard'
  })

  it('navigates client-side instead of reloading the page', () => {
    // Plain <a href> re-downloads and re-executes the whole bundle on every nav
    // click, refetches all data, and makes PageTransition's exit animation and
    // the layoutId shared-element animations dead code - the document is
    // replaced before either can run.
    render(<NavBar />)
    for (const name of [/dashboard/i, /tasks/i, /people/i]) {
      expect(screen.getByRole('link', { name })).toHaveAttribute('data-client-nav', 'true')
    }
  })

  it('uses the product vocabulary from the design', () => {
    render(<NavBar />)
    expect(screen.getByRole('link', { name: /tasks/i })).toHaveAttribute('href', '/planner')
    expect(screen.getByRole('link', { name: /people/i })).toHaveAttribute('href', '/contacts')
  })

  it('offers a settings route for managing connected accounts', () => {
    render(<NavBar />)
    expect(screen.getByRole('link', { name: /settings/i })).toHaveAttribute('href', '/settings')
  })

  it('keeps a nested route highlighted in the sidebar', () => {
    mockPathname = '/contacts/view'
    render(<NavBar />)
    expect(screen.getByRole('link', { name: /people/i })).toHaveAttribute('aria-current', 'page')
  })
})
