type DashboardHeroProps = {
  name: string | null
  now: Date
}

function greeting(now: Date): string {
  const hour = now.getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

/**
 * The dashboard greeting.
 *
 * It used to be a bordered card with the landscape painted inside it, which
 * made the artwork one more boxed element competing with the cards below. The
 * painting is now the page's own ground (see DashboardBackdrop), so the
 * greeting sits directly on it with nothing drawn around it - the only element
 * on the page that is not in a container, which is what makes it read as the
 * top of the page rather than its first card.
 *
 * The artwork keeps its negative space on the left, which is where this sits.
 */
export function DashboardHero({ name, now }: DashboardHeroProps) {
  // First name only: a full display name from a work directory can be long
  // enough to wrap the heading across the artwork.
  const firstName = name?.trim().split(/\s+/)[0] ?? null

  return (
    <header className="max-w-xl pb-2 pt-6 sm:pt-10">
      <h1 className="text-3xl font-semibold tracking-tight text-[var(--color-fg)] sm:text-4xl">
        {greeting(now)}
        {firstName ? `, ${firstName}.` : '.'}
      </h1>
      <p className="mt-2 text-[var(--color-muted)]">Your emails, turned into actionable tasks.</p>
    </header>
  )
}
