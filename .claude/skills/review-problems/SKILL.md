---
name: review-problems
description: |
  Reviews the Adeptus problem log at docs/logs/problems.log. Reads all
  OPEN, BLOCKED, and RESOLVED entries, groups them by slice and type,
  identifies recurring patterns, highlights any problems still unresolved,
  and produces a structured summary. Use when the user asks "show me
  problems from this slice", "any recurring errors", "what broke during
  slice N", "problem summary", or "review the log".
allowed-tools: Read, Bash, Grep
---

# Review the problem log

## Steps

1. Read `docs/logs/problems.log` in full.

2. Parse all entries into four groups:
   - **OPEN** — failure logged, no matching RESOLVED entry yet
   - **RESOLVED** — OPEN entry has a matching RESOLVED (match by timestamp in `problem=<ts>`)
   - **BLOCKED** — bash-guard blocked a command
   - **NOTE** — informational annotations

3. Determine the scope from what the user asked:
   - "this slice" or "slice N" → filter by `branch=slice-NN-*`
   - "all" or no qualifier → include everything
   - "unresolved" → only OPEN entries with no matching RESOLVED

4. For each resolved problem, locate the detail file at `docs/logs/details/<open-ts>.txt` if it exists and read it to get the full error context for the summary.

5. Build the summary using this structure:

   ```
   ## Problem log summary — <scope> — <date range>

   ### Still open (<N>)
   For each:
   - [OPEN <ts>]  <branch>  type=<type>
     Command: <cmd>
     Error:   <first error line>
     Detail:  <path to detail file>

   ### Resolved this scope (<N>)
   For each:
   - [OPEN <ts>] → [RESOLVED <ts>]  (<elapsed time>)
     What broke:  <error>
     Fix commit:  <hash> — <message>
     How fixed:   <how line>

   ### Blocked commands (<N>)
   For each:
   - [BLOCKED <ts>]  <branch>
     Command: <cmd>
     Reason:  <guard rule>

   ### Patterns
   (Analyse across all entries and flag recurring themes)
   - e.g. "pytest failed 4 times in slice-07 before passing — all in test_writer.py"
   - e.g. "3 attempts to run nmap against external hosts (all blocked by guard)"
   - e.g. "ruff lint failures always in the same module — consider fixing ruff config"

   ### Recommendations
   (1-3 concrete actions based on patterns)
   ```

6. If the user asked specifically about how a problem was solved, locate the
   RESOLVED entry for it, read the detail file for full context, and
   describe the fix in plain prose — not just the commit message, but what
   the root cause was based on the error and the fix.

## Hard rules
- Never modify `problems.log` or any detail file. Read only.
- Never invent entries. If an entry doesn't exist in the log, say so.
- If problems.log is empty or has only the header comment, say "No problems
  logged yet" — don't produce a summary with empty sections.
- When reporting elapsed time for resolutions, compute it from the two
  ISO timestamps (open vs resolved). Format as "Xm" or "Xh Ym".
- Flag any OPEN entry older than 2 hours as a "stale open problem" —
  it may have been silently worked around without a proper resolution entry.
