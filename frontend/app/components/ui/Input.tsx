import clsx from 'clsx'

type InputProps = {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  type?: string
  'aria-label'?: string
}

export function Input({
  value,
  onChange,
  placeholder,
  className,
  type = 'text',
  'aria-label': ariaLabel,
}: InputProps) {
  return (
    <input
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      aria-label={ariaLabel}
      className={clsx(
        'w-full rounded-[var(--radius-control)] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5 text-sm text-[var(--color-fg)] placeholder-[var(--color-muted)] transition focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/30',
        className
      )}
    />
  )
}
