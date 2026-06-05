import { LOW_CONFIDENCE_THRESHOLD } from './CertaintyBadge'

interface NodeCertaintyBadgeProps {
  /** The latest certainty the AI asserted for this node, or undefined → no badge. */
  certainty?: number
}

/**
 * The thin presentational decorator for the graph-item certainty overlay (§5.3 "on graph
 * items"). Given the latest certainty for a node it renders a small percentage badge
 * (amber below the threshold, neutral at/above); given `undefined` (no claim references the
 * node) it renders nothing. Purely presentational — it reads no store and writes nothing
 * (ADR-0001 / §8.2); the parent supplies the certainty from {@link useCertaintyByNode}.
 */
export function NodeCertaintyBadge({ certainty }: NodeCertaintyBadgeProps) {
  if (certainty === undefined) return null
  const lowConfidence = certainty < LOW_CONFIDENCE_THRESHOLD
  return (
    <span
      data-testid="node-certainty-badge"
      data-low-confidence={lowConfidence}
      title={`AI certainty: ${certainty}%`}
      className={[
        'inline-flex items-center rounded-full border px-1.5 py-0 text-[10px] font-semibold',
        lowConfidence
          ? 'border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300'
          : 'border-border bg-muted text-muted-foreground',
      ].join(' ')}
    >
      {certainty}%
    </span>
  )
}
