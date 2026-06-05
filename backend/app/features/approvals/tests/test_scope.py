"""Unit tests for the pure scope parser + matcher (Slice 17 task 1).

This is the safety-relevant boundary of the slice: a parsing/matching error is the
worst failure (an out-of-scope command read as in-scope, or vice versa). The parser
(separators, host/IPv4/IPv6/CIDR/domain/wildcard classification, scheme/port stripping,
case-insensitivity, tolerant handling of malformed entries) and the matcher (exact,
CIDR membership, non-IP-vs-CIDR, wildcard suffix vs parent/sibling, **empty scope is
always in scope** — the soft policy) are densely exercised.
"""

from app.features.approvals.scope import is_in_scope, parse_scope

# --- parsing --------------------------------------------------------------------------


def test_parse_comma_and_whitespace_separated() -> None:
    scope = parse_scope("juice-shop, 10.0.0.0/24\n*.target.test   example.com")
    # All four entries land (2 names + bare host + cidr counted across both buckets).
    assert is_in_scope("juice-shop", scope)
    assert is_in_scope("10.0.0.7", scope)
    assert is_in_scope("a.b.target.test", scope)
    assert is_in_scope("example.com", scope)


def test_empty_scope_is_always_in_scope() -> None:
    # Soft policy (load-bearing): no declared scope ⇒ nothing is "outside" it.
    for raw in ("", "   ", "\n\t , ,"):
        scope = parse_scope(raw)
        assert scope.is_empty()
        assert is_in_scope("anything.example.com", scope)
        assert is_in_scope("10.9.8.7", scope)


def test_malformed_entry_is_ignored_not_raised() -> None:
    # Weird entries must never throw; the good entries around them still parse.
    scope = parse_scope("juice-shop, http://[:::bad, , target.test")
    assert is_in_scope("juice-shop", scope)
    assert is_in_scope("target.test", scope)
    # The malformed IPv6 URL did not become a matchable host for an unrelated target.
    assert not is_in_scope("evil.example.com", scope)


# --- bare host / IP exact match -------------------------------------------------------


def test_bare_host_exact_match() -> None:
    scope = parse_scope("juice-shop")
    assert is_in_scope("juice-shop", scope)
    assert not is_in_scope("evil-host", scope)


def test_ipv4_exact_match() -> None:
    scope = parse_scope("10.0.0.5")
    assert is_in_scope("10.0.0.5", scope)
    assert not is_in_scope("10.0.0.6", scope)


def test_ipv6_exact_match() -> None:
    scope = parse_scope("2001:db8::1")
    assert is_in_scope("2001:db8::1", scope)
    assert not is_in_scope("2001:db8::2", scope)


# --- CIDR -----------------------------------------------------------------------------


def test_cidr_membership() -> None:
    scope = parse_scope("10.0.0.0/24")
    assert is_in_scope("10.0.0.1", scope)
    assert is_in_scope("10.0.0.254", scope)
    assert not is_in_scope("10.0.1.1", scope)


def test_ipv6_cidr_membership() -> None:
    scope = parse_scope("2001:db8::/32")
    assert is_in_scope("2001:db8::dead", scope)
    assert not is_in_scope("2001:dead::1", scope)


def test_non_ip_host_never_matches_cidr() -> None:
    scope = parse_scope("10.0.0.0/24")
    # A domain name parses as no IP, so it can never fall inside a CIDR range.
    assert not is_in_scope("example.com", scope)
    assert not is_in_scope("10-0-0-1.example.com", scope)


# --- domain / wildcard ----------------------------------------------------------------


def test_domain_exact_match() -> None:
    scope = parse_scope("target.test")
    assert is_in_scope("target.test", scope)  # apex
    assert is_in_scope("a.b.target.test", scope)  # a domain entry covers subdomains
    assert not is_in_scope("nottarget.test", scope)


def test_wildcard_suffix_match() -> None:
    scope = parse_scope("*.target.test")
    assert is_in_scope("a.target.test", scope)
    assert is_in_scope("a.b.target.test", scope)


def test_wildcard_does_not_match_parent_sibling() -> None:
    scope = parse_scope("*.target.test")
    assert not is_in_scope("target.test", scope)  # apex (parent) is NOT a subdomain
    assert not is_in_scope("othertarget.test", scope)  # sibling does not share the suffix
    assert not is_in_scope("target.test.evil.com", scope)  # suffix in the middle, not end


# --- normalisation --------------------------------------------------------------------


def test_entry_with_scheme_and_port_is_stripped() -> None:
    scope = parse_scope("http://juice-shop:3000/login")
    assert is_in_scope("juice-shop", scope)
    # A URL-wrapped IP resolves to its IP host (and matches as an exact /32).
    scope_ip = parse_scope("http://10.0.0.5:8080")
    assert is_in_scope("10.0.0.5", scope_ip)


def test_case_insensitive() -> None:
    scope = parse_scope("Juice-Shop, *.Target.TEST, EXAMPLE.com")
    assert is_in_scope("JUICE-SHOP", scope)
    assert is_in_scope("A.target.test", scope)
    assert is_in_scope("example.COM", scope)
