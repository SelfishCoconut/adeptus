#!/usr/bin/env bash
# .claude/hooks/session-start.sh
# Print branch + active slice + top of PROJECT_PLAN at session start.
# Output goes to Claude as context for the session.

set -o pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
echo "== Adeptus session =="
echo "Branch: $branch"

if echo "$branch" | grep -qE '^slice-[0-9]+'; then
  slice_num="$(echo "$branch" | grep -oE '[0-9]+' | head -1)"
  slice_file="$(ls docs/slices/slice-${slice_num}-*.md 2>/dev/null | head -1)"
  if [ -n "$slice_file" ]; then
    echo "Active slice spec: $slice_file"
    echo "--- Slice goal ---"
    awk '/^## Goal/{flag=1;next} /^## /{flag=0} flag' "$slice_file" | head -5
  fi
fi

if [ -f docs/slices/PROJECT_PLAN.md ]; then
  echo "--- Project status (top of PROJECT_PLAN.md) ---"
  head -50 docs/slices/PROJECT_PLAN.md
fi

exit 0
