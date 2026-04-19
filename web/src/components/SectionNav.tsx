import { motion } from 'framer-motion'
import { Archive, BookmarkCheck, ListMusic, Sparkles, Sunrise } from 'lucide-react'
import { useI18n } from '../lib/i18n'
import { cn } from '../lib/cn'

export type Section = 'playlist' | 'ai' | 'today' | 'plans' | 'archive'

interface SectionNavProps {
  active: Section
  onChange: (section: Section) => void
}

interface Item {
  id: Section
  icon: React.ComponentType<{ className?: string }>
  labelKey: string
}

const ITEMS: Item[] = [
  { id: 'playlist', icon: ListMusic, labelKey: 'section_playlist' },
  { id: 'ai', icon: Sparkles, labelKey: 'section_ai' },
  { id: 'today', icon: Sunrise, labelKey: 'section_today' },
  { id: 'plans', icon: BookmarkCheck, labelKey: 'section_plans' },
  { id: 'archive', icon: Archive, labelKey: 'section_archive' },
]

export function SectionNav({ active, onChange }: SectionNavProps) {
  const { t } = useI18n()

  return (
    <div className="mx-auto flex w-full max-w-5xl items-center gap-1 px-4 pt-3">
      <div className="glass-strong relative flex w-full items-center gap-1 rounded-full border border-white/10 p-1">
        {ITEMS.map((item) => {
          const Icon = item.icon
          const isActive = active === item.id
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onChange(item.id)}
              aria-label={t(item.labelKey)}
              title={t(item.labelKey)}
              className={cn(
                'relative flex flex-1 items-center justify-center gap-0 rounded-full px-2 py-2 text-[12px] font-medium tracking-tight transition-colors md:gap-2 md:px-3',
                isActive ? 'text-white' : 'text-fog-200/70 hover:text-white',
              )}
            >
              {isActive && (
                <motion.span
                  layoutId="section-nav-active"
                  className="absolute inset-0 rounded-full border border-white/[0.12] bg-white/[0.08]"
                  transition={{ type: 'spring', stiffness: 320, damping: 30 }}
                />
              )}
              <Icon className={cn(
                'relative h-4 w-4 transition-colors',
                isActive ? 'text-[var(--color-indigo-primary)]' : 'opacity-70',
              )} />
              <span className="relative hidden md:inline">{t(item.labelKey)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
