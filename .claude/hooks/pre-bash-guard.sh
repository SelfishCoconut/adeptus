#!/usr/bin/env bash
# .claude/hooks/pre-bash-guard.sh
# Blocks: destructive git, pentest tools against non-sandbox targets, rm -rf outside scratch.
# Exit 2 + stderr message = blocked (Claude reads it and adjusts).
# Exit 0 = allowed.
#
# DEFENSE-IN-DEPTH, NOT A SECURITY BOUNDARY. This hook is a guardrail to stop
# accidental scans of non-sandbox hosts during development. It is best-effort
# pattern matching and is trivially bypassable (run the command manually, in a
# subshell, via a wrapper script, etc.). Real target authorization, network
# isolation, and egress control live elsewhere — do not rely on this for safety.
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

# 2. Pentest tools against non-sandbox targets.
#    Rather than substring-matching the whole command line (which lets
#    `nmap evil.com # localhost` slip through), extract the actual target
#    arguments and check THOSE against the sandbox allowlist. Unknown
#    network-capable tools default to BLOCK (fail-safe, not fail-open).
if command -v python3 >/dev/null 2>&1; then
  guard_out="$(python3 - "$cmd" <<'PYEOF'
import shlex, sys
from urllib.parse import urlparse

cmd = sys.argv[1] if len(sys.argv) > 1 else ""

# Hosts that are always OK (the dev sandbox + loopback).
SANDBOX = {"localhost", "127.0.0.1", "::1", "[::1]", "juice-shop", "host.docker.internal"}

# Known network-capable pentest / recon tools. Non-exhaustive by design — the
# unknown-tool backstop below catches anything not listed here.
PENTEST = {
    "nmap", "masscan", "gobuster", "ffuf", "wfuzz", "feroxbuster", "dirb",
    "dirbuster", "sqlmap", "nikto", "hydra", "medusa", "patator", "wpscan",
    "amass", "subfinder", "assetfinder", "nuclei", "katana", "gospider",
    "hakrawler", "gau", "waybackurls", "httpx", "httprobe", "whatweb",
    "wafw00f", "testssl", "testssl.sh", "sslscan", "sslyze", "dnsrecon",
    "dnsenum", "fierce", "dalfox", "xsstrike", "commix", "arjun",
    "crackmapexec", "netexec", "enum4linux", "smbmap", "nbtscan",
    "onesixtyone", "snmpwalk", "metasploit", "msfconsole",
}

# Ordinary dev commands that legitimately reach external hosts. These are NOT
# treated as "unknown network-capable tools" by the backstop.
COMMON_SAFE = {
    "curl", "wget", "http", "https", "httpie", "git", "gh", "ssh", "scp",
    "sftp", "rsync", "ping", "dig", "host", "nslookup", "traceroute",
    "npm", "pnpm", "npx", "yarn", "node", "deno", "bun", "uv", "pip", "pip3",
    "pipx", "python", "python3", "poetry", "pytest", "ruff", "mypy", "eslint",
    "tsc", "prettier", "vite", "docker", "docker-compose", "podman", "make",
    "cmake", "alembic", "psql", "pg_dump", "createdb", "redis-cli", "mongosh",
    "aws", "gcloud", "az", "kubectl", "helm", "terraform", "ansible", "brew",
    "apt", "apt-get", "yum", "dnf", "pacman", "cargo", "go", "rustc", "java",
    "mvn", "gradle", "bash", "sh", "zsh", "env", "cat", "echo", "printf",
    "grep", "egrep", "rg", "sed", "awk", "find", "ls", "cp", "mv", "rm",
    "mkdir", "touch", "chmod", "chown", "tar", "gzip", "unzip", "zip", "jq",
    "yq", "head", "tail", "sort", "uniq", "wc", "tee", "xargs", "cut", "tr",
    "base64", "openssl", "date", "sleep", "true", "false", "test",
}

FILE_EXT = {
    "txt", "json", "yaml", "yml", "csv", "xml", "html", "htm", "conf", "cfg",
    "lst", "list", "pdf", "md", "log", "ini", "toml", "sh", "py", "js", "ts",
    "sql", "db", "pem", "key", "crt", "env",
}

