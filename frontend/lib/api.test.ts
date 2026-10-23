import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

const getSessionMock = vi.fn()

vi.mock('./supabase/client', () => ({
  createClient: () => ({ auth: { getSession: getSessionMock } }),
}))

import { apiBaseUrl, apiFetch } from './api'

describe('apiFetch', () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = 'https://api.example.com'
    getSessionMock.mockReset()
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 200 }))
  })

  it('attaches the Supabase access token as a Bearer header', async () => {
    getSessionMock.mockResolvedValue({ data: { session: { access_token: 'jwt-123' } } })

    await apiFetch('/api/me/graph-status')

    const call = (global.fetch as unknown as Mock).mock.calls[0]
    expect(call[0]).toBe('https://api.example.com/api/me/graph-status')
    const headers: Headers = call[1].headers
    expect(headers.get('Authorization')).toBe('Bearer jwt-123')
  })

  it('omits the Authorization header when there is no session', async () => {
    getSessionMock.mockResolvedValue({ data: { session: null } })

    await apiFetch('/api/me/graph-status')

    const call = (global.fetch as unknown as Mock).mock.calls[0]
    const headers: Headers = call[1].headers
    expect(headers.get('Authorization')).toBeNull()
  })

  it('strips stray whitespace and a trailing slash from the configured base URL', () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = '  https://api.example.com/  '

    expect(apiBaseUrl()).toBe('https://api.example.com')
  })

  it('names the missing variable rather than throwing on undefined', () => {
    // NEXT_PUBLIC_* values are inlined at build time, so an unset one is a
    // misconfigured deploy. The non-null assertion this replaces produced
    // "Cannot read properties of undefined (reading 'trim')" on every request,
    // naming neither the variable nor the cause.
    const saved = process.env.NEXT_PUBLIC_API_BASE_URL
    delete process.env.NEXT_PUBLIC_API_BASE_URL
    try {
      expect(() => apiBaseUrl()).toThrowError(/NEXT_PUBLIC_API_BASE_URL/)
    } finally {
      process.env.NEXT_PUBLIC_API_BASE_URL = saved
    }
  })
})
