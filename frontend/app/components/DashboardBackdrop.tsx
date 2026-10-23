/**
 * The painted landscape, as the dashboard's ground rather than a panel on it.
 *
 * Shown at its own proportions across the top of the content column and
 * dissolved into the page colour, rather than stretched to cover the viewport.
 * Covering it would crop a wide landscape to a tall box, zooming into a corner
 * of the painting and showing less of it, not more.
 *
 * The scrim is light at the top so the artwork actually reads, and reaches the
 * flat page colour before the stat tiles begin. Cards are opaque, so every
 * figure keeps full contrast regardless of what sits behind it.
 *
 * Anchored right: the artwork's negative space is on the left, which is where
 * the greeting sits.
 *
 * aria-hidden and pointer-events-none - atmosphere, carrying no information and
 * intercepting no clicks.
 */
export function DashboardBackdrop() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[46rem] overflow-hidden"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/hero.webp"
        alt=""
        className="h-full w-full object-cover object-right-top"
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(to bottom, ' +
            'rgba(251, 248, 242, 0.08) 0%, ' +
            'rgba(251, 248, 242, 0.30) 30%, ' +
            'rgba(251, 248, 242, 0.62) 55%, ' +
            'rgba(251, 248, 242, 0.88) 78%, ' +
            'var(--color-bg) 100%)',
        }}
      />
    </div>
  )
}
