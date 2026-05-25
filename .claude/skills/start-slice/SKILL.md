---
name: start-slice
description: |
  Starts work on an approved Adeptus slice. Loads the slice spec, resets
  Claude's context, creates the git branch, opens a mirror GitHub Issue,
  and asks for explicit human approval of the plan before any code is
  written. Use after pick-next-slice has produced an approved spec, or
  when the user says "start slice N", "let's begin slice X", "execute
  the plan", or similar.
allowed-tools: Read, Bash
---

# Start a slice (plan-gated)

## Steps

1. Confirm the slice spec exists at `docs/slices/slice-NN-*.md`. If not, STOP and tell the user to run `pick-next-slice` first.

2. Confirm the working tree is clean: `git status --porcelain`. If dirty, STOP — tell the user to commit or stash.

3. Confirm we're on `main` and up to date: `git checkout main && git pull --ff-only`.

4. Run `/clear` to drop accumulated context from the previous slice. This is non-negotiable.

5. After clear, the SessionStart hook re-injects CLAUDE.md and the slice spec via branch detection. We'll create the branch next so the hook fires correctly on the next turn.

6. Create the slice branch: `git checkout -b slice-NN-<kebab>` (use the kebab from the slice filename).

7. Update PROJECT_PLAN.md: this slice's `Status: planned` → `Status: in-progress`.

8. Open a GitHub Issue mirroring the slice spec:
   ```
   gh issue create \
     --title "Slice NN: <slice goal>" \
     --body-file docs/slices/slice-NN-*.md \
     --label "slice"
   ```
   Record the issue number in the slice spec's "GitHub Issue" line (add the line if missing).

9. Print the slice's Goal, User-visible demo, and Plan sections to the user. Stop. Ask exactly:
   > "Approve this plan? (y / edit / N)"

10. Wait for the response:
    - `y` → delegate the first ordered task to the `implementer` subagent
    - `edit` → list the slice spec path, stop, wait for user to edit and re-confirm
    - `N` or no clear answer → revert: undo the branch (`git checkout main && git branch -D slice-NN-...`), revert PROJECT_PLAN.md status, close the GitHub Issue with a comment "Plan not approved — slice deferred"

## Hard rules
- Never write code before the human types `y`.
- Never skip the `/clear` — context hygiene between slices is the whole point of plan-gated workflow.
- Never combine multiple slices in one branch. One branch = one slice = one PR.
- If the slice is marked `risky: yes` in PROJECT_PLAN, add an extra line to the approval prompt: "This slice is RISKY (security-sensitive). It will require security-reviewer at finish-slice time. Confirm?"
