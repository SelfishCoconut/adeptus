"""The pure two-tier classifier (§5.2) — the safety boundary of Slice 16.

``classify(action, *, tool_config)`` maps a proposed command to ``AUTONOMOUS`` (run
immediately) or ``REQUIRES_APPROVAL`` (gate) with a typed list of reasons. It is pure:
inputs are the parsed ``ProposedAction`` + the resolved manifest ``ToolConfig``
(``weight`` + ``capability_flags``), no I/O, no DB.

**Inverted default (Resolved decision 2):** a command is AUTONOMOUS *unless* it matches an
explicit dangerous predicate — ``weight=heavy``, a dangerous capability flag, membership
on a dangerous list/preset, or a credential arg-signal. The only AUTONOMOUS tools are those
with a present, non-dangerous classification (a present ``weight`` + no dangerous flag/list/arg).

**Layered fail-safe for an un-manifested tool.** The safety story for the inverted default
has two layers, belt-and-suspenders:

1. **Live enforcement (fail-closed at config load):** the MCP registry parser REQUIRES a
   present, valid ``weight`` (``light``/``heavy``) for every tool — a tool with no/invalid
   weight raises ``ConfigError`` and the server does not register it, so it can never be
   proposed or run at all (stricter than gating). This is the authoritative live guarantee.
2. **Defense-in-depth (this module):** the **escape hatch** below gates any ``ToolConfig``
   with no present ``weight`` as ``unclassified_manifest`` rather than letting it run. It
   covers the pure-classifier boundary and any future/alternate resolver that might yield a
   weightless ``ToolConfig`` without going through the strict registry parser; paired with
   ``validate_tool_manifests`` (a loud load-time warning), it ensures "never silently
   autonomous" even if layer 1 is ever relaxed.

``out_of_scope`` is reserved in the reason enum but **never returned here** — Slice 17
adds the scope check that appends it. This module is the single boundary Slice 18's
standing-autonomy toggle will short-circuit and Slice 17's scope check will extend.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.features.approvals import config
from app.features.approvals.schemas import (
    ApprovalReason,
    ApprovalTier,
    ClassificationResult,
    ProposedAction,
)

logger = logging.getLogger(__name__)

__all__ = ["ToolConfig", "classify", "validate_tool_manifests"]


@dataclass(frozen=True)
class ToolConfig:
    """The manifest classification the classifier consumes — decoupled from mcp's
    ``McpToolConfig`` so the classifier stays pure and mcp-free. ``weight is None`` with
    no ``capability_flags`` models the missing-manifest escape-hatch case.
    """

    weight: str | None = None
    capability_flags: tuple[str, ...] = field(default_factory=tuple)


def _norm(value: str) -> str:
    return value.strip().lower()


def _tool_matches(action: ProposedAction, names: frozenset[str]) -> bool:
    """True if the action's ``tool`` or ``server/tool`` pair is on the given list."""
    tool = _norm(action.tool_name)
    pair = f"{_norm(action.server_name)}/{tool}"
    return tool in names or pair in names


def _flatten_args(value: object) -> list[str]:
    """Recursively stringify every key and scalar in the args tree (for arg-signal scan)."""
    out: list[str] = []
    if isinstance(value, dict):
        for key, val in value.items():
            out.append(str(key))
            out.extend(_flatten_args(val))
    elif isinstance(value, list | tuple):
        for item in value:
            out.extend(_flatten_args(item))
    else:
        out.append(str(value))
    return out


def _arg_signal(action: ProposedAction, signals: frozenset[str]) -> bool:
    haystack = " ".join(_flatten_args(action.args)).lower()
    return any(sig in haystack for sig in signals)


def classify(action: ProposedAction, *, tool_config: ToolConfig) -> ClassificationResult:
    """Classify one proposed command into the two-tier risk model (§5.2)."""
    reasons: list[ApprovalReason] = []
    flags = {_norm(f) for f in tool_config.capability_flags}
    weight = _norm(tool_config.weight) if tool_config.weight is not None else None
    preset = _norm(action.preset_name) if action.preset_name else None

    # §5.2 — Active scans likely to trigger IDS/IPS or DoS (aggressive nmap, heavy fuzzing).
    if (
        weight == "heavy"
        or (preset is not None and preset in config.AGGRESSIVE_PRESETS)
        or _tool_matches(action, config.AGGRESSIVE_SCAN_TOOLS)
    ):
        reasons.append(ApprovalReason.AGGRESSIVE_SCAN)

    # §5.2 — Writes/modifications to the target (exploits, uploads, persistence).
    if (flags & config.TARGET_WRITE_FLAGS) or _tool_matches(action, config.TARGET_WRITE_TOOLS):
        reasons.append(ApprovalReason.TARGET_WRITE)

    # §5.2 — Credential attacks (brute force, password spraying).
    if (
        (flags & config.CREDENTIAL_FLAGS)
        or _tool_matches(action, config.CREDENTIAL_ATTACK_TOOLS)
        or _arg_signal(action, config.CREDENTIAL_ARG_SIGNALS)
    ):
        reasons.append(ApprovalReason.CREDENTIAL_ATTACK)

    # Fail-safe escape hatch (Resolved decision 2): a tool with NO present weight was
    # never classified — gate it even absent any dangerous signal so it can never run
    # ungated under the inverted default. (A present weight=light/heavy IS a validated
    # classification; a light tool with no dangerous flag stays autonomous.)
    if not reasons and weight is None:
        reasons.append(ApprovalReason.UNCLASSIFIED_MANIFEST)

    if reasons:
        # Dedupe defensively (preserve first-seen order) so overlapping config lists can
        # never surface the same reason twice on the card.
        deduped: list[ApprovalReason] = []
        for reason in reasons:
            if reason not in deduped:
                deduped.append(reason)
        return ClassificationResult(tier=ApprovalTier.REQUIRES_APPROVAL, reasons=deduped)
    return ClassificationResult(tier=ApprovalTier.AUTONOMOUS, reasons=[])


def validate_tool_manifests(tools: Iterable[tuple[str, ToolConfig]]) -> list[str]:
    """Flag (and loudly warn about) tools with no present manifest classification.

    Called at MCP registry load (task 7). A tool with ``weight is None`` was never given
    a load-bearing classification; it will gate at runtime via the ``unclassified_manifest``
    escape hatch until its manifest is fixed. Returns the list of offending tool names so
    the caller (and tests) can assert on it.
    """
    unclassified: list[str] = []
    for name, cfg in tools:
        if cfg.weight is None:
            unclassified.append(name)
            logger.warning(
                "MCP tool %r has no manifest weight; it will REQUIRE APPROVAL via the "
                "unclassified_manifest escape hatch until its manifest declares a "
                "weight (light|heavy). Fix the server manifest to restore autonomy.",
                name,
            )
    return unclassified
