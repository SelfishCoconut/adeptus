// Display-label maps for the findings wire enums. The wire values are snake_case
// (e.g. `false_positive`, `risk_accepted`) to match the backend StrEnums; the UI
// renders the human-friendly labels below. Keeping this here means the labels live
// in one place and the components stay declarative.
import type { RemediationStatus, Severity, VerificationStatus } from './api'

export const SEVERITY_LABELS: Record<Severity, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  info: 'Info',
}

export const VERIFICATION_LABELS: Record<VerificationStatus, string> = {
  unverified: 'Unverified',
  verified: 'Verified',
  false_positive: 'False positive',
}

export const REMEDIATION_LABELS: Record<RemediationStatus, string> = {
  open: 'Open',
  fixed: 'Fixed',
  risk_accepted: 'Risk accepted',
}

export const SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low', 'info']
export const VERIFICATION_ORDER: VerificationStatus[] = [
  'unverified',
  'verified',
  'false_positive',
]
export const REMEDIATION_ORDER: RemediationStatus[] = ['open', 'fixed', 'risk_accepted']

// Severity → Badge variant (color-coded by Simple severity). Mirrors the graph
// node-type badge mapping so the visual language is consistent.
export const SEVERITY_VARIANT: Record<
  Severity,
  'default' | 'secondary' | 'outline' | 'destructive'
> = {
  critical: 'destructive',
  high: 'destructive',
  medium: 'default',
  low: 'secondary',
  info: 'outline',
}
