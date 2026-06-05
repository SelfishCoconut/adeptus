"""Unit tests for the two-tier classifier (Slice 16 task 3).

This is the §5.2 safety boundary AND the inverted-default (Resolved decision 2) — the
densest-covered module in the slice. Every dangerous category, the dangerous-flag set,
the escape hatch, the inverted default, ``validate_tool_manifests``, the reserved
``out_of_scope``, and multi-reason combination are exercised.
"""

from app.features.approvals.classifier import ToolConfig, classify, validate_tool_manifests
from app.features.approvals.schemas import ApprovalReason, ApprovalTier, ProposedAction
from app.features.approvals.scope import parse_scope


def _action(
    *,
    server: str = "httpx-server",
    tool: str = "httpx",
    args: dict[str, object] | None = None,
    preset: str | None = None,
) -> ProposedAction:
    return ProposedAction(
        server_name=server,
        tool_name=tool,
        args=args or {"target": "10.0.0.5"},
        preset_name=preset,
    )


# --- Autonomous (the inverted default) ------------------------------------------------


def test_light_with_safe_flags_is_autonomous() -> None:
    result = classify(
        _action(), tool_config=ToolConfig(weight="light", capability_flags=("network",))
    )
    assert result.tier is ApprovalTier.AUTONOMOUS
    assert result.reasons == []


def test_unknown_but_light_and_safe_runs_autonomously() -> None:
    # The load-bearing INVERTED default: a present, non-dangerous classification runs
    # WITHOUT a human gate even for a tool not on any list.
    result = classify(
        _action(server="mystery", tool="some-new-recon-tool"),
        tool_config=ToolConfig(weight="light", capability_flags=()),
    )
    assert result.tier is ApprovalTier.AUTONOMOUS
    assert result.reasons == []


# --- target_write ---------------------------------------------------------------------


def test_shell_exec_flag_is_target_write() -> None:
    result = classify(
        _action(server="shell-exec", tool="run", args={"cmd": "id"}),
        tool_config=ToolConfig(weight="light", capability_flags=("shell-exec",)),
    )
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert result.reasons == [ApprovalReason.TARGET_WRITE]


def test_filesystem_write_flag_is_target_write() -> None:
    result = classify(
        _action(), tool_config=ToolConfig(weight="light", capability_flags=("filesystem-write",))
    )
    assert result.reasons == [ApprovalReason.TARGET_WRITE]


def test_target_write_flag_is_target_write() -> None:
    result = classify(
        _action(), tool_config=ToolConfig(weight="light", capability_flags=("target-write",))
    )
    assert result.reasons == [ApprovalReason.TARGET_WRITE]


def test_target_write_tool_list_is_target_write() -> None:
    result = classify(
        _action(server="exploit", tool="sqlmap"),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
    )
    assert result.reasons == [ApprovalReason.TARGET_WRITE]


# --- aggressive_scan ------------------------------------------------------------------


def test_heavy_tool_is_aggressive_scan() -> None:
    result = classify(
        _action(server="nmap-server", tool="nmap"),
        tool_config=ToolConfig(weight="heavy", capability_flags=("network",)),
    )
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert result.reasons == [ApprovalReason.AGGRESSIVE_SCAN]


def test_aggressive_preset_is_aggressive_scan() -> None:
    result = classify(
        _action(server="nmap-server", tool="nmap", preset="aggressive"),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
    )
    assert result.reasons == [ApprovalReason.AGGRESSIVE_SCAN]


def test_aggressive_scan_tool_list_is_aggressive_scan() -> None:
    result = classify(
        _action(server="scan", tool="masscan"),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
    )
    assert result.reasons == [ApprovalReason.AGGRESSIVE_SCAN]


# --- credential_attack ----------------------------------------------------------------


def test_credential_flag_is_credential_attack() -> None:
    result = classify(
        _action(), tool_config=ToolConfig(weight="light", capability_flags=("credential-attack",))
    )
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert result.reasons == [ApprovalReason.CREDENTIAL_ATTACK]


def test_credential_tool_list_is_credential_attack() -> None:
    result = classify(
        _action(server="creds", tool="hydra", args={"target": "ssh://10.0.0.5"}),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
    )
    assert result.reasons == [ApprovalReason.CREDENTIAL_ATTACK]


def test_brute_arg_signal_is_credential_attack() -> None:
    result = classify(
        _action(tool="ffuf", args={"mode": "brute", "wordlist": "rockyou.txt"}),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
    )
    assert result.reasons == [ApprovalReason.CREDENTIAL_ATTACK]


# --- The escape hatch (load-bearing) --------------------------------------------------


def test_empty_manifest_gates_as_unclassified() -> None:
    # No weight, no flags at all → the genuinely-unknown case still gates.
    result = classify(_action(), tool_config=ToolConfig())
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert result.reasons == [ApprovalReason.UNCLASSIFIED_MANIFEST]


def test_missing_weight_only_gates_as_unclassified() -> None:
    # A missing weight ALONE gates, even when a benign flag is present: a tool without a
    # present weight was never classified, so it can never run ungated.
    result = classify(_action(), tool_config=ToolConfig(weight=None, capability_flags=("network",)))
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert result.reasons == [ApprovalReason.UNCLASSIFIED_MANIFEST]


def test_missing_weight_with_dangerous_flag_uses_specific_reason() -> None:
    # When a dangerous signal is present the specific reason wins over the escape hatch.
    result = classify(
        _action(), tool_config=ToolConfig(weight=None, capability_flags=("shell-exec",))
    )
    assert result.reasons == [ApprovalReason.TARGET_WRITE]
    assert ApprovalReason.UNCLASSIFIED_MANIFEST not in result.reasons


# --- Multi-reason combination ---------------------------------------------------------


