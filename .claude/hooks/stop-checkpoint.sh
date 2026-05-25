#!/usr/bin/env bash
# .claude/hooks/stop-checkpoint.sh
# At the end of every Claude turn, append a one-line checkpoint to the
# active slice's ## Progress section. This survives /clear and /compact.

set -o pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
slice_num="$(echo "$branch" | grep -oE '^slice-[0-9]+' | grep -oE '[0-9]+' || true)"
[ -z "$slice_num" ] && exit 0

slice_file="$(ls docs/slices/slice-${slice_num}-*.md 2>/dev/null | head -1)"
[ -z "$slice_file" ] && exit 0

last_commit="$(git log -1 --format='%h %s' 2>/dev/null || echo 'no commits yet')"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if grep -q '^## Progress' "$slice_file"; then
  printf -- '- %s — %s\n' "$ts" "$last_commit" >> "$slice_file"
fi

exit 0
