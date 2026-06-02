#!/usr/bin/env bash
# .claude/hooks/stop-typecheck.sh
# Slow, import-graph-aware checks run ONCE per turn (Stop hook), not per edit:
#   backend  -> mypy over the package (needs the whole import graph to be correct)
#   frontend -> eslint + tsc --noEmit over the project
# These are too slow and/or inaccurate to run per-file in post-edit-format.sh.
# Non-blocking: always exits 0 and writes a concise summary to stderr so Claude
# self-corrects on the next turn. No-ops quickly when nothing has changed.

set -o pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

# Drain stdin (Stop hook receives a JSON payload we don't need).
cat >/dev/null 2>&1 || true

# What changed in the working tree this turn? Cheap gate — if nothing relevant
# is dirty, exit immediately without spinning up mypy/tsc.
changed="$(git status --porcelain 2>/dev/null | sed -E 's/^...//')"
[ -z "$changed" ] && exit 0

py_changed=0
fe_changed=0
while IFS= read -r path; do
  case "$path" in
    backend/*.py) py_changed=1 ;;
    frontend/*.ts|frontend/*.tsx|frontend/*.js|frontend/*.jsx) fe_changed=1 ;;
  esac
done <<EOF
$changed
EOF

[ "$py_changed" -eq 0 ] && [ "$fe_changed" -eq 0 ] && exit 0

if [ "$py_changed" -eq 1 ] && [ -d backend ] && command -v mypy >/dev/null 2>&1; then
  if ! mypy_out="$(cd backend && mypy app 2>&1)"; then
    echo "stop-typecheck: mypy reported errors in backend/app:" >&2
    echo "$mypy_out" >&2
  fi
fi

if [ "$fe_changed" -eq 1 ] && [ -d frontend ]; then
  if ! eslint_out="$(cd frontend && npx --no-install eslint src 2>&1)"; then
    echo "stop-typecheck: eslint reported errors in frontend/src:" >&2
    echo "$eslint_out" >&2
  fi
  if ! tsc_out="$(cd frontend && npx --no-install tsc --noEmit 2>&1)"; then
    echo "stop-typecheck: tsc --noEmit reported errors in frontend:" >&2
    echo "$tsc_out" >&2
  fi
fi

exit 0
