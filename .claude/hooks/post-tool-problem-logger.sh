#!/usr/bin/env bash
# .claude/hooks/post-tool-problem-logger.sh
#
# PostToolUse hook — logs failures and auto-resolves on passing tests/lint.
#
# Payload is written to a temp file first to avoid all string-interpolation
# and quoting issues. Python reads the file directly.
# Never exits non-zero — logging must never block Claude's work.

set -o pipefail

LOG_DIR="${CLAUDE_PROJECT_DIR:-.}/docs/logs"
LOG_FILE="$LOG_DIR/problems.log"
DETAIL_DIR="$LOG_DIR/details"

[ -f "$LOG_FILE" ] || exit 0

# Write stdin to a temp file — the only reliable way to pass arbitrary JSON to python
payload_file="$(mktemp /tmp/adeptus-payload-XXXXXX.json)"
cat > "$payload_file"

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
branch="$(git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
# Detached HEAD guard (happens in some CI/test envs)
[ "$branch" = "HEAD" ] && branch="$(git -C "${CLAUDE_PROJECT_DIR:-.}" branch --show-current 2>/dev/null || echo unknown)"

# ── Parse payload into a temp env file via python3 ────────────────────────
env_file="$(mktemp /tmp/adeptus-env-XXXXXX.sh)"

python3 - "$payload_file" "$env_file" << 'PYEOF'
import json, sys, base64, os

payload_path = sys.argv[1]
env_path     = sys.argv[2]

def b64(s): return base64.b64encode(str(s).encode()).decode()

try:
    with open(payload_path) as f:
        d = json.load(f)
except Exception as e:
    with open(env_path, "w") as f:
        f.write(f"PARSE_FAILED=true\n")
    sys.exit(0)

ti = d.get("tool_input",    {}) or {}
tr = d.get("tool_response", {}) or {}

tool_name = str(d.get("tool_name", ""))
exit_code = str(tr.get("exit_code") or tr.get("exitCode") or "")
stderr    = str(tr.get("stderr")  or tr.get("error")  or "")
stdout    = str(tr.get("stdout")  or tr.get("output") or "")
cmd       = str(ti.get("command") or ti.get("file_path") or ti.get("path") or "")
combined  = (stderr + "\n" + stdout).strip()

# First meaningful error line
first_error = ""
for line in combined.splitlines():
    line = line.strip()
    if not line:
        continue
    if any(line.startswith(p) for p in ("Traceback", 'File "', "  File ", "During handling", "  ^")):
        continue
    first_error = line[:120]
    break
if not first_error:
    first_error = "(see detail file)"

# Failure detection
is_failure = "false"
if tool_name == "Bash":
    if exit_code not in ("", "0"):
        is_failure = "true"
elif str(tr.get("success", "true")).lower() == "false":
    is_failure = "true"

# Category
category = "bash-exit"
if tool_name in ("Write", "Edit", "str_replace_based_edit"):
    category = "edit-fail"
elif any(x in cmd for x in ("pytest", "uv run pytest")):
    category = "test"
elif any(x in cmd for x in ("ruff", "mypy", "eslint", "tsc --noEmit", "pnpm tsc")):
    category = "lint"

with open(env_path, "w") as f:
    f.write(f"TOOL_NAME_B64={b64(tool_name)}\n")
    f.write(f"EXIT_CODE_B64={b64(exit_code)}\n")
    f.write(f"CMD_B64={b64(cmd[:300])}\n")
    f.write(f"COMBINED_B64={b64(combined[:4000])}\n")
    f.write(f"FIRST_ERROR_B64={b64(first_error)}\n")
    f.write(f"CATEGORY_B64={b64(category)}\n")
    f.write(f"IS_FAILURE={is_failure}\n")
PYEOF

py_exit=$?
rm -f "$payload_file"

[ "$py_exit" != "0" ] && { rm -f "$env_file"; exit 0; }

# shellcheck disable=SC1090
source "$env_file" 2>/dev/null
rm -f "$env_file"

[ "${PARSE_FAILED:-false}" = "true" ] && exit 0

# Decode a base64 var
b64d() { printf '%s' "$1" | python3 -c "import base64,sys; sys.stdout.write(base64.b64decode(sys.stdin.read().strip()).decode())" 2>/dev/null; }

TOOL_NAME="$(b64d "${TOOL_NAME_B64:-}")"
EXIT_CODE="$(b64d "${EXIT_CODE_B64:-}")"
CMD="$(b64d "${CMD_B64:-}")"
COMBINED="$(b64d "${COMBINED_B64:-}")"
FIRST_ERROR="$(b64d "${FIRST_ERROR_B64:-}")"
CATEGORY="$(b64d "${CATEGORY_B64:-}")"

# ── Helpers ────────────────────────────────────────────────────────────────
last_open_ts() {
  grep "^\[OPEN\].*branch=${branch}" "$LOG_FILE" 2>/dev/null \
    | tail -1 \
    | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' \
    | head -1
}

last_open_resolved() {
  grep -q "^\[RESOLVED\].*problem=${1}" "$LOG_FILE" 2>/dev/null
}

# ── Log a failure ──────────────────────────────────────────────────────────
if [ "${IS_FAILURE:-false}" = "true" ]; then
  mkdir -p "$DETAIL_DIR"
  detail_file="$DETAIL_DIR/${ts}.txt"
  {
    printf 'Timestamp: %s\n' "$ts"
    printf 'Branch:    %s\n' "$branch"
    printf 'Tool:      %s\n' "$TOOL_NAME"
    printf 'Exit code: %s\n' "$EXIT_CODE"
    printf 'Command:   %s\n' "$CMD"
    printf -- '---\n'
    printf '%s\n' "$COMBINED"
  } > "$detail_file" 2>/dev/null || true

  {
    printf '[OPEN]     %s  branch=%s  type=%s  exit=%s\n' \
      "$ts" "$branch" "$CATEGORY" "$EXIT_CODE"
    printf '           cmd: %s\n'   "${CMD:0:200}"
    printf '           error: %s\n' "$FIRST_ERROR"
    printf '           full: docs/logs/details/%s.txt\n' "$ts"
  } >> "$LOG_FILE"

# ── Auto-resolve on a passing test/lint run ────────────────────────────────
elif [ "$TOOL_NAME" = "Bash" ] && [ "$EXIT_CODE" = "0" ]; then
  if echo "$CMD" | grep -qE '(pytest|ruff|mypy|eslint|tsc|make test|make lint)'; then
    open_ts="$(last_open_ts)"
    if [ -n "$open_ts" ] && ! last_open_resolved "$open_ts"; then
      last_commit="$(git -C "${CLAUDE_PROJECT_DIR:-.}" log -1 --format='%h %s' 2>/dev/null || echo 'unknown')"
      fix_desc="$(git -C "${CLAUDE_PROJECT_DIR:-.}" log -1 --format='%s' 2>/dev/null | cut -c1-100)"
      {
        printf '[RESOLVED] %s  branch=%s  problem=%s\n' "$ts" "$branch" "$open_ts"
        printf '           commit: %s\n' "$last_commit"
        printf '           how: %s\n'   "${fix_desc:-test/lint run passed}"
      } >> "$LOG_FILE"
    fi
  fi
fi

exit 0
