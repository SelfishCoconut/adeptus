---
name: switch-task
description: |
  Cleanly switches between unrelated tasks in Adeptus by preserving the
  current slice's state and then clearing context. Use whenever the user
  wants to start working on something unrelated to the current conversation
  — a different slice, a different feature, debugging vs implementation,
  exploration vs coding. Triggers on phrases like "switch to", "now let's",
  "let's do X instead", "change topic", "new task", "different task".
allowed-tools: Read, Bash, Edit
---

# Switch task cleanly

## Steps

1. Detect the current state:
   - Run `git rev-parse --abbrev-ref HEAD` to get the branch.
   - If on a slice branch (matches `^slice-NN-`), there is in-flight slice
     state to preserve. Proceed to step 2.
   - If on `main`, there is no slice state to preserve. Skip to step 4.

2. Run the `compact-handoff` skill's steps inline:
   - Open the active slice spec at `docs/slices/slice-NN-*.md`.
   - Append a checkpoint to its `## Progress` section recording:
     - timestamp
     - what was just being worked on
     - next intended action when the user returns to this slice
     - any open questions or gotchas surfaced this session
   - Commit the slice spec update with message
     `chore(slice-NN): checkpoint before task switch`.

3. Confirm the working tree is clean:
   - Run `git status --porcelain`.
   - If dirty, STOP and tell the user: "Uncommitted changes on the slice
     branch. Commit or stash before switching tasks." Do NOT clear.

4. Tell the user exactly:
   > "State preserved. Type `/clear` now. After clearing, describe the new
   > task and I'll start fresh — I will not reference anything from this
   > conversation."

5. STOP. Do not run `/clear` yourself (only the user can run slash commands).
   Do not continue to the new task in the same conversation — the whole
   point is the clear boundary.

## Hard rules
- Never skip the checkpoint step when on a slice branch — losing in-flight
  state is the failure mode this skill exists to prevent.
- Never proceed with the new task in the same conversation. The clear is
  not optional.
- Never clear if the working tree is dirty — protect uncommitted work first.
- If the user resists clearing ("just do it without clearing"), warn once:
  "Carrying context from the previous task increases hallucination risk."
  Then comply if they insist — they own the tradeoff.