def test_multiple_reasons_combine() -> None:
    # A heavy credential tool that also writes the target → all three §5.2 categories.
    result = classify(
        _action(server="creds", tool="hydra", args={"mode": "brute"}, preset="aggressive"),
        tool_config=ToolConfig(weight="heavy", capability_flags=("shell-exec",)),
    )
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert set(result.reasons) == {
        ApprovalReason.AGGRESSIVE_SCAN,
        ApprovalReason.TARGET_WRITE,
        ApprovalReason.CREDENTIAL_ATTACK,
    }
    # No duplicate reasons.
    assert len(result.reasons) == len(set(result.reasons))


# --- out_of_scope only when the scope args are supplied -------------------------------


def test_out_of_scope_not_returned_without_scope_args() -> None:
    # The scope arm is opt-in: with no scope/target_host args (the Slice-16 call shape)
    # classify never produces out_of_scope, regardless of the command.
    cases = [
        (_action(), ToolConfig()),
        (_action(tool="hydra"), ToolConfig(weight="heavy", capability_flags=("shell-exec",))),
        (_action(), ToolConfig(weight="light", capability_flags=("network",))),
        (_action(preset="aggressive"), ToolConfig(weight="light")),
    ]
    for action, cfg in cases:
        result = classify(action, tool_config=cfg)
        assert ApprovalReason.OUT_OF_SCOPE not in result.reasons


# --- Scope arm (Slice 17) -------------------------------------------------------------


def test_out_of_scope_host_appends_out_of_scope_reason() -> None:
    scope = parse_scope("juice-shop")
    result = classify(
        _action(tool="httpx", args={"target": "http://example.com"}),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
        scope=scope,
        target_host="example.com",
    )
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert result.reasons == [ApprovalReason.OUT_OF_SCOPE]


def test_in_scope_host_does_not_append() -> None:
    scope = parse_scope("juice-shop, example.com")
    result = classify(
        _action(tool="httpx", args={"target": "http://example.com"}),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
        scope=scope,
        target_host="example.com",
    )
    assert result.tier is ApprovalTier.AUTONOMOUS
    assert result.reasons == []


def test_no_scope_arg_is_slice16_behaviour() -> None:
    # Without the scope/target args the function is byte-for-byte Slice 16: an otherwise
    # autonomous light tool stays autonomous even with an out-of-scope-looking target.
    result = classify(
        _action(tool="httpx", args={"target": "http://example.com"}),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
    )
    assert result.tier is ApprovalTier.AUTONOMOUS
    assert ApprovalReason.OUT_OF_SCOPE not in result.reasons


def test_targetless_command_never_out_of_scope() -> None:
    # A None target_host has no host to test — soft posture never flags it out-of-scope.
    scope = parse_scope("juice-shop")
    result = classify(
        _action(server="shell-exec", tool="run", args={"cmd": "id"}),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
        scope=scope,
        target_host=None,
    )
    assert result.tier is ApprovalTier.AUTONOMOUS
    assert ApprovalReason.OUT_OF_SCOPE not in result.reasons


def test_empty_scope_never_out_of_scope() -> None:
    scope = parse_scope("")  # no declared scope ⇒ nothing is outside it
    result = classify(
        _action(tool="httpx", args={"target": "http://example.com"}),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
        scope=scope,
        target_host="example.com",
    )
    assert result.tier is ApprovalTier.AUTONOMOUS
    assert result.reasons == []


def test_out_of_scope_combines_with_aggressive_scan() -> None:
    scope = parse_scope("juice-shop")
    result = classify(
        _action(server="nmap-server", tool="nmap", args={"target": "http://example.com"}),
        tool_config=ToolConfig(weight="heavy", capability_flags=("network",)),
        scope=scope,
        target_host="example.com",
    )
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert set(result.reasons) == {ApprovalReason.AGGRESSIVE_SCAN, ApprovalReason.OUT_OF_SCOPE}
    assert len(result.reasons) == len(set(result.reasons))  # no duplicates


def test_in_scope_dangerous_command_has_only_danger_reason() -> None:
    # An in-scope but dangerous command gates for its DANGER, not for scope.
    scope = parse_scope("example.com")
    result = classify(
        _action(server="nmap-server", tool="nmap", args={"target": "http://example.com"}),
        tool_config=ToolConfig(weight="heavy", capability_flags=("network",)),
        scope=scope,
        target_host="example.com",
    )
    assert result.reasons == [ApprovalReason.AGGRESSIVE_SCAN]
    assert ApprovalReason.OUT_OF_SCOPE not in result.reasons


def test_out_of_scope_forces_requires_approval_even_if_otherwise_autonomous() -> None:
    scope = parse_scope("10.0.0.0/24")
    result = classify(
        _action(tool="httpx", args={"target": "http://203.0.113.9"}),
        tool_config=ToolConfig(weight="light", capability_flags=("network",)),
        scope=scope,
        target_host="203.0.113.9",
    )
    assert result.tier is ApprovalTier.REQUIRES_APPROVAL
    assert result.reasons == [ApprovalReason.OUT_OF_SCOPE]


# --- validate_tool_manifests ----------------------------------------------------------


def test_validate_tool_manifests_flags_unclassified() -> None:
    tools = [
        ("good-light", ToolConfig(weight="light", capability_flags=("network",))),
        ("good-heavy", ToolConfig(weight="heavy")),
        ("bad-unclassified", ToolConfig(weight=None, capability_flags=())),
        ("bad-weightless-with-flag", ToolConfig(weight=None, capability_flags=("network",))),
    ]
    flagged = validate_tool_manifests(tools)
    assert flagged == ["bad-unclassified", "bad-weightless-with-flag"]


def test_validate_tool_manifests_empty_input() -> None:
    assert validate_tool_manifests([]) == []
