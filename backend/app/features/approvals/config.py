"""Dangerous-classification config for the approvals classifier (Slice 16).

The dangerous capability-flag set + the dangerous tool / preset / arg-signal lists live
here (NOT in code) so adding a dangerous tool needs no code change — each is overridable
by a comma-separated env var. This is the load-bearing surface the security reviewer
assesses for completeness under the INVERTED default (Resolved decision 2, Risk 2,
threat-model (a)/(j)): a genuinely dangerous tool that is mis-manifested AND absent from
every list here would run ungated, so these lists are a primary safety compensation.

All names/flags are compared case-insensitively (normalized to lower-case).
"""

import os

__all__ = [
    "AGGRESSIVE_PRESETS",
    "AGGRESSIVE_SCAN_TOOLS",
    "CREDENTIAL_ARG_SIGNALS",
    "CREDENTIAL_ATTACK_TOOLS",
    "CREDENTIAL_FLAGS",
    "DANGEROUS_CAPABILITY_FLAGS",
    "TARGET_WRITE_FLAGS",
    "TARGET_WRITE_TOOLS",
]


def _env_set(name: str, default: frozenset[str]) -> frozenset[str]:
    """Read a comma-separated env override (lower-cased, stripped); else the default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


# Capability flags that mark a tool as a target-write danger (§5.2 first bullet).
TARGET_WRITE_FLAGS: frozenset[str] = _env_set(
    "ADEPTUS_TARGET_WRITE_FLAGS",
    frozenset({"shell-exec", "filesystem-write", "target-write"}),
)

# Capability flag that marks a tool as a credential-attack danger (§5.2 third bullet).
CREDENTIAL_FLAGS: frozenset[str] = _env_set(
    "ADEPTUS_CREDENTIAL_FLAGS",
    frozenset({"credential-attack"}),
)

# The union — any of these flags makes a tool dangerous (used by validate/diagnostics).
DANGEROUS_CAPABILITY_FLAGS: frozenset[str] = TARGET_WRITE_FLAGS | CREDENTIAL_FLAGS

# Explicit dangerous-tool lists. Each entry is a bare ``tool`` name OR a ``server/tool``
# pair; both forms are matched. Kept conservative — most danger is caught by the
# capability flags / weight, these cover tools whose manifest may under-declare.
TARGET_WRITE_TOOLS: frozenset[str] = _env_set(
    "ADEPTUS_TARGET_WRITE_TOOLS",
    frozenset({"sqlmap", "metasploit", "msfconsole"}),
)

AGGRESSIVE_SCAN_TOOLS: frozenset[str] = _env_set(
    "ADEPTUS_AGGRESSIVE_SCAN_TOOLS",
    # Matched against ``action.tool_name`` (and ``server/tool``), so the entry is the
    # manifest tool name ``run_nmap``, not the category. nmap's ``weight=heavy`` already
    # gates it; this is belt-and-suspenders if the weight is ever relaxed.
    frozenset({"masscan", "run_nmap", "nmap/run_nmap"}),
)

CREDENTIAL_ATTACK_TOOLS: frozenset[str] = _env_set(
    "ADEPTUS_CREDENTIAL_ATTACK_TOOLS",
    # ffuf is included (the spec's canonical example) so a login-endpoint fuzz gates as a
    # credential attack even without a brute/spray arg signal; its weight=heavy also gates it.
    frozenset({"hydra", "medusa", "ncrack", "patator", "crowbar", "ffuf"}),
)

# Resolved preset names that always mean an aggressive scan (§5.2 second bullet).
AGGRESSIVE_PRESETS: frozenset[str] = _env_set(
    "ADEPTUS_AGGRESSIVE_PRESETS",
    frozenset({"aggressive"}),
)

# Substrings in the proposed args (keys or values) that signal a credential attack
# (brute force / password spraying). Kept unambiguous to avoid mislabeling benign
# wordlist-driven content discovery.
CREDENTIAL_ARG_SIGNALS: frozenset[str] = _env_set(
    "ADEPTUS_CREDENTIAL_ARG_SIGNALS",
    frozenset({"brute", "spray", "rockyou"}),
)
