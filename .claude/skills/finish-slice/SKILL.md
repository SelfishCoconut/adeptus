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

9. **Flip the plan status to `done` as part of the slice PR, then push and open it.** Do this *right before* `gh pr create` so the status change rides inside the slice PR — there is NO separate follow-up flip PR (master is protected, so a post-merge edit would otherwise need its own PR just to move one line):
   - In `docs/slices/PROJECT_PLAN.md`, set this slice `Status: in-progress` → `Status: done` and commit it on the slice branch:
     ```
     git commit -am "chore(plan): flip slice NN -> done (#<issue-or-pr>)"
     ```
   - Why `done` and why it's safe to write it before the merge: **master is the source of truth and only reflects this commit once the PR merges**, so on master `done` still means "merged." Carrying the flip inside the slice PR keeps the slice self-contained. `in-review` is no longer written by this skill — it stays a manual-only status for a slice deliberately parked mid-review.
   - Then push and open the PR:
     ```
     git push -u origin slice-NN-<kebab>
     gh pr create --title "Slice NN: <goal>" --body "<generated body>" --label slice
     ```

10. Output to the user:
    - PR URL
    - Code review summary (one paragraph)
    - Security review verdict (if applicable)
    - Drift audit verdict (one line)
    - Status note: "PROJECT_PLAN marks this slice `done` inside the PR; master reflects `done` the moment the PR merges — no follow-up flip PR needed. Until the merge, master still shows the pre-slice status, so pick-next-slice won't unblock dependents built on unmerged code."
    - Suggestion: "After the PR merges, run pick-next-slice for the next one."

## Hard rules
- Never open the PR if the gate is red or reviewers flag Critical findings.
- Never skip the security review on a risky slice.
- Never open the PR before the final drift audit (step 8) has run and its verdict is clean or explicitly accepted by the user.
- Never auto-merge. The human merges.
- Never proceed if the working tree is dirty — every change must be intentional and committed.
- If anything looks off (commits with no associated task, uncommitted scratch files, unexpected file moves), stop and ask the user first.
