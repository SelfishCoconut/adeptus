#!/usr/bin/env bash
# .claude/hooks/pre-bash-guard.sh
# Blocks: destructive git, pentest tools against non-sandbox targets, rm -rf outside scratch.
# Exit 2 + stderr message = blocked (Claude reads it and adjusts).
# Exit 0 = allowed.
#
# Uses Python (stdlib only) to parse the JSON payload — no jq dependency.

set -euo pipefail

payload="$(cat)"

# Extract the command. Try python3 first (always present on a Python project),
# fall back to grep if python is somehow unavailable.
if command -v python3 >/dev/null 2>&1; then
  cmd="$(echo "$payload" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")"
else
  # Crude fallback — won't survive escaped quotes but better than nothing
  cmd="$(echo "$payload" | grep -oE '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*"command"[[:space:]]*:[[:space:]]*"([^"]*)"/\1/')"
fi

[ -z "$cmd" ] && exit 0

# 1. Destructive git
if echo "$cmd" | grep -qE '(git\s+push\s+--force|git\s+reset\s+--hard\s+HEAD~|git\s+clean\s+-fdx)'; then
  echo "Blocked: destructive git operation. Ask the user before retrying." >&2
  exit 2
fi

# 2. Pentest tools against non-sandbox targets
if echo "$cmd" | grep -qE '^[[:space:]]*(sudo[[:space:]]+)?(nmap|gobuster|ffuf|sqlmap|nikto|hydra|wpscan|masscan|amass|wfuzz)\b'; then
  if ! echo "$cmd" | grep -qE '(localhost|127\.0\.0\.1|juice-shop|host\.docker\.internal:3000|::1\b)'; then
    echo "Blocked: pentest tool against non-sandbox target." >&2
    echo "Run pentest tools only against the Juice Shop sandbox:" >&2
    echo "  make sandbox    # brings up Juice Shop at http://localhost:3000" >&2
    exit 2
  fi
fi

# 3. rm -rf outside known scratch paths
if echo "$cmd" | grep -qE 'rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)[[:space:]]+/'; then
  if ! echo "$cmd" | grep -qE 'rm[[:space:]]+-[rf]+[[:space:]]+(/tmp|node_modules|\./node_modules|dist|\.venv|\.next|coverage)'; then
    echo "Blocked: rm -rf outside known scratch paths. If intentional, run manually." >&2
    exit 2
  fi
fi

# 4. Curl/wget piped to shell
if echo "$cmd" | grep -qE '(curl|wget).*\|.*(sh|bash|python)\b'; then
  echo "Blocked: piping curl/wget output to a shell. Run manually if intentional." >&2
  exit 2
fi

exit 0
