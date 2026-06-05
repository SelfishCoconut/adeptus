---
name: pick-next-slice
description: |
  Picks the next vertical slice to work on for Adeptus by reading
  docs/slices/PROJECT_PLAN.md, finding the next slice whose status is
  todo and whose dependencies are all done, and then delegating the
  spec-writing to the slice-planner subagent. Triggers when the user
  asks "what's next", "next slice", "pick next slice", or "what should
  I work on" — particularly after a slice was just merged.
allowed-tools: Read, Grep
---

# Pick the next slice

## Steps

1. Read `docs/slices/PROJECT_PLAN.md`.

2. Build the candidate list: every slice with `Status: todo`.

3. Filter to slices where every entry in `Depends on:` has `Status: done`. Only `done` (i.e. PR merged) satisfies a dependency. A dependency that is `in-review` (PR open but not merged) does NOT count — do not unblock a slice whose dependency is merely in-review.

4. If the candidate list is empty:
   - If there are `in-progress` slices, surface them: one whose slice PR is already open is just awaiting merge (merging it flips it to `done` and may unblock dependents); one with no PR yet still needs implementing/finishing. Suggest the user merge the open PR or finish the work.
   - If any slice is `in-review` (the manual park status), surface it the same way — its PR must merge before it counts as `done`.
   - If there are `blocked` slices and nothing else, list them and surface why.
   - Otherwise: "All slices are done."
   - STOP.

5. If multiple candidates:
   - Prefer the lowest slice number (preserves the planner's ordering).
   - Surface the alternatives so the user knows.

6. Output to the main loop:
   - Selected slice number + name + one-line goal
   - The full PROJECT_PLAN entry for that slice (verbatim)
   - Whether it's marked `risky: yes`
   - Alternatives the user might prefer (if any)
   - Prompt: "Plan it now? (yes → delegates to slice-planner; no → stop)"

7. On `yes`: delegate to the `slice-planner` subagent with the slice number and let it write the full spec.

## Hard rules
- Never write the spec yourself. Always delegate to slice-planner.
- A dependency is satisfied ONLY when its status is `done` (PR merged). `in-review` (PR open, unmerged) does NOT satisfy a dependency — never unblock a slice on top of unmerged code.
- Never modify PROJECT_PLAN.md. Status transitions are owned by start-slice (todo/planned → in-progress) and finish-slice (in-progress → done, committed *inside the slice PR* so master flips to `done` exactly when the PR merges). `in-review` is a manual-only park status (a slice deliberately held open mid-review) and is not part of the automated flow.
- If the user names a different slice (e.g. "actually do slice 15 instead"), surface the dependency status before delegating — if 15's dependencies aren't done, warn but pass the choice to the user.
