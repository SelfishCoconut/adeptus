"""Heuristic secret-pattern scanner for the cloud egress-friction layer (Slice 14, §5.1).

This is *friction, not redaction* and *not* a DLP product (§5.5): a precision-over-recall
regex pass that FLAGS likely-secret content so the UI can ask "send anyway?" before a
``cloud_enabled`` engagement's message leaves the local network. A missed secret is a known
limitation; a false-positive modal is mere annoyance (Risk 4).

The matched VALUE is never captured, returned, logged, or persisted — only the stable
category NAME — so a secret can never leak into the audit log, a 409 body, or a log line via
this module (§5.5 / Risk 7). ``EgressMatch`` deliberately carries no substring field.

The v1 pattern set is LOCKED to the §5.1 examples (Resolved decision 2): no GitHub PAT, GCP
SA JSON, or Stripe key in this slice. The frontend re-implements a SUBSET of these for a
pre-flight UX scan; THIS module is the single server-authoritative source of truth — the POST
guard re-scans regardless of the client (Risk 3). Patterns are tuned precision-over-recall.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["EgressMatch", "scan", "category_names"]


@dataclass(frozen=True)
class EgressMatch:
    """One matched secret-pattern category.

    Carries ONLY the stable category name — never the matched substring (§5.5 / Risk 7).
    Keeping the value out of this object keeps it out of every downstream sink (audit
    payload, 409 body, logs), since there is nothing to leak."""

    category: str


# Ordered, named patterns. ``scan`` reports categories in this declaration order so the
# modal copy / audit list is stable. Each is anchored / length-bounded for precision: the
# goal is to almost never fire on ordinary prose, accepting that a cleverly-formatted secret
# may slip past (friction, not DLP — §5.1 / Risk 4).
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # AWS access key id: AKIA/ASIA prefix + 16 uppercase base32 chars (20 total).
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    # PEM private-key header, any algorithm label (RSA/EC/OPENSSH/ENCRYPTED/…) or none.
    ("private_key_block", re.compile(r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----")),
    # JWT: three base64url segments separated by dots; header segment starts ``eyJ`` (the
    # base64url of ``{"``), which is what distinguishes a JWT from any dotted token.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    # password=/passwd:/pwd = assignment with a non-empty value. The required [:=] keeps
    # bare prose ("I forgot my password") from matching (precision negative, Risk 4).
    ("password_assignment", re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*\S+")),
    # api_key/apikey/secret/token assignment with a LONG (>=16) opaque value, so "token=1"
    # or "the secret is safe" do not fire (precision).
    (
        "generic_api_key",
        re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{16,}"),
    ),
    # Authorization: Bearer <token> — require a long opaque token so "bearer of news" misses.
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.=+/]{12,}")),
    # Slack token: xoxb-/xoxa-/xoxp-/xoxr-/xoxs- + a long body (high-precision representative).
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
)


def scan(content: str) -> list[EgressMatch]:
    """Scan ``content`` for likely-secret patterns; return the matched categories.

    Returns one :class:`EgressMatch` per matched category, in pattern-declaration order,
    deduplicated (a category matched by several substrings is reported once). Returns ``[]``
    when nothing matches. The matched substring is intentionally NOT returned (§5.5)."""
    matches: list[EgressMatch] = []
    for category, pattern in _PATTERNS:
        if pattern.search(content):
            matches.append(EgressMatch(category=category))
    return matches


def category_names(content: str) -> list[str]:
    """Convenience: the matched category names only (e.g. for the 409 body / audit list).

    Pure projection of :func:`scan` — still never the matched value (§5.5)."""
    return [m.category for m in scan(content)]
