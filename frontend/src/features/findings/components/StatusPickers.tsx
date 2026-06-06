// StatusPickers — two inline <select>s (verification + remediation) shown on each
// findings row. Changing a picker fires the matching mutation. There is no local
// state: while a mutation is pending the picker shows the optimistic value (from
// the mutation variables); on error it reverts to the server value (isPending is
// false again → server value) and an inline alert is shown. Free transitions are
// allowed (Decision 3), so every option is always selectable.
import { useSetRemediation, useSetVerification } from '../api'
import type { Finding, RemediationStatus, VerificationStatus } from '../api'
import {
  REMEDIATION_LABELS,
  REMEDIATION_ORDER,
  VERIFICATION_LABELS,
  VERIFICATION_ORDER,
} from '../findingsLabels'
import { SELECT_CLASS_COMPACT } from '../selectClass'

export interface StatusPickersProps {
  engagementId: string
  finding: Finding
}

export function StatusPickers({ engagementId, finding }: StatusPickersProps) {
  const verification = useSetVerification(engagementId)
  const remediation = useSetRemediation(engagementId)

  // Optimistic-while-pending, revert-on-error: derive the displayed value from the
  // in-flight mutation variables, falling back to the server value.
  const verificationValue =
    verification.isPending && verification.variables?.findingId === finding.id
      ? verification.variables.verification_status
      : finding.verification_status
  const remediationValue =
    remediation.isPending && remediation.variables?.findingId === finding.id
      ? remediation.variables.remediation_status
      : finding.remediation_status

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <select
          aria-label="Verification status"
          className={SELECT_CLASS_COMPACT}
          value={verificationValue}
          disabled={verification.isPending}
          onChange={(e) =>
            verification.mutate({
              findingId: finding.id,
              verification_status: e.target.value as VerificationStatus,
            })
          }
        >
          {VERIFICATION_ORDER.map((v) => (
            <option key={v} value={v}>
              {VERIFICATION_LABELS[v]}
            </option>
          ))}
        </select>
        <select
          aria-label="Remediation status"
          className={SELECT_CLASS_COMPACT}
          value={remediationValue}
          disabled={remediation.isPending}
          onChange={(e) =>
            remediation.mutate({
              findingId: finding.id,
              remediation_status: e.target.value as RemediationStatus,
            })
          }
        >
          {REMEDIATION_ORDER.map((r) => (
            <option key={r} value={r}>
              {REMEDIATION_LABELS[r]}
            </option>
          ))}
        </select>
      </div>
      {(verification.isError || remediation.isError) && (
        <p role="alert" className="text-xs text-destructive">
          {verification.error?.message ?? remediation.error?.message}
        </p>
      )}
    </div>
  )
}
