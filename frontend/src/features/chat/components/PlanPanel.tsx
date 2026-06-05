import type { PlanStep, PlanStepStatus } from '@/shared/api'

interface PlanPanelProps {
  /** The AI's running plan for a turn (§5.3 visible plan). Empty → a subtle no-plan note. */
  plan: PlanStep[]
}

/** Human label per status (also the accessible text for the colored dot). */
const STATUS_LABEL: Record<PlanStepStatus, string> = {
  todo: 'To do',
  in_progress: 'In progress',
  done: 'Done',
}

/** The status dot styling — outlined for todo, amber in-progress, emerald done. */
const STATUS_DOT: Record<PlanStepStatus, string> = {
  todo: 'border border-muted-foreground/60',
  in_progress: 'bg-amber-500',
  done: 'bg-emerald-500',
}

/**
 * The inline Plan panel (§5.3 "visible plan"): a read-only, per-turn rendering of the AI's
 * own ordered todo list with a status affordance per step. It is explicitly NOT an
 * actionable task board — there are no edit/check-off/reorder controls (§11.6 forbids a
 * separate kanban / task queue). Pure presentational; the parent owns which turn's plan to
 * show. Step text is rendered verbatim (no redaction, §5.5).
 */
export function PlanPanel({ plan }: PlanPanelProps) {
  if (plan.length === 0) {
    return (
      <p data-testid="plan-panel-empty" className="text-xs italic text-muted-foreground">
        No plan for this turn.
      </p>
    )
  }

  return (
    <section
      aria-label="AI plan"
      data-testid="plan-panel"
      className="space-y-1 rounded-md border bg-card p-2"
    >
      <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Plan
      </h4>
      <ol className="space-y-1">
        {plan.map((step, index) => (
          <li
            key={`${index}:${step.step}`}
            data-status={step.status}
            className="flex items-start gap-2 text-xs"
          >
            <span
              aria-hidden="true"
              className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[step.status]}`}
            />
            <span
              className={
                step.status === 'done'
                  ? 'text-muted-foreground line-through'
                  : 'text-foreground'
              }
            >
              {step.step}
            </span>
            <span className="sr-only">{STATUS_LABEL[step.status]}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}
