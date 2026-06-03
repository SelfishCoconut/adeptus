/**
 * Cross-feature query key factories.
 *
 * Placing shared key factories here prevents silent key divergence when two
 * features share the same TanStack Query cache entry.  Each factory is the
 * single source of truth; both producer and consumer import from this module.
 *
 * Current shared keys:
 *   - toolQueueKey — the heavy-tool concurrency snapshot for an engagement,
 *     consumed by `mcp/api.ts` (useToolQueue) and `engagements/api.ts`
 *     (useEngagementPause invalidation).
 */

/**
 * TanStack Query key for the tool-queue snapshot of a given engagement.
 *
 * Shape: `['mcp', 'tool-queue', engagementId]`
 *
 * Consumers:
 *   - `mcp/api.ts`          — `useToolQueue` query + `useKillToolRun`,
 *                             `useTimeoutDecision` invalidation.
 *   - `engagements/api.ts`  — `useEngagementPause` invalidation.
 */
export function toolQueueKey(engagementId: string): readonly [string, string, string] {
  return ['mcp', 'tool-queue', engagementId] as const
}
