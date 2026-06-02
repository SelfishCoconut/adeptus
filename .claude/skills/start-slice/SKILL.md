---
name: start-slice
description: |
  Starts work on an approved Adeptus slice. Loads the slice spec, creates
  the git branch, opens a mirror GitHub Issue, and asks for explicit human
  approval of the plan before any code is written. Use after pick-next-slice
  has produced an approved spec, or when the user says "start slice N",
  "let's begin slice X", "execute the plan", or similar.
allowed-tools: Read, Bash
---

# Start a slice (plan-gated)

## Steps

1. Confirm the slice spec exists at `docs/slices/slice-NN-*.md`. If not, STOP and tell the user to run `pick-next-slice` first.

2. Confirm the working tree is clean: `git status --porcelain`. If dirty, STOP — tell the user to commit or stash.

3. Confirm we're on `main` and up to date: `git checkout main && git pull --ff-only`.

4. Create the slice branch: `git checkout -b slice-NN-<kebab>` (use the kebab from the slice filename).

5. Update PROJECT_PLAN.md: this slice's `Status: planned` → `Status: in-progress`.

6. Open a GitHub Issue mirroring the slice spec:
   ```
   gh issue create \
     --title "Slice NN: <slice goal>" \
     --body-file docs/slices/slice-NN-*.md \
     --label "slice"
   ```
   Record the issue number in the slice spec's "GitHub Issue" line (add the line if missing).

7. Print the slice's Goal, User-visible demo, and Plan sections to the user. Stop. Ask exactly:
   > "Approve this plan? (y / edit / N)"

8. Wait for the response:
   - `y` → go to step 9 (hand the clear back to the human).
   - `edit` → list the slice spec path, stop, wait for user to edit and re-confirm.
   - `N` or no clear answer → revert: undo the branch (`git checkout main && git branch -D slice-NN-...`), revert PROJECT_PLAN.md status, close the GitHub Issue with a comment "Plan not approved — slice deferred". Then STOP.

9. After approval, hand the clear back to the human. A skill cannot run `/clear` — it is a user action. Print exactly:
   > "Plan approved and branch `slice-NN-<kebab>` is ready. For context hygiene between slices, please run `/clear` now, then say **go**. On the next turn I'll re-orient from the slice branch and spec and delegate the first task to the implementer."
   Then STOP and wait. Do not write any code in this turn.

10. On the next turn (after the human has cleared and said "go"), re-orient: read the current branch (`git branch --show-current`), read the matching `docs/slices/slice-NN-*.md` spec, then delegate the first ordered task to the `implementer` subagent.

## Hard rules
- Never write code before the human types `y` AND has cleared context and said "go".
- The skill does NOT clear context itself and does NOT rely on any hook re-injecting context on `/clear`. The clear is a human action handed back in step 9; re-orientation in step 10 is done explicitly by reading the branch + spec.
- Never combine multiple slices in one branch. One branch = one slice = one PR.
- If the slice is marked `risky: yes` in PROJECT_PLAN, add an extra line to the approval prompt: "This slice is RISKY (security-sensitive). It will require security-reviewer at finish-slice time. Confirm?"
