import type { PrivacyMode } from '@/shared/api'
import { PrivacyModeBadge } from './PrivacyModeBadge'

interface PrivacyModeBannerProps {
  privacyMode: PrivacyMode
}

export function PrivacyModeBanner({ privacyMode }: PrivacyModeBannerProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center border-b bg-muted/40 px-4 py-1.5"
    >
      <PrivacyModeBadge privacyMode={privacyMode} />
    </div>
  )
}
