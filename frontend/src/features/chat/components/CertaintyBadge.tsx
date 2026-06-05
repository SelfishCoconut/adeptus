import type { Claim } from '@/shared/api'

/**
 * Fallback low-confidence threshold used until the backend value loads. The authoritative
 * value is the backend `ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD`, surfaced on
 * `ChatMessagePage.low_confidence_threshold` and read via `useLowConfidenceThreshold`; this
 * constant is only the default for the optional `threshold` prop.
 */
export const LOW_CONFIDENCE_THRESHOLD = 70

interface CertaintyBadgeProps {
  claim: Claim
  /** Certainty % below which the claim is low-confidence; defaults to the fallback constant. */
  threshold?: number
}

/**
 * One inline certainty badge (§5.3 "uncertainty signaling … in chat"): the AI's flagged
 * claim text followed by its stated certainty percentage. Below the threshold it is
 * amber-flagged as low-confidence; at/above it renders with a subtle neutral affordance.
 * Claim text is verbatim (no redaction, §5.5).
 */
export function CertaintyBadge({ claim, threshold = LOW_CONFIDENCE_THRESHOLD }: CertaintyBadgeProps) {
  const lowConfidence = claim.certainty < threshold
  return (
    <span
      data-testid="certainty-badge"
      data-low-confidence={lowConfidence}
      className={[
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs',
        lowConfidence
          ? 'border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300'
          : 'border-border bg-muted text-muted-foreground',
      ].join(' ')}
    >
      <span className="text-foreground">{claim.text}</span>
      <span className="font-semibold whitespace-nowrap">({claim.certainty}% certain)</span>
    </span>
  )
}
