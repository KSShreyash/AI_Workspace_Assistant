import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DashboardBackdrop } from './DashboardBackdrop'

describe('DashboardBackdrop', () => {
  it('is decorative, so screen readers skip it entirely', () => {
    // It carries no information the page does not already state in text.
    const { container } = render(<DashboardBackdrop />)
    const img = container.querySelector('img')
    expect(img).toHaveAttribute('alt', '')
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true')
  })

  it('never intercepts clicks', () => {
    // It covers the whole viewport; without this it would swallow every click
    // on the page behind it.
    const { container } = render(<DashboardBackdrop />)
    expect(container.firstElementChild?.className).toContain('pointer-events-none')
  })

  it('anchors to the top right, where the artwork has its negative space', () => {
    // The greeting sits on the left; anchoring elsewhere lands it on the cottage.
    const { container } = render(<DashboardBackdrop />)
    expect(container.querySelector('img')?.className).toContain('object-right-top')
  })
})
