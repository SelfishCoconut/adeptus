---
name: finish-slice
description: |
  Wraps up an Adeptus slice. Runs the full test gate (lint + typecheck +
  tests + coverage), invokes code-reviewer and (for risky slices)
  security-reviewer, generates a PR body from the slice spec, opens the
  pull request, marks the slice in-review in PROJECT_PLAN.md, and closes the
  GitHub Issue. Use when the user says "finish slice", "ship it", "wrap
  up", "open PR", or when next-task-in-slice reports all tasks complete.
allowed-tools: Read, Bash, Grep, Edit
---

# Finish a slice

## Steps

1. Confirm we're on a slice branch and the working tree is clean. If dirty, STOP.

2. Run the full local gate:
   ```
   make lint && make test
   ```
   If anything fails, surface the failure verbatim and STOP. Do not proceed to review until green.

3. Confirm coverage:
   - Backend: ≥80% on `app/features/<this-slice's-feature>`
   - Frontend: ≥60% on `src/features/<this-slice's-feature>`
   - If below, suggest delegating to `test-writer` agent before proceeding.

4. Delegate to `code-reviewer` subagent:
   - Goal: review the slice diff against CLAUDE.md and the slice spec
   - Return: structured findings + verdict (ready / fix needed)

5. If verdict = "fix needed" with Critical or multiple Warning findings: STOP, surface findings to user, return them to the implementer in the main loop. Do not open the PR.

6. If the slice is marked `risky: yes` in PROJECT_PLAN, delegate to `security-reviewer` subagent. Same gating: BLOCK MERGE = stop; MERGE WITH FIXES = surface and stop until human resolves.

7. Generate PR body from the slice spec:
   ```
   ## What
   <Goal section from slice spec>

   ## Why
   <Requirements traceability section>

   ## How
   <Summarized backend + frontend tasks>

   ## Test plan
   <Test plan section verbatim>

   ## Demo
   <Acceptance criteria section verbatim>

   ## Reviewers
   - Code review: ✓ <link to review summary>
   - Security review: <✓ or N/A>
   - Drift audit: <verdict from step 8>

   Closes #<github-issue-number>
   ```

8. **Final drift audit — the last gate before any PR is opened.** Invoke the `audit-drift` skill to compare the slice's changes against the source-of-truth docs (slice spec, PROJECT_PLAN.md, requirements.md, architecture.md, the ADRs, CLAUDE.md), run the mechanical gates, and sweep for anti-patterns / structural violations. Read its Drift & Health Report verdict and gate on it:
   - If the audit flags **code that strayed from the plan** (anti-patterns, structural violations, gate failures): STOP, surface the report to the user, and return findings to the implementer in the main loop. Do not push or open the PR.
   - If the audit flags **a plan that no longer matches reality** (drift better mended via an ADR / slice-planner than a code fix): surface it and ask the user how to proceed before opening the PR.
   - Only when the verdict is clean — or the user explicitly accepts the noted drift — proceed. Capture the verdict for the PR body's Reviewers line.

9. Push and open PR:
   ```
   git push -u origin slice-NN-<kebab>
   gh pr create --title "Slice NN: <goal>" --body "<generated body>" --label slice
   ```

10. Update PROJECT_PLAN.md: `Status: in-progress` → `Status: in-review`. The PR is open but NOT merged, so the slice is NOT `done` yet. `in-review` deliberately does not satisfy dependencies, so pick-next-slice won't unblock dependents built on unmerged code. The slice becomes `done` only after the human merges the PR (mark it then — e.g. on the next pick-next-slice, confirm the PR merged and flip in-review → done).

11. Output to the user:
    - PR URL
    - Code review summary (one paragraph)
    - Security review verdict (if applicable)
    - Drift audit verdict (one line)
    - Status note: "Slice is now `in-review` (PR open, not merged). It won't unblock dependents until it's `done`."
    - Suggestion: "After the PR merges, flip this slice to `done` in PROJECT_PLAN.md, then run pick-next-slice for the next one."

## Hard rules
- Never open the PR if the gate is red or reviewers flag Critical findings.
- Never skip the security review on a risky slice.
- Never open the PR before the final drift audit (step 8) has run and its verdict is clean or explicitly accepted by the user.
- Never auto-merge. The human merges.
- Never proceed if the working tree is dirty — every change must be intentional and committed.
- If anything looks off (commits with no associated task, uncommitted scratch files, unexpected file moves), stop and ask the user first.
