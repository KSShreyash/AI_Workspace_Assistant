import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const signInWithOAuthMock = vi.fn()

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({ auth: { signInWithOAuth: signInWithOAuthMock } }),
}))

import LoginPage from './page'

describe('LoginPage', () => {
  beforeEach(() => {
    signInWithOAuthMock.mockReset()
  })

  it('starts the Microsoft OAuth flow with the required Graph scopes on click', () => {
    render(<LoginPage />)

    fireEvent.click(screen.getByRole('button', { name: /sign in with microsoft/i }))

    expect(signInWithOAuthMock).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: 'azure',
        options: expect.objectContaining({
          scopes: expect.stringContaining('Mail.Read'),
          redirectTo: expect.stringContaining('/callback'),
        }),
      })
    )
  })

  it('lets someone sign in with Google instead', () => {
    // Offering only Microsoft locked out anyone whose mail lives in Gmail -
    // they could not reach the product at all, let alone connect a mailbox.
    render(<LoginPage />)

    fireEvent.click(screen.getByRole('button', { name: /sign in with google/i }))

    expect(signInWithOAuthMock).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: 'google',
        options: expect.objectContaining({
          scopes: expect.stringContaining('gmail.readonly'),
          // Both are required for Google to issue a refresh token at all.
          queryParams: expect.objectContaining({
            access_type: 'offline',
            prompt: 'consent',
          }),
        }),
      })
    )
  })

  it('tells the callback which provider each button authorised', () => {
    render(<LoginPage />)

    fireEvent.click(screen.getByRole('button', { name: /sign in with microsoft/i }))
    fireEvent.click(screen.getByRole('button', { name: /sign in with google/i }))

    const [[microsoft], [google]] = signInWithOAuthMock.mock.calls
    expect(microsoft.options.redirectTo).toContain('provider=azure')
    expect(google.options.redirectTo).toContain('provider=google')
  })

  it('renders the hero headline and feature bullets', () => {
    render(<LoginPage />)

    expect(
      screen.getByRole('heading', { name: /stop losing relationships/i })
    ).toBeInTheDocument()
    expect(screen.getByText(/extracts action items from email & calendar/i)).toBeInTheDocument()
    expect(screen.getByText(/tracks who owes who what/i)).toBeInTheDocument()
    expect(screen.getByText(/books follow-ups directly on your calendar/i)).toBeInTheDocument()
  })
})
