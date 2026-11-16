import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface TabDef {
  value: string
  label: string
}

/** Small accessible tab set (WAI-ARIA tabs pattern: arrow keys, Home/End). Panels are rendered by the caller. */
export function TabList({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: TabDef[]
  value: string
  onChange: (v: string) => void
  label: string
}) {
  const base = useId()
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const move = (e: KeyboardEvent, i: number) => {
    const last = tabs.length - 1
    const next =
      e.key === 'ArrowRight'
        ? i === last
          ? 0
          : i + 1
        : e.key === 'ArrowLeft'
          ? i === 0
            ? last
            : i - 1
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? last
              : null
    if (next === null) return
    e.preventDefault()
    onChange(tabs[next]!.value)
    refs.current[next]?.focus()
  }
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b">
      {tabs.map((t, i) => {
        const active = t.value === value
        return (
          <button
            key={t.value}
            ref={(el) => {
              refs.current[i] = el
            }}
            id={`${base}-${t.value}`}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={`${base}-panel-${t.value}`}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.value)}
            onKeyDown={(e) => move(e, i)}
            className={cn(
              'h-11 shrink-0 border-b-2 px-4 text-sm font-medium whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring sm:h-10',
              active
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

export function TabPanel({
  value,
  active,
  children,
}: {
  value: string
  active: boolean
  children: ReactNode
}) {
  if (!active) return null
  return (
    <div role="tabpanel" aria-label={value} className="space-y-6">
      {children}
    </div>
  )
}
