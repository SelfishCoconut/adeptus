import { cn } from '@/lib/utils'
import { useHealth } from './api'

export function HealthIndicator() {
  const health = useHealth()
  const healthy = health.isSuccess

  return (
    <span
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 text-sm text-muted-foreground"
    >
      <span
        aria-label={healthy ? 'Backend reachable' : 'Backend unreachable'}
        className={cn('size-2.5 rounded-full', healthy ? 'bg-green-500' : 'bg-red-500')}
      />
      {healthy ? 'Connected' : 'Disconnected'}
    </span>
  )
}
