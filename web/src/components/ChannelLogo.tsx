import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { cn } from '../lib/cn'

interface ChannelLogoProps {
  id: string
  name: string
  hasLogo: boolean
  size?: number
  className?: string
}

const HUES = [
  214, 252, 180, 330, 10, 150, 42, 290, 120, 200, 340, 60, 270, 100, 20,
]

function hueForName(name: string): number {
  let h = 0
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return HUES[h % HUES.length]
}

function initialsOf(name: string): string {
  const clean = name.replace(/\s*\b(HD|FHD|UHD|4K)\b/gi, '').trim()
  const parts = clean.split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '—'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

export function ChannelLogo({ id, name, hasLogo, size = 40, className }: ChannelLogoProps) {
  const [failed, setFailed] = useState(false)

  // Reset failed state when the logo becomes available (e.g. after warming)
  useEffect(() => {
    if (hasLogo) setFailed(false)
  }, [hasLogo])

  const showImage = hasLogo && !failed

  const hue = hueForName(name)
  const bg = `linear-gradient(135deg, hsl(${hue}deg 60% 28%), hsl(${(hue + 40) % 360}deg 55% 20%))`

  return (
    <div
      className={cn(
        'relative flex shrink-0 items-center justify-center overflow-hidden rounded-lg',
        'ring-1 ring-white/10',
        className,
      )}
      style={{
        width: size,
        height: size,
        background: showImage ? 'rgba(255,255,255,0.03)' : bg,
      }}
    >
      {showImage ? (
        <img
          src={api.logoUrl(id)}
          alt=""
          className="h-full w-full object-contain p-1"
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <span
          className="font-mono font-semibold uppercase tracking-wider text-white/90"
          style={{ fontSize: size < 32 ? '0.55rem' : '0.7rem' }}
        >
          {initialsOf(name)}
        </span>
      )}
    </div>
  )
}
