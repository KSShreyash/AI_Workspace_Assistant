import { createClient } from './supabase/client'

export function apiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL
  if (!raw) {
    // Inlined at build time, so an unset value is a misconfigured deploy rather
    // than a runtime condition. The non-null assertion this replaces threw
    // "Cannot read properties of undefined (reading 'trim')" on every request,
    // which names neither the variable nor the cause.
    throw new Error(
      'NEXT_PUBLIC_API_BASE_URL is not set. It is inlined at build time, so set it and rebuild.',
    )
  }
  return raw.trim().replace(/\/+$/, '')
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const supabase = createClient()
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token

  const headers = new Headers(init.headers)
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  return fetch(`${apiBaseUrl()}${path}`, { ...init, headers })
}
