#!/usr/bin/env bash
# .git-hooks/check-commit-msg.sh
# Enforce Conventional Commits with optional (slice-NN) scope.
# Pre-commit passes the path to the commit message file as $1.

set -euo pipefail

msg_file="${1:-}"
if [ -z "$msg_file" ] || [ ! -f "$msg_file" ]; then
  echo "check-commit-msg: expected commit message file as first arg" >&2
  exit 1
fi

first_line="$(head -n1 "$msg_file")"

# Allowed types: feat, fix, chore, docs, test, refactor, perf, build, ci, style, revert
# Optional scope: (slice-NN) or any (word)
# Required: ": " followed by at least one character
pattern='^(feat|fix|chore|docs|test|refactor|perf|build|ci|style|revert)(\([a-z0-9_-]+\))?: .+'

if ! echo "$first_line" | grep -qE "$pattern"; then
  cat >&2 <<'ERR'
Commit message does not follow Conventional Commits.

Format:
  <type>(<scope>): <description>

Examples:
  feat(slice-03): add MCP shell-exec server
  fix(slice-07): correct race in single-writer queue drain
  chore: bump ruff to 0.6.1
  docs(slice-10): document hash-chain verification CLI

Allowed types: feat | fix | chore | docs | test | refactor | perf | build | ci | style | revert
Scope is optional; prefer slice-NN where applicable.
ERR
  exit 1
fi

exit 0
