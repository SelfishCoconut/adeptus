---
name: compact-handoff
description: |
  Prepares Adeptus's working context for /compact or /clear without
  losing project state. Writes a structured progress summary to the
  active slice's spec file, including decisions made, files touched,
  next intended action, and any open questions. Use when the user
  signals "context is getting full", "compact now", "save state",
  or before any long session break.
allowed-tools: Read, Edit, Bash
---

# Compact handoff

## Steps

1. Confirm we're on a slice branch. If on `main`, there's nothing per-slice to save — write a one-line note to `docs/runbooks/handoffs.md` instead and stop.

2. Open the active slice spec at `docs/slices/slice-NN-*.md`.

3. Append a new entry under `## Progress` (create the section if missing):
   ```markdown
   ### Checkpoint — <ISO timestamp>
   - **Status**: <one line — what you were doing>
   - **Tasks done**: <bullets — match against the slice's task list>
   - **Tasks remaining**: <bullets>
   - **Next intended action**: <one paragraph — be specific>
   - **Decisions made this session** (not yet in ADRs):
     - ...
   - **Open questions for the human**:
     - ...
   - **Files touched in this session** (uncommitted or recently committed):
     - <bullets — `git log main..HEAD --name-only --oneline`>
   - **Gotchas to remember**:
     - <anything weird discovered: a library quirk, a flaky test, a workaround>
   ```

4. Commit the slice spec update:
   ```
   git add docs/slices/slice-NN-*.md
   git commit -m "chore(slice-NN): checkpoint progress"
   ```

5. Tell the user to run `/compact` (preserving decisions and file paths) or `/clear`. Pre-format the recommended `/compact` instructions:
   > "When summarizing, preserve: the slice spec path, the next intended action, any TODO items, and any library API gotchas surfaced this session. Summarize aggressively for exploration and dead ends."

## Hard rules
- Never overwrite existing Progress entries. Always append.
- Never invent decisions. If you're not sure what was decided this session, leave it out and surface the uncertainty as an open question.
- Never compact during a half-edited file. Confirm `git status` shows either a clean tree or only intentional changes before suggesting compact.
- If the slice has clearly stalled (no commits in N turns, lots of dead-end exploration), surface that to the user with a recommendation: "Want to abandon this slice and re-plan, or push through?"
