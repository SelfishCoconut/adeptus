"""Unit tests for the cloud egress secret-pattern scanner (Slice 14, §5.1 / §5.5).

This is the core security logic of the slice and gets the densest coverage: every category
matched, clean text negative, precision negatives, dedup, and — critically — the guarantee
that the matched secret VALUE never escapes the scanner (only category names do, §5.5 /
Risk 7).

The secret-shaped strings below are SYNTHETIC test vectors, not real credentials. Each
vector line carries a ``gitleaks:allow`` directive so the gitleaks pre-commit hook does not
flag the synthetic value; the PEM header is assembled from two literals so the substring-
based ``detect-private-key`` hook does not flag it either.
"""

from __future__ import annotations

from app.features.chat import egress_scan

# Assembled from two literals so the contiguous PEM private-key header never appears as a
# single string in this file (the detect-private-key hook matches on that substring).
# Reassembled at runtime, the scanner sees a normal PEM header.
_PRIVATE_KEY_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"


def _categories(content: str) -> list[str]:
    return [m.category for m in egress_scan.scan(content)]


def test_aws_access_key_matched() -> None:
    assert "aws_access_key" in _categories("creds: AKIAIOSFODNN7EXAMPLE end")  # gitleaks:allow


def test_private_key_block_matched() -> None:
    assert "private_key_block" in _categories(f"key:\n{_PRIVATE_KEY_HEADER}\nMIIB...")


def test_jwt_matched() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w"  # gitleaks:allow
    assert "jwt" in _categories(f"token={jwt}")


def test_password_assignment_matched() -> None:
    assert "password_assignment" in _categories("login with password=hunter2")  # gitleaks:allow


def test_password_assignment_variants_matched() -> None:
    # passwd: and pwd = spellings (and whitespace around the operator) all flag.
    assert "password_assignment" in _categories("passwd: s3cr3tvalue")  # gitleaks:allow
    assert "password_assignment" in _categories("pwd = letmein")  # gitleaks:allow


def test_generic_api_key_matched() -> None:
    assert "generic_api_key" in _categories(
        "api_key=AbCdEf0123456789ZZ"  # gitleaks:allow
    )


def test_bearer_token_matched() -> None:
    assert "bearer_token" in _categories(
        "Authorization: Bearer abcDEF123456ghiJKL"  # gitleaks:allow
    )


def test_slack_token_matched() -> None:
    assert "slack_token" in _categories(
        "xoxb-2401234567-1234567890123-abcdEFGH"  # gitleaks:allow
    )


def test_clean_text_no_match() -> None:
    """Ordinary technical prose must not trip any pattern (precision — Risk 4)."""
    assert egress_scan.scan("How do I test for SQL injection on the login form?") == []


def test_ordinary_prose_with_the_word_password_not_matched() -> None:
    """The bare word "password" without an assignment is NOT a password= match (Risk 4)."""
    assert "password_assignment" not in _categories("I forgot my password, please help.")


def test_secret_word_without_value_not_matched() -> None:
    """ "the secret is safe" has no assignment + long value, so generic_api_key misses."""
    assert _categories("the secret is safe with me") == []


def test_short_token_value_not_matched() -> None:
    """A trivially short token= value is below the opaque-length floor (precision)."""
    assert "generic_api_key" not in _categories("token=1")


def test_multiple_matches_deduped_category_names() -> None:
    """Several secrets (incl. two of one kind) report each category exactly once, in order."""
    content = (
        "AKIAIOSFODNN7EXAMPLE and AKIAABCDEFGH12345678 "  # gitleaks:allow
        "plus password=hunter2"  # gitleaks:allow
    )
    cats = _categories(content)
    assert cats == ["aws_access_key", "password_assignment"]
    assert cats.count("aws_access_key") == 1


def test_match_never_includes_secret_value() -> None:
    """An EgressMatch carries only the category name — never the matched substring (§5.5).

    Guards Risk 7: the scanner result cannot carry a secret into audit / 409 body / logs."""
    secret = "AKIAIOSFODNN7EXAMPLE"  # gitleaks:allow
    matches = egress_scan.scan(f"here: {secret}")
    assert matches
    for match in matches:
        # The only field is ``category``; assert the secret value appears nowhere on it.
        assert secret not in str(match)
        assert vars(match) == {"category": match.category}


def test_category_names_projection_matches_scan() -> None:
    """category_names is a pure projection of scan to names (used by the 409 body / audit)."""
    content = "password=hunter2"  # gitleaks:allow
    assert egress_scan.category_names(content) == [m.category for m in egress_scan.scan(content)]
