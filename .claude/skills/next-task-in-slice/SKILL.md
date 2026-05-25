---
name: next-task-in-slice
description: |
  Determines the next concrete task to execute within the currently active
  Adeptus slice. Reads the active slice spec and the git log on the slice
  branch, identifies which ordered tasks are complete and which remains
  next, and returns the next task description ready to hand to the
  implementer subagent. Triggers when the user asks "next step", "what
  now", "next task", or after a commit lands on the slice branch.
allowed-tools: Read, Bash, Grep
---

# Next task in slice

## Steps

1. Confirm we're on a slice branch: `git rev-parse --abbrev-ref HEAD` matches `^slice-NN-`. If not, suggest the user run `start-slice` first.

2. Read the active slice spec at `docs/slices/slice-NN-*.md`.

3. Read the git log for the slice branch: `git log main..HEAD --oneline`.

4. Match commits to tasks in the slice spec's "Backend tasks" and "Frontend tasks" lists. A task is considered done if any commit message references it (look for `slice-NN: <task name>` or related keywords).

5. Determine the next task:
   - Prefer backend tasks if frontend hasn't been started — the contract-first build order means schemas exist first.
   - If a task has explicit dependencies inside the slice, respect them.
   - If the next backend and frontend tasks are independent (have the OpenAPI contract already), surface both as "could be done in parallel — pick one."

6. Output to the main loop:
   - Slice number + name
   - Tasks completed so far (bulleted with commit hash)
   - The next task — verbatim from the slice spec — with its complexity estimate
   - Files likely to be touched (your best guess from the task description)
   - Suggested command: "Delegate to implementer? (yes / let me think)"

7. On `yes`: delegate to the `implementer` subagent with the task description, file scope, and slice number.

## Hard rules
- Never invent a task that isn't in the slice spec. If the user wants to do something extra, tell them to amend the slice spec first.
- Never mark tasks done in the spec yourself — the implementer's commit IS the marker.
- If all tasks appear complete based on commits, suggest the user run `finish-slice` instead.
- If you can't determine the next task confidently (ambiguous commits, conflicting signals), ask the user.
