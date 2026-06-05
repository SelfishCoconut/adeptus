/**
 * Client-side pre-flight mirror of a SUBSET of the backend egress secret-pattern scanner
 * (Slice 14, §5.1) — see `backend/app/features/chat/egress_scan.py`.
 *
 * This is convenience UX only: it decides whether to show the friction modal *before* the
 * POST, so the user is asked before anything leaves the machine. The SERVER is authoritative —
 * it re-scans every cloud_enabled send and returns a 409 regardless of the client (Risk 3), so
 * a client/server pattern drift can never let an unconfirmed secret reach the cloud.
 *
 * Returns matched category NAMES only — never the matched value (§5.5). The names match the
 * backend exactly so the modal labels line up with the server's 409 `matched_categories`.
 */

interface EgressPattern {
  readonly category: string
  readonly pattern: RegExp
}

// A precision-tuned SUBSET of the backend's locked v1 set (slack_token is server-only). Each
// pattern mirrors its Python counterpart; JS uses the `i` flag where Python used `(?i)`.
const PATTERNS: readonly EgressPattern[] = [
  { category: 'aws_access_key', pattern: /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/ },
  { category: 'private_key_block', pattern: /-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----/ },
  { category: 'jwt', pattern: /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/ },
  { category: 'password_assignment', pattern: /\b(?:password|passwd|pwd)\s*[:=]\s*\S+/i },
  {
    category: 'generic_api_key',
    pattern: /\b(?:api[_-]?key|secret|token)\s*[:=]\s*['"]?[A-Za-z0-9_\-./+]{16,}/i,
  },
  { category: 'bearer_token', pattern: /\bBearer\s+[A-Za-z0-9_\-.=+/]{12,}/i },
]

/**
 * Human-readable labels for the friction modal copy. Covers ALL backend categories (including
 * the server-only `slack_token`) so a modal driven by the server's 409 categories renders
 * properly even for a pattern the client does not itself scan. Unknown categories fall back to
 * the raw name (forward-compatible with a future backend addition).
 */
export const EGRESS_CATEGORY_LABELS: Readonly<Record<string, string>> = {
  aws_access_key: 'AWS access key',
  private_key_block: 'private key block',
  jwt: 'JWT',
  password_assignment: 'password= assignment',
  generic_api_key: 'API key / secret',
  bearer_token: 'bearer token',
  slack_token: 'Slack token',
}

/** Map a category name to its human-readable label (falls back to the raw name). */
export function egressCategoryLabel(category: string): string {
  return EGRESS_CATEGORY_LABELS[category] ?? category
}

/**
 * Scan composer text for likely-secret patterns; return the matched category names
 * (deduplicated, in declaration order). Empty when nothing matches. Never returns the matched
 * value (§5.5) — only the category name.
 */
export function scanEgress(content: string): string[] {
  return PATTERNS.filter(({ pattern }) => pattern.test(content)).map(({ category }) => category)
}