def leading_tool(tokens):
    """First real command token, skipping sudo and VAR=val env prefixes."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "sudo":
            i += 1
            continue
        if "=" in t and not t.startswith("-") and "/" not in t.split("=")[0]:
            i += 1  # env assignment prefix
            continue
        base = t.rsplit("/", 1)[-1]  # strip any path
        return base
    return ""

def host_of(token):
    """Return (host, strong) if token looks like a network target, else None.
    strong=True for URLs / IPs / explicit host:port (unambiguous targets);
    strong=False for bare dotted hostnames (could be a filename — handled
    conservatively)."""
    import re
    t = token.strip()
    if not t or t.startswith("-"):
        return None
    # Bare sandbox host, possibly with a port (e.g. localhost, juice-shop:3000).
    # These have no dot, so the generic hostname rules below would miss them.
    m = re.match(r"^([A-Za-z0-9._-]+?)(:\d+)?$", t)
    if m and m.group(1).lower() in SANDBOX:
        return (m.group(1).lower(), True)
    # URL
    if t.startswith(("http://", "https://", "ftp://", "ws://", "wss://")):
        h = urlparse(t).hostname or ""
        return (h.lower(), True) if h else None
    # IPv4, optional :port or /cidr
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}([:/]\d+)?$", t):
        return (t.split(":")[0].split("/")[0], True)
    # host:port
    m = re.match(r"^([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9-]+)+):\d+$", t)
    if m:
        return (m.group(1).lower(), True)
    # bare dotted hostname (weak — skip if it looks like a filename)
    if re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9-]+)+$", t):
        last = t.rsplit(".", 1)[-1].lower()
        if last in FILE_EXT or last.isdigit():
            return None
        return (t.lower(), False)
    return None

try:
    tokens = shlex.split(cmd, comments=True)
except ValueError:
    tokens = cmd.split()

tool = leading_tool(tokens)
targets = [r for r in (host_of(t) for t in tokens[1:]) if r]

def is_sandbox(h):
    return h in SANDBOX

if tool in PENTEST:
    nonsandbox = [h for h, _ in targets if not is_sandbox(h)]
    if nonsandbox:
        print("BLOCK\tpentest tool '%s' against non-sandbox target(s): %s"
              % (tool, ", ".join(sorted(set(nonsandbox)))))
    elif targets:
        print("ALLOW")
    else:
        print("BLOCK\tpentest tool '%s' with no sandbox target detected; "
              "if intentional, run it manually" % tool)
elif tool and tool not in COMMON_SAFE:
    # Unknown leading command. Fail safe only when it carries an explicit
    # (strong) off-sandbox target — avoids nuking ordinary local commands.
    strong_off = [h for h, strong in targets if strong and not is_sandbox(h)]
    if strong_off:
        print("BLOCK\tunknown network-capable tool '%s' against non-sandbox "
              "target(s): %s; if intentional, run it manually"
              % (tool, ", ".join(sorted(set(strong_off)))))
    else:
        print("ALLOW")
else:
    print("ALLOW")
PYEOF
)"
  if [ "${guard_out%%	*}" = "BLOCK" ]; then
    echo "Blocked: ${guard_out#*	}" >&2
    echo "Run pentest tools only against the Juice Shop sandbox:" >&2
    echo "  make sandbox    # brings up Juice Shop at http://localhost:3000" >&2
    exit 2
  fi
else
  # No python3: fall back to the conservative substring check (block known
  # pentest tools unless a sandbox host appears somewhere in the command).
  if echo "$cmd" | grep -qE '^[[:space:]]*(sudo[[:space:]]+)?(nmap|gobuster|ffuf|sqlmap|nikto|hydra|wpscan|masscan|amass|wfuzz|nuclei|katana|dirb|feroxbuster|testssl(\.sh)?|whatweb|subfinder)\b'; then
    if ! echo "$cmd" | grep -qE '(localhost|127\.0\.0\.1|juice-shop|host\.docker\.internal|::1\b)'; then
      echo "Blocked: pentest tool against non-sandbox target (python3 unavailable; conservative check)." >&2
      echo "Run pentest tools only against the Juice Shop sandbox:" >&2
      echo "  make sandbox    # brings up Juice Shop at http://localhost:3000" >&2
      exit 2
    fi
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
