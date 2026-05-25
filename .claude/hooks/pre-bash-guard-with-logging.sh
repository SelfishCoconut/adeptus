#!/usr/bin/env bash
# .claude/hooks/pre-bash-guard-with-logging.sh
#
# Wraps pre-bash-guard.sh — runs the guard, and if it blocks (exit 2),
# also writes a [BLOCKED] entry to docs/logs/problems.log before exiting.
# This replaces pre-bash-guard.sh in settings.json as the PreToolUse hook.
#
# By wrapping rather than merging, the guard logic stays in one place.

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${CLAUDE_PROJECT_DIR:-.}/docs/logs"
LOG_FILE="$LOG_DIR/problems.log"

# Read stdin once — we need to pass it to the guard AND parse it ourselves
payload="$(cat)"

# Run the guard
guard_stderr="$(echo "$payload" | bash "$SCRIPT_DIR/pre-bash-guard.sh" 2>&1)"
guard_exit=$?

if [ "$guard_exit" = "2" ] && [ -f "$LOG_FILE" ]; then
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  branch="$(git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"

  cmd="$(echo "$payload" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('tool_input', {}).get('command', '')[:300].replace('\n', ' '))
except Exception:
    pass
" 2>/dev/null || echo "(parse error)")"

  reason="$(echo "$guard_stderr" | head -1 | cut -c1-120)"

  {
    printf '[BLOCKED]  %s  branch=%s  type=bash-guard\n' "$ts" "$branch"
    printf '           cmd: %s\n' "$cmd"
    printf '           reason: %s\n' "$reason"
  } >> "$LOG_FILE"

  # Re-emit the guard's stderr so Claude still sees the explanation
  echo "$guard_stderr" >&2
fi

exit "$guard_exit"
