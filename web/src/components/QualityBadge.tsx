import { cn } from '../lib/cn'

export type Quality = 'SD' | 'HD' | '4K'

const STYLES: Record<Quality, string> = {
  SD: 'text-fog-100/40 bg-white/[0.05] border-white/[0.09]',
  HD: 'text-indigo-300 bg-indigo-500/[0.12] border-indigo-400/25',
  '4K': 'text-amber-300 bg-amber-500/[0.12] border-amber-400/25 shadow-[0_0_8px_rgba(251,191,36,0.18)]',
}

interface QualityBadgeProps {
  quality: Quality
  className?: string
}

export function QualityBadge({ quality, className }: QualityBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center rounded border px-[5px] py-[2px]',
        'font-mono text-[9px] font-bold uppercase leading-none tracking-[0.08em]',
        STYLES[quality],
        className,
      )}
    >
      {quality}
    </span>
  )
}

/** Map a raw pixel height to a quality tier. Returns null for unknown (0). */
export function heightToQuality(height: number): Quality | null {
  if (height >= 2160) return '4K'
  if (height >= 720) return 'HD'
  if (height > 0) return 'SD'
  return null
}
