#!/usr/bin/env bash
# .claude/hooks/post-edit-format.sh
# Fast per-edit formatter: runs only cheap, file-local checks on the edited file.
#   *.py                         -> ruff format + ruff check
#   *.ts/tsx/js/jsx              -> prettier --write
#   *.md/json/yml/yaml           -> prettier --write
# The slow, import-graph-aware checks (mypy, eslint, tsc) live in
# stop-typecheck.sh and run once per turn — never per edit.
# Non-blocking: writes summary to stderr so Claude self-corrects on next turn.
# Uses Python stdlib for JSON parsing (no jq dependency).

set -o pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

payload="$(cat)"

if command -v python3 >/dev/null 2>&1; then
  file="$(echo "$payload" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input', {})
    print(ti.get('file_path') or ti.get('path') or '')
except Exception:
    pass
" 2>/dev/null || echo "")"
else
  file="$(echo "$payload" | grep -oE '"(file_path|path)"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')"
fi

[ -z "$file" ] && exit 0
[ ! -f "$file" ] && exit 0

# Skip generated and vendored
case "$file" in
  */node_modules/*|*/.venv/*|*/dist/*|*/build/*|*/.next/*|*/__pycache__/*)
    exit 0 ;;
esac

case "$file" in
  *.py)
    if command -v ruff >/dev/null 2>&1; then
      ruff format "$file" 2>/dev/null || true
      if ! lint_out="$(ruff check "$file" 2>&1)"; then
        echo "Ruff lint failed on $file:" >&2
        echo "$lint_out" >&2
      fi
    fi
    ;;

  *.ts|*.tsx|*.js|*.jsx)
    if [ -d frontend ]; then
      (cd frontend && npx --no-install prettier --write "../$file" 2>/dev/null) || true
    fi
    ;;

  *.md|*.json|*.yml|*.yaml)
    if command -v npx >/dev/null 2>&1; then
      npx --no-install prettier --write "$file" 2>/dev/null || true
    fi
    ;;
esac

exit 0
