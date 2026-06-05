"""Pure scope parser + matcher for the §5.2 soft-scope arm (Slice 17).

The engagement's scope is free text captured by the Slice-01 create wizard
(``engagements.scope``: IPs/domains, §4). This module parses that text into a
normalised :class:`ScopeList` and answers :func:`is_in_scope` for a resolved target
host. It is the only genuinely new logic in the slice and the safety-relevant boundary,
so it is pure (no I/O, no DB) and densely unit-tested.

Matching rules (case-insensitive throughout):

* **Bare host / IP literal** — exact host equality, or IP membership for an IP literal
  (a bare ``10.0.0.5`` is treated as a ``/32`` network).
* **CIDR range** — IP membership via the stdlib ``ipaddress`` module; a non-IP host
  never matches a CIDR (Risk 6).
* **Domain** (``target.test``) — matches the apex AND any subdomain
  (``a.b.target.test``).
* **Wildcard** (``*.target.test``) — matches any subdomain (``a.b.target.test``) but
  NOT the apex (``target.test``) and NOT a sibling (``othertarget.test``) — a strict
  suffix-only rule that guards against over-broad matching (Risk 1).

**Empty-scope policy (soft, load-bearing):** an engagement with no declared scope has
nothing to be "outside" of, so :func:`is_in_scope` returns ``True`` for an empty
``ScopeList`` — scope never fires, never blocks. This is the §5.2 soft posture.

Host extraction for scope reuses :func:`mcp.concurrency.parse_host` — the same
userinfo-smuggling-safe extractor used by the per-target lock and the sandbox guard —
so the scope host, the lock host, and the sandbox host can never drift (Risk 3).
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field

from app.features.mcp.concurrency import parse_host

logger = logging.getLogger(__name__)

__all__ = ["ScopeList", "is_in_scope", "parse_scope"]

# Scope entries are separated by commas and/or any whitespace (incl. newlines).
_SEPARATORS = re.compile(r"[,\s]+")

_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class _NamePattern:
    """A parsed host / domain / wildcard scope entry.

    ``value`` is the lower-cased entry; for a wildcard it is the suffix *including* the
    leading dot (``*.target.test`` → ``.target.test``). ``wildcard`` selects the match
    rule: a wildcard matches strictly on the dotted suffix (subdomains only); a plain
    name matches the apex or any subdomain.
    """

    value: str
    wildcard: bool

    def matches(self, host: str) -> bool:
        if self.wildcard:
            return host.endswith(self.value)
        return host == self.value or host.endswith("." + self.value)


@dataclass(frozen=True)
class ScopeList:
    """A normalised, matchable view of an engagement's free-text scope.

    ``networks`` are IP/CIDR entries (bare IPs are stored as ``/32`` or ``/128``);
    ``names`` are host/domain/wildcard entries. An empty ``ScopeList`` (no networks,
    no names) means "no scope declared" — :func:`is_in_scope` is always ``True``.
    """

    networks: tuple[_IPNetwork, ...] = field(default_factory=tuple)
    names: tuple[_NamePattern, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not self.networks and not self.names


def _try_network(entry: str) -> _IPNetwork | None:
    """Parse ``entry`` as an IP or CIDR (bare IP → host network), else ``None``."""
    try:
        return ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return None


def _try_address(host: str) -> _IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def parse_scope(raw: str) -> ScopeList:
    """Parse free-text engagement scope into a :class:`ScopeList` (tolerant, never raises).

    Splits on commas / whitespace / newlines and classifies each entry as an IP/CIDR
    network or a host/domain/wildcard name. URL-like entries are reduced to their host
    via ``parse_host`` (scheme/port/path stripped). Blank or unparseable entries are
    ignored, never fatal (Risk 2/6) — a malformed scope degrades to a smaller list, it
    never throws. A blank scope parses to an empty ``ScopeList``.
    """
    networks: list[_IPNetwork] = []
    names: list[_NamePattern] = []
    for token in _SEPARATORS.split(raw or ""):
        entry = token.strip().lower()
        if not entry:
            continue
        try:
            # IP/CIDR first so a CIDR's "/24" is never mistaken for a URL path.
            net = _try_network(entry)
            if net is not None:
                networks.append(net)
                continue
            # Otherwise a host/domain/wildcard: strip scheme/port/path if URL-like.
            host = parse_host(entry)
            if not host:
                continue
            # A URL-wrapped IP (e.g. http://10.0.0.5:3000) resolves to an IP host.
            net = _try_network(host)
            if net is not None:
                networks.append(net)
                continue
            if host.startswith("*."):
                names.append(_NamePattern(value=host[1:], wildcard=True))
            else:
                names.append(_NamePattern(value=host, wildcard=False))
        except Exception:  # noqa: BLE001 — tolerant: a weird entry is skipped, not fatal
            logger.warning("Ignoring unparseable scope entry %r", entry)
            continue
    return ScopeList(networks=tuple(networks), names=tuple(names))


def is_in_scope(host: str, scope: ScopeList) -> bool:
    """Return ``True`` if ``host`` is within ``scope`` (case-insensitive).

    An empty ``scope`` is always in scope (the soft, no-scope-declared policy). A host
    matches on exact host equality, IP/CIDR membership, an apex-or-subdomain domain
    entry, or a strict subdomain wildcard entry. A non-IP host never matches a CIDR.
    """
    if scope.is_empty():
        return True
    h = host.strip().lower()
    if not h:
        return True
    ip = _try_address(h)
    if ip is not None and any(ip in net for net in scope.networks):
        return True
    return any(pattern.matches(h) for pattern in scope.names)
