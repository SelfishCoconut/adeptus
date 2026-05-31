import { Cloud, ShieldCheck } from 'lucide-react'
import type { PrivacyMode } from '@/shared/api'

interface PrivacyModeBadgeProps {
  privacyMode: PrivacyMode
}

export function PrivacyModeBadge({ privacyMode }: PrivacyModeBadgeProps) {
  if (privacyMode === 'cloud_enabled') {
    return (
      <span
        role="status"
        className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-3 py-1 text-sm font-medium text-amber-800 dark:bg-amber-900/30 dark:text-amber-300"
      >
        <Cloud className="size-4" aria-hidden="true" />
        Cloud enabled — data may leave the local network
      </span>
    )
  }

  return (
    <span
      role="status"
      className="inline-flex items-center gap-1.5 rounded-full bg-green-100 px-3 py-1 text-sm font-medium text-green-800 dark:bg-green-900/30 dark:text-green-300"
    >
      <ShieldCheck className="size-4" aria-hidden="true" />
      Local only — no data leaves the local network
    </span>
  )
}
