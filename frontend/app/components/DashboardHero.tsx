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
 * The dashboard header, over the painted landscape.
 *
 * Static rather than a layered parallax. The five `layer-*` files in assets/
 * support one, but motion was not wanted here, and shipping 645 KB of layers to
 * animate a header - on a free-tier host that cold-starts - buys nothing the
 * single 67 KB composite does not already give.
 *
 * The artwork has deliberate negative space across its left side, which is
 * where the greeting sits.
 */
export function DashboardHero({ name, now }: DashboardHeroProps) {
  // First name only: a full display name from a work directory can be long
  // enough to wrap the heading onto the artwork.
  const firstName = name?.trim().split(/\s+/)[0] ?? null

  return (
    <div className="relative overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-bg-alt)]">
      {/* A plain <img>, not next/image. next.config sets output: "export" with
          images.unoptimized, so the Image component's optimiser does nothing
          here - it would add runtime for a single static asset already served
          as a 67 KB webp from the CDN. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/hero.webp"
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full object-cover object-right"
      />
      {/* Fades the artwork out under the text so the heading keeps its contrast
          regardless of what sits behind it at a given viewport width. */}
      <div
        aria-hidden="true"
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(to right, var(--color-bg-alt) 0%, var(--color-bg-alt) 38%, transparent 72%)',
        }}
      />

      <div className="relative px-8 py-12 sm:py-16">
        <h1 className="text-3xl font-extrabold tracking-tight text-[var(--color-fg)] sm:text-4xl">
          {greeting(now)}
          {firstName ? (
            <>
              , <span className="text-[var(--color-accent)]">{firstName}</span>.
            </>
          ) : (
            '.'
          )}
        </h1>
        <p className="mt-2 max-w-md text-[var(--color-muted)]">
          Your emails, turned into actionable tasks.
        </p>
      </div>
    </div>
  )
}
