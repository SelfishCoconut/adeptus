# Slice 17: Soft scope enforcement

**Branch**: `slice-17-soft-scope-enforcement`
**GitHub Issue**: #46
**Status**: in-progress
**Risky**: no

---

## Goal

When the AI proposes a command against a target that is **outside the engagement's declared
scope list**, the existing approval gate fires with an `out_of_scope` reason so the command
**warns and requires an explicit human confirmation** before it can run (§5.2 soft scope).

## User-visible demo

After this slice is merged, with `make dev` up (Ollama reachable, `qwen3.5:9b` pulled), in an
engagement that already works as in Slices 11–16:

- Open an engagement whose **scope** (set in the create wizard / settings) is e.g.
  `juice-shop, 10.0.0.0/24, *.target.test`. The persistent privacy banner and the chat /
  approvals machinery behave exactly as in Slice 16.
- Ask the AI to run an **otherwise-autonomous** recon command (e.g. a `light`, safe-flagged
  `httpx`) **against an in-scope target** (`http://juice-shop:3000`). It runs **automatically**
  — the "running automatically" card from Slice 16 — because the target is in scope and the
  command is not otherwise dangerous (§5.2 autonomous).
- Ask the AI to run the **same otherwise-autonomous command against an out-of-scope target**
  (e.g. `http://example.com`). Now an **inline approval card appears** (the Slice-16 card),
  and its reason reads **"target is outside the declared scope"** (`out_of_scope`). The command
  does **not** run until a member explicitly approves — this is the §5.2 *soft* posture: not a
  hard block, a warn-and-confirm. The card shows the resolved out-of-scope host and the
  engagement's scope so the confirmer knows exactly what they are signing off.
- Approve the out-of-scope card → it flips to **"Approved by @you"** and the command runs (the
  Slice-16 approve path, attributed to the initiator; the `approval_granted` audit entry carries
  `self_approved` and is attributed to the decider). Reject it → **"Rejected by @you"**, never
  runs. **No new approval UI is introduced** — scope reuses the Slice-16 card / tab / endpoints.
- Ask for an **already-dangerous** command (e.g. an aggressive scan or credential attack from
  Slice 16) **against an out-of-scope target**. The card lists **both** reasons — e.g.
  "aggressive scan" **and** "target is outside the declared scope" — because the classifier
  combines reasons (Slice 16 already supports multi-reason cards).
- Ask for a command **against an in-scope target** that is also dangerous → the card lists only
  the dangerous reason(s), **not** `out_of_scope` (an in-scope dangerous command is gated for
  its danger, not its scope).
- Engagement with **no scope declared** (empty/blank `scope`) → scope never fires
  (`out_of_scope` is never added); only the Slice-16 dangerous categories gate. (Soft posture:
  with no declared scope there is nothing to be "outside" of.)
- As an admin, open the **Audit tab** (Slice 10): an approved/rejected out-of-scope command
  produced exactly one `approval_granted` / `approval_rejected` entry (Slice 16 wiring,
  unchanged) whose `reasons` payload now includes `out_of_scope`. `make verify-audit` still
  returns OK (no audit-schema change).

## Out of scope

This slice ships **only the scope arm of §5.2** — computing `out_of_scope` and feeding it into
the already-built classifier → gate → approval → audit pipeline. It deliberately does NOT:

- **Does NOT add any new endpoint, table, approval card, or Approvals-tab UI.** The
  `out_of_scope` reason is rendered by the **existing** Slice-16 `ApprovalCard` / `ApprovalQueue`
  and decided by the **existing** `POST .../approve` / `.../reject`. The only frontend change is a
  human-readable label for the (already-enum'd) `out_of_scope` reason and the new scope-context
  fields on the card.
- **Does NOT make scope enforcement hard.** §5.2 is explicit: scope is **soft** — "AI warns and
  asks for explicit confirmation before touching out-of-scope targets." An out-of-scope target
  **gates** (an approval card the human can approve); it is **never auto-blocked or 403'd**. The
  hard `SandboxGuardViolation` (mcp `_enforce_sandbox_guard`, a dev/test-only 403) is a
  **separate, unrelated** mechanism and is NOT touched, broadened, or coupled to scope here (see
  Design notes — "Scope is not the sandbox guard").
- **Does NOT change the autonomy default, the dangerous lists, or the `unclassified_manifest`
  escape hatch** (Slice 16, Resolved decision 2). Scope only **appends** `out_of_scope`; it never
  removes a dangerous reason and never makes a dangerous command autonomous.
- **Does NOT change the engagement scope storage to a structured field.** Scope stays the
  existing free-text `engagements.scope` column (IPs/domains, §4 wizard). This slice adds a
  **parser** over that free text — no migration, no schema change (see Open questions for the
  structured-scope alternative).
- **Does NOT implement the delegation / standing-autonomy pattern** (Slice 18). "Always approve
  out-of-scope" is a Slice-18 delegation category that reuses the Slice-16 classifier seam; not
  built here. Every out-of-scope command gates, every time.
- **Does NOT add scope editing UI.** Scope is already set in the Slice-01 create wizard and the
  engagement settings; this slice consumes the existing value. No new scope editor.
- **Does NOT block manual (human-initiated) tool runs by scope.** §5.2 scope enforcement governs
  **AI-proposed** commands (the autonomy model). A member's own manual `POST /tool-runs` is a
  deliberate human action and is governed by the existing sandbox guard, not this soft AI gate.
  (See Open questions — confirm this boundary.)
- **Does NOT add `target` extraction for commands that carry no `target` arg.** A command with no
  resolvable target host (e.g. a bare `run_command` shell) cannot be classified out-of-scope by
  this slice (it has no host to compare). It still gates on its Slice-16 dangerous reasons
  (`shell-exec` → `target_write`). See Design notes.

## Requirements traceability

§5.2's fourth dangerous category — the scope arm — is the entire subject of this slice; Slice 16
classified the other three and **reserved** `out_of_scope`. Each cited clause:

- **§5.2 — Dangerous commands (fourth bullet)** — quoted:
  > * Anything against a target outside the explicit scope list.

  This slice computes that "outside the explicit scope list" predicate: the proposed command's
  resolved **target host** (via `mcp.concurrency.parse_host` — the canonical, userinfo-smuggling-
  safe extractor already used by the per-target lock and the sandbox guard) is matched against the
  parsed **engagement scope list**. A miss appends the `out_of_scope` reason and routes the command
  to `requires_approval`. The match honours bare hosts, IPv4 addresses, **CIDR ranges**, and
  **domain / wildcard** entries parsed from the free-text scope.

- **§5.2 — Scope enforcement (soft)** — quoted:
  > **Scope enforcement:** **soft** — AI warns and asks for explicit confirmation before touching
  > out-of-scope targets.

  "Soft" is the load-bearing word. Out-of-scope does **not** block; it produces the **same
  approval card** as any other dangerous reason — a warning plus an explicit approve/reject —
  reusing the entire Slice-16 gate. The human can always approve an out-of-scope command (e.g. a
  legitimately newly-in-scope host the scope text hasn't caught up to). This is implemented by
  **appending a reason**, never by raising a block.

- **§5.2 — Two-tier risk model / Approval flow / Who approves / Attribution / Rejections** —
  unchanged from Slice 16. An out-of-scope command is just another `requires_approval` command: it
  creates a `pending` `approval_requests` row, renders the inline card, any engagement member
  approves/rejects, attribution + `self_approved` are recorded, no time-out. This slice adds **no**
  new flow — it widens the set of commands that enter the existing flow.

- **§14 — Audit log records every approval/rejection with attribution** — quoted:
  > Records every tool run, AI call, graph edit, login, and approval/rejection — with user
  > attribution. Approval entries include the `self_approved` boolean (§5.2).

  Unchanged mechanism. The `approval_granted` / `approval_rejected` audit entry for an out-of-scope
  decision is emitted by the Slice-16 `decide(...)` path; its `payload["reasons"]` now simply
  contains `out_of_scope` (the reason list is already serialised into the payload). No new audit
  action, no audit-schema change, chain integrity preserved (`make verify-audit`).

- **§4 — Engagement scope (creation flow)** — quoted:
  > **Creation flow:** wizard — name, scope (IPs/domains), client info, privacy mode, AI persona
  > → create.

  The free-text scope captured by the Slice-01 wizard (`engagements.scope`) is the **single source
  of truth** for the scope list. This slice reads it and parses it; it does not change how scope is
  captured or stored.

- **§17.2 — Human in the loop where it matters; expands scope involves a human** — quoted:
  > Anything that touches a target destructively, **expands scope**, or alters shared truth involves
  > a human. Any engagement member can act as that human — attribution is recorded, not gated.

  An out-of-scope command *expands scope* — exactly the §17.2 case that "involves a human." The
  soft gate **is** that human-in-the-loop: any member can approve (attribution recorded), it is not
  narrowed to an admin/initiator. This slice realises §17.2's "expands scope" clause.

- **§17.1 / §17.5 — engagement isolation; privacy posture visible** — the scope list is read from
  the caller's own engagement only (the classify call site already holds the membership-verified
  engagement). No cross-engagement scope leakage. The out-of-scope card surfaces the engagement's
  own scope to the confirmer (no other engagement's data).

- **Reuses the Slice-16 reserved seam** — the `OUT_OF_SCOPE` value already exists in
  `ApprovalReason` (schema + OpenAPI), the classifier already documents "Slice 17 adds the scope
  check that appends it," and `create_requests_for_turn` is the documented seam. This slice fills
  the reserved seam without re-opening the Slice-16 contract for the reason enum.

## Contract

**No new endpoints. No new top-level schemas.** The `out_of_scope` value is already in the
`ApprovalReason` enum from Slice 16 (so it is already in `frontend/openapi.json` and the generated
types). This slice makes a **small additive change** to the `ApprovalRequest` schema so the
out-of-scope card can show *why* it is out of scope (the resolved host + the engagement scope it was
matched against), for an informative confirmation prompt. Because this is a schema field add,
`make generate-api` is still required.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 17 delta
  version: "0.17.0"

components:
  schemas:
    # CHANGED (approvals feature): two OPTIONAL, nullable scope-context fields so the inline
    # card / Approvals tab can render an informative out-of-scope confirmation prompt. Both are
    # null for every non-out_of_scope request (the existing Slice-16 dangerous reasons), so the
    # change is purely additive and back-compatible with stored Slice-16 rows.
    ApprovalRequest:
      type: object
      # ... all existing Slice-16 fields unchanged (id, engagement_id, chat_message_id,
      #     initiator_user_id, server_name, tool_name, args, preset_name, rationale, reasons,
      #     status, acted_by_user_id, acted_by_username, self_approved, tool_run_id, created_at,
      #     decided_at) ...
      properties:
        out_of_scope_host:
          oneOf: [{ type: string }, { type: "null" }]
          description: >-
            The resolved target host that was outside the declared scope (lower-cased, no port —
            from mcp.concurrency.parse_host). Non-null ONLY when `reasons` contains `out_of_scope`;
            null otherwise. Shown on the card so the confirmer sees exactly which host is
            out-of-scope (§5.2 soft — informative warning before confirmation).
        scope_checked_against:
          oneOf: [{ type: string }, { type: "null" }]
          description: >-
            The engagement's declared scope text the host was matched against, echoed verbatim
            (no redaction, §5.5) for the confirmation prompt. Non-null only alongside
            `out_of_scope_host`.

    # ApprovalReason is UNCHANGED — `out_of_scope` was already present (reserved) in Slice 16.
    # This slice merely starts PRODUCING it.
```

No WebSocket frame contract change: the Slice-16 `proposed_action` frame already carries an
`ApprovalRequest` (gated) — it now simply may carry one whose `reasons` include `out_of_scope`
plus the two new context fields.

## Data model changes

**No migration in the primary design.** Scope stays the existing free-text `engagements.scope`
column; this slice adds a parser over it, not a new column.

The two new `ApprovalRequest` fields (`out_of_scope_host`, `scope_checked_against`) are persisted on
the **existing** `approval_requests` table (NOT a new table, and NOT a column on any shared entity —
they are properties of the request itself, consistent with the Slice-16 "the approval request IS the
attribution row" rule and the §8.2 / §17.4 no-provenance-smear rule). So there is a **single tiny
migration**:

- `approval_requests` table — add two columns:
  - `out_of_scope_host` VARCHAR(253) NULL — the resolved out-of-scope host (max DNS name length);
    null unless `out_of_scope` is among `reasons`.
  - `scope_checked_against` TEXT NULL — the engagement scope text echoed for the prompt; null
    unless out-of-scope.

  No FK, no index (these are render-only fields read with the row by PK / the existing
  engagement+status index). Written via the `write-alembic-migration` skill (register nothing new
  in `env.py` — `approval_requests` is already imported from Slice 16; recreate the autogenerated
  file as the non-root user per the Alembic-autogenerate memory). `make migrate` applies it cleanly;
  `alembic downgrade -1` drops the two columns.

**No change** to `engagements`, `chat_messages`, `audit_entries`, `tool_runs`, or any `graph_*` /
`findings` table. The audit reason list already rides in the existing `audit_entries.payload`
JSON — no audit-schema change (so `make verify-audit` is unaffected).

## Tasks

Tasks are tracked by git, not checkboxes — every commit subject cites its task id, e.g.
`feat(slice-17): add scope parser (task 1)`. Numbered continuously across the whole slice
(backend then frontend).

### Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[M]** Add `app/features/approvals/scope.py` — a **pure** scope parser + matcher, the only
   genuinely new logic in this slice:
   - `parse_scope(raw: str) -> ScopeList` — tolerant parse of the free-text `engagements.scope`
     into a normalised matcher. Splits on commas / whitespace / newlines; classifies each entry as
     (a) a bare host / IPv4 / IPv6 literal, (b) an **IPv4/IPv6 CIDR** (via `ipaddress`), or (c) a
     **domain / wildcard** pattern (`*.target.test`, `target.test`, lower-cased, leading-`*`
     suffix-match). Strips schemes/ports/paths from entries by reusing `parse_host` where an entry
     looks like a URL. A blank/whitespace-only scope parses to an **empty** `ScopeList`.
   - `is_in_scope(host: str, scope: ScopeList) -> bool` — `True` if `host` matches any entry: exact
     host/IP equality, CIDR membership (parse `host` as an IP and test `in network`; a non-IP host
     never matches a CIDR), or domain/wildcard suffix match (`a.b.target.test` matches
     `*.target.test` and `target.test`). Case-insensitive throughout.
   - **Empty-scope policy (soft, load-bearing):** `is_in_scope(host, empty_scope)` returns `True`
     (an engagement with no declared scope has nothing to be "outside" of — never flag, never block;
     the soft posture). Documented inline; pinned by a test.
   - Heavily unit-tested in `tests/test_scope.py`: `test_parse_comma_and_whitespace_separated`,
     `test_bare_host_exact_match`, `test_ipv4_exact_match`, `test_cidr_membership`,
     `test_non_ip_host_never_matches_cidr`, `test_domain_exact_match`,
     `test_wildcard_suffix_match`, `test_wildcard_does_not_match_parent_sibling`,
     `test_entry_with_scheme_and_port_is_stripped`, `test_case_insensitive`,
     `test_empty_scope_is_always_in_scope` (the soft policy — load-bearing),
     `test_malformed_entry_is_ignored_not_raised` (tolerant; never throws on weird scope text).
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_scope.py`).

2. **[M]** Extend `app/features/approvals/classifier.py` — thread an **optional** scope check into
   the existing pure `classify(...)`:
   - Signature: `classify(action, *, tool_config, scope: ScopeList | None = None,
     target_host: str | None = None) -> ClassificationResult`. When `scope` is non-`None` AND
     `target_host` is non-`None` AND `not is_in_scope(target_host, scope)`, **append**
     `ApprovalReason.OUT_OF_SCOPE` to `reasons` (so it combines with any dangerous reason — Slice 16
     already dedupes/orders) and force `tier = REQUIRES_APPROVAL`.
   - **Soft / additive invariants (load-bearing):** scope only ever **adds** `out_of_scope`; it
     never removes a reason and never downgrades a `requires_approval` to autonomous. When `scope` or
     `target_host` is `None`, behaviour is **identical to Slice 16** (backward compatible — the
     scope check is opt-in at the call site, the pure function stays mcp-free and engagement-free).
     A targetless command (`target_host is None`) is **never** out-of-scope (it has no host to test —
     soft posture).
   - Keep `classify` pure (no I/O, no DB, no mcp import beyond the existing `ToolConfig`). The
     `ScopeList` type comes from the new `scope.py`.
   - Tests added to `tests/test_classifier.py`:
     `test_out_of_scope_host_appends_out_of_scope_reason`,
     `test_in_scope_host_does_not_append`,
     `test_no_scope_arg_is_slice16_behaviour`,
     `test_targetless_command_never_out_of_scope`,
     `test_out_of_scope_combines_with_aggressive_scan` (multi-reason),
     `test_in_scope_dangerous_command_has_only_danger_reason`,
     `test_empty_scope_never_out_of_scope`,
     `test_out_of_scope_forces_requires_approval_even_if_otherwise_autonomous`.
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_classifier.py`).

3. **[S]** Extend `app/features/approvals/schemas.py` + `models.py` — add `out_of_scope_host:
   str | None = None` and `scope_checked_against: str | None = None` to `ApprovalRequestRead`
   (with `from_attributes`) and the two nullable columns to the `ApprovalRequest` ORM model
   (per the Data model section). Update the enum/DB parity test if needed (no enum change).
   Tests in `tests/test_schemas.py`: the two fields default to `None`; a row with `out_of_scope`
   carries a non-null `out_of_scope_host`.
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_schemas.py`).

4. **[S]** Extend `app/features/approvals/repository.py` — `create_request(...)` gains optional
   `out_of_scope_host` / `scope_checked_against` parameters persisted on the new columns
   (null by default, so Slice-16 call paths are unchanged). Tests in `tests/test_repository.py`:
   `test_create_persists_out_of_scope_context`, `test_create_without_scope_context_is_null`.
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_repository.py`).

5. **[M]** Wire scope into `app/features/approvals/service.py` `create_requests_for_turn(...)` —
   the call site that already loads per-turn context:
   - Load the engagement once (it already has `engagement_id`) and `parse_scope(engagement.scope)`
     **once per turn** (not per action). A non-member / missing engagement is impossible here (the
     chat service already verified membership before proposing), but read defensively.
   - For each action, resolve `target_host = mcp.concurrency.resolve_target_host(action.server_name,
     action.tool_name, action.args)` (the canonical extractor — reuses the lock/sandbox host logic,
     userinfo-smuggling-safe). Pass `scope=` and `target_host=` into `classify(...)`.
   - When the result's reasons include `out_of_scope`, persist `out_of_scope_host=target_host` and
     `scope_checked_against=engagement.scope` on the created request (task 4). For non-out-of-scope
     gated actions, leave both null.
   - The autonomous / gated routing, the audit emission on decision, and the `decide(...)` path are
     **unchanged** — an out-of-scope action simply lands in the `gated` list and flows through the
     existing Slice-16 machinery.
   - Tests in `tests/test_service.py` (mcp/audit mocked; `get_registry` stubbed with an in-scope and
     an out-of-scope target):
     `test_out_of_scope_autonomous_command_is_gated_with_out_of_scope_reason`,
     `test_in_scope_autonomous_command_still_runs`,
     `test_out_of_scope_context_persisted_on_request`,
     `test_dangerous_and_out_of_scope_combines_reasons`,
     `test_empty_engagement_scope_never_gates_on_scope`,
     `test_targetless_command_not_gated_for_scope` (a `run_command` with no `target` arg is not
     out-of-scope; it still gates on `shell-exec` → `target_write` per Slice 16),
     `test_scope_parsed_once_per_turn` (parse called once for a multi-action turn).
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_service.py`).

6. **[S]** Add the Alembic migration for the two `approval_requests` columns via the
   `write-alembic-migration` skill. Confirm `make migrate` applies cleanly and
   `alembic downgrade -1` drops the columns. Confirm `make verify-audit` still returns OK (no
   audit-schema change).
   - Test command: `make migrate` then `alembic downgrade -1` (in the backend container).

### Frontend tasks

Numbering continues from the backend tasks.

7. **[S]** Run `make generate-api` to regenerate types into `frontend/src/shared/api/`; commit the
   updated `frontend/openapi.json` snapshot (adds `out_of_scope_host` / `scope_checked_against` to
   `ApprovalRequest`; `out_of_scope` was already in `ApprovalReason`). Note the
   OpenAPI-nullable-field handling — both are optional/nullable so no consumer becomes required.
   - Test command: `make generate-api` then `make lint`.

8. **[M]** Extend `frontend/src/features/approvals/components/ApprovalCard.tsx` (Slice 16) — add a
   human-readable label for the `out_of_scope` reason (e.g. **"target is outside the declared
   scope"**) to the existing reason-label map, and, when that reason is present, render a small
   **scope-context line** showing `out_of_scope_host` and `scope_checked_against` (e.g. "example.com
   is not in scope: juice-shop, 10.0.0.0/24, \*.target.test"). No new card, no new buttons — the
   existing Approve/Reject path decides it. Tests in `ApprovalCard.test.tsx`:
   `renders out_of_scope reason label`, `shows the out-of-scope host and scope context`,
   `shows both danger and out_of_scope labels when combined`,
   `does not render scope context for a non-out-of-scope request`.
   - Test command: `make test-frontend` (`vitest run src/features/approvals/components/ApprovalCard.test.tsx`).

9. **[S]** Confirm the Slice-16 `ApprovalQueue.tsx` (Approvals tab) renders an out-of-scope request
   with the new label via the shared `ApprovalCard` (no code change expected beyond what task 8
   provides — add a regression test only). Tests in `ApprovalQueue.test.tsx`:
   `lists an out-of-scope pending request with the scope reason label`.
   - Test command: `make test-frontend` (`vitest run src/features/approvals/components/ApprovalQueue.test.tsx`).

10. **[S]** Verify coverage ≥ 60% on `src/features/approvals/`; `make lint` clean (no `any`).
    Confirm the Slice-16 chat inline-card render, the Slice-02 banner, and all earlier panels are
    untouched except the additive reason label + scope-context line.
    - Test command: `make test-frontend` then `make lint`.

## Test plan

- **Unit — backend** (coverage ≥ 80% on the new `scope.py` + the changed classifier/service lines):
  - `tests/test_scope.py` — the densest coverage in the slice: the parser (comma/whitespace/newline
    splitting, host/IPv4/IPv6/CIDR/domain/wildcard classification, scheme/port stripping,
    case-insensitivity, tolerant handling of malformed entries) and the matcher (exact, CIDR
    membership, non-IP-vs-CIDR, wildcard suffix vs parent/sibling, **empty scope is always in
    scope** — the soft policy). This is the safety-relevant boundary of the slice.
  - `tests/test_classifier.py` — the scope-arm `test_*` names in backend task 2: append-on-miss,
    no-append-in-scope, Slice-16-identical when no scope arg, targetless-never-out-of-scope,
    multi-reason combination, in-scope-dangerous-has-only-danger, empty-scope-never, and the
    force-`requires_approval`-on-out-of-scope.
  - `tests/test_service.py` — the call-site `test_*` names in backend task 5: out-of-scope gating,
    in-scope passthrough, context persistence, danger+scope combination, empty-scope no-gate,
    targetless no-scope-gate, parse-once-per-turn.
  - `tests/test_schemas.py` / `tests/test_repository.py` — the field defaults + persistence tests.
- **Unit — frontend** (coverage ≥ 60% on `src/features/approvals/`):
  - `ApprovalCard.test.tsx` — out-of-scope reason label; host + scope context line; combined
    danger+scope labels; no context line for non-out-of-scope.
  - `ApprovalQueue.test.tsx` — out-of-scope request listed with the scope label.
- **Integration** (`@pytest.mark.integration`, real Postgres; **Ollama + MCP subprocess mocked** —
  external services never hit; pentest tools never run against external targets, only the sandbox,
  CLAUDE.md):
  - `test_out_of_scope_command_gated_then_approved_executes` — an engagement whose scope is
    `juice-shop`; POST a chat message; stream a **faked Ollama reply carrying a `propose_command`
    tool-call** for an otherwise-autonomous `httpx` against `http://example.com`; assert a `pending`
    `approval_requests` row is created with `reasons=[out_of_scope]`, `out_of_scope_host='example.com'`,
    and **no** tool run yet; approve as the initiator; assert one `approval_granted` audit entry
    (attributed to the decider, `self_approved=true`) whose `payload.reasons` contains `out_of_scope`,
    the request `approved`, and the run executes via the pipeline. **Headline §5.2-soft + §14
    happy-path.**
  - `test_in_scope_autonomous_command_runs_without_request` — same engagement; faked reply targets
    `http://juice-shop:3000` (in scope) with the same safe `httpx`; assert **no** `approval_requests`
    row and the run executes (the Slice-16 autonomous path is preserved for in-scope targets).
  - `test_out_of_scope_combines_with_dangerous_reason` — faked reply proposes an aggressive scan
    against an out-of-scope host; assert the `pending` request's `reasons` contains **both**
    `aggressive_scan` and `out_of_scope`.
  - `test_empty_scope_does_not_gate_on_scope` — an engagement with blank scope; faked reply targets
    an arbitrary host with a safe `httpx`; assert it runs autonomously (no scope gate).
  - `test_audit_chain_intact_after_out_of_scope_decisions` — after approving/rejecting some
    out-of-scope commands, `verify.run()` returns OK (Slice-10/§14 guarantee preserved; reasons ride
    the existing payload, no schema change).
- **E2E** (Playwright) — **DEFERRED (accepted at finish-slice; plan corrected to match reality).**
  This bullet was written assuming a Slice-16 `approvals.spec.ts` to extend, but **no approval E2E
  exists** — Slice 16 shipped the entire approval flow (the `proposed_action` WS frame, the inline
  `ApprovalCard`, the Approvals tab, approve/reject) with no Playwright journey. A faithful
  out-of-scope E2E needs the model to emit a **specific** `propose_command` tool-call for an
  out-of-scope target, but the E2E harness only points `ADEPTUS_OLLAMA_URL` at an external stub that
  streams a fixed reply (`chat.spec.ts`); a spec cannot deterministically drive a particular
  tool-call without **controllable Ollama-stub infrastructure that does not exist in the repo**.
  Building that first-ever approval-E2E harness is disproportionate to this small scope-arm slice and
  cannot be run/verified locally (the 15 stack-dependent journeys skip without `E2E_STACK=1`). The
  scope arm is instead covered by: dense **unit** tests (`test_scope.py` parser/matcher;
  `test_classifier.py` scope arm), **integration** tests against real Postgres
  (`test_integration.py` — gate → approve → run → audit, chain-intact), and **RTL DOM** tests
  asserting the rendered card/queue (`ApprovalCard.test.tsx`, `ApprovalQueue.test.tsx`). A
  controllable-Ollama-stub approval E2E is **owed by the approvals feature broadly** (not introduced
  by this slice) and is tracked as a follow-up. `make test` still runs Playwright (smoke passes, the
  stack-gated journeys skip) so the gate stays green.

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage gates
  hold (≥80% backend on the new `scope.py` + changed classifier/service; ≥60% frontend
  `approvals`).
- `make lint` passes with no new errors.
- `make migrate` applies the two-column `approval_requests` migration cleanly against a fresh
  Postgres container; `alembic downgrade -1` reverts it.
- `make generate-api` produces an updated `frontend/openapi.json` containing the two new
  `ApprovalRequest` fields; regenerated types committed.
- `make verify-audit` exits `0` on a chain that includes `approval_*` entries whose reasons include
  `out_of_scope` (no audit-schema change; §14 tamper-evidence preserved).
- `make dev` brings up the stack; manual demo:
  1. Engagement scope set to e.g. `juice-shop, 10.0.0.0/24, *.target.test`.
  2. Ask for a safe `httpx` against `http://juice-shop:3000` (in scope) → runs **automatically**
     (no gate).
  3. Ask for the **same** safe `httpx` against `http://example.com` (out of scope) → an inline
     **approval card** appears with reason **"target is outside the declared scope"** and shows the
     host + scope; the command does **not** run yet (§5.2 soft).
  4. **Approve** → "Approved by @you", the command runs (the run is attributed to the initiator; the
     `approval_granted` audit entry to the decider, `self_approved=true`, reasons include
     `out_of_scope`).
  5. Ask for an aggressive scan against an out-of-scope host → the card lists **both** "aggressive
     scan" and "target is outside the declared scope".
  6. **Reject** an out-of-scope proposal → "Rejected by @you", the command is never executed.
  7. `make verify-audit` → OK with the out-of-scope decisions woven into the chain.
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — Scope parsing false-negatives (a genuinely out-of-scope target read as in-scope).**
  The worst failure for this slice: an out-of-scope command runs ungated because the parser/matcher
  wrongly matched. Mitigation: the matcher uses `ipaddress` for CIDR (not string prefixing), exact
  equality for hosts/IPs, and an explicit suffix rule for wildcards
  (`test_wildcard_does_not_match_parent_sibling` guards over-broad matching); host extraction reuses
  the audited userinfo-smuggling-safe `parse_host`; the parser is pure and densely unit-tested. The
  posture is **soft anyway** — a missed gate means an autonomous command ran on an out-of-scope host,
  which is the same exposure the spec accepts for *in-scope* autonomous commands; the sandbox guard
  still hard-blocks non-sandbox hosts in dev/test independently.
- **Risk 2 — Scope parsing false-positives (an in-scope target read as out-of-scope) annoying the
  user.** A too-strict parser gates legitimate in-scope commands, training users to rubber-stamp
  approvals. Mitigation: tolerant parsing (malformed entries ignored, not fatal); scheme/port/path
  stripping; case-insensitive; **empty scope is always in scope** so an unconfigured engagement never
  gates; the human can always approve (soft). Guarded by `test_*_in_scope_*` and
  `test_empty_scope_*`.
- **Risk 3 — Host extraction disagreeing with the lock / sandbox host (drift).** If scope used a
  *different* host extractor than the per-target lock and sandbox guard, the same command could be
  "in scope" to one and "out" to another. Mitigation: scope reuses `mcp.concurrency.parse_host` /
  `resolve_target_host` verbatim — the single canonical extractor — so the scope host, the lock host,
  and the sandbox host are always identical (the same Risk-5 reconciliation Slice 05/16 already
  pinned).
- **Risk 4 — Treating soft scope as a hard block.** A bug that 403s or auto-drops an out-of-scope
  command would violate §5.2 ("soft — warns and asks for explicit confirmation"). Mitigation: scope
  only ever **appends a reason** to the existing approval gate; there is no new exception, no
  early-return block, no coupling to `SandboxGuardViolation`. `test_out_of_scope_command_gated_then_
  approved_executes` proves the human can approve and run an out-of-scope command.
- **Risk 5 — Targetless commands silently un-scoped.** A command with no `target` arg (bare shell)
  has no host to test, so scope cannot fire — an out-of-scope shell action would not be caught *by
  scope*. Mitigation: such commands already gate on their Slice-16 `shell-exec` → `target_write`
  reason (the shell path is dangerous regardless of host); documented in Out of scope and pinned by
  `test_targetless_command_not_gated_for_scope`. (Extracting an implied target from a shell command
  line is explicitly out of scope — see Open questions.)
- **Risk 6 — IPv6 / unusual scope syntax mishandled.** IPv6 literals and mixed CIDR notations can be
  parsed wrong. Mitigation: delegate IP/CIDR handling to the stdlib `ipaddress` module (handles
  IPv4/IPv6 and CIDR uniformly); a non-IP host simply never matches a CIDR entry; malformed entries
  are ignored, never fatal. Guarded by `test_cidr_membership`, `test_non_ip_host_never_matches_cidr`,
  and `test_malformed_entry_is_ignored_not_raised`.
- **Risk 7 — Echoing scope/host on the card leaks nothing it shouldn't, but must not redact.** The
  card shows the engagement's own scope text and the resolved host verbatim (§5.5 no-redaction). This
  is the caller's own engagement data (no cross-engagement leak, §17.1) and is informative, not
  secret. Mitigation: `scope_checked_against` is echoed verbatim; the field is read-only render
  context on the request the member is already authorised to see.

## Open questions for the human

- **Scope storage — free-text parser now, or a structured scope field?** This slice parses the
  existing free-text `engagements.scope` (no migration to the engagements table). A more robust
  alternative is a **structured scope** (a typed list of host/CIDR/domain entries captured by the
  wizard, validated at entry). The free-text parser is chosen here to keep the slice small and avoid
  re-opening the Slice-01/§4 engagement schema, but it accepts whatever text users typed. *Default
  decision (proceed unless you object): free-text parser this slice; a structured-scope migration is
  a separate future slice (and would slot in behind the same `parse_scope` seam).* Confirm.
- **Does soft scope govern manual (human-initiated) tool runs, or only AI-proposed commands?** §5.2
  scope sits under the **Autonomy Model** (AI-proposed commands), and §17.2 frames the human gate as
  for AI actions; a member's own `POST /tool-runs` is already a deliberate human action governed by
  the sandbox guard. This slice gates **only AI-proposed** commands. *Default decision (proceed
  unless you object): AI-proposed only; manual runs unchanged.* Confirm — if you want manual runs
  scope-warned too, that is an additive follow-up (a confirm-modal on the tool runner), not part of
  this slice.
- **Should scope try to extract an implied target from a bare shell command line** (e.g. parse
  `curl http://example.com` inside a `run_command` arg)? This slice does **not** — only commands with
  a structured `target` arg are scope-checked; shell commands gate on `shell-exec` regardless. *Default
  decision (proceed unless you object): no shell-line target inference this slice.* Confirm.

## Security review required?

**Borderline — recommend a lightweight security review even though PROJECT_PLAN marks slice 17
`Risky: no`.** This slice does **not** itself touch the audit hash-chain, the single-writer, RAG
isolation, egress, secrets, or auth, and it adds **no new approval endpoint or flow** — it only
*produces* a reason that the already-reviewed Slice-16 approval/audit machinery handles. That keeps
it off the mandatory-review list. **However**, it *extends the §5.2 safety classifier* (the approval
boundary), so the reviewer (or a focused self-review at finish-slice) should confirm:

- (a) **scope is soft, not hard** — an out-of-scope command *gates* (an approvable card) and is never
  auto-blocked / 403'd; the human can always approve and run it (Risk 4); the change is purely
  "append a reason," with no new block and no coupling to the `SandboxGuardViolation` hard guard;
- (b) **scope only adds, never subtracts** — `out_of_scope` is appended; no dangerous reason is ever
  removed and no `requires_approval` is downgraded to autonomous by the scope path (Risk 1/2);
- (c) **host extraction matches the lock/sandbox host** — scope reuses `parse_host` /
  `resolve_target_host`, so scope, the per-target lock, and the sandbox guard agree (no drift —
  Risk 3);
- (d) **the matcher is sound** — exact host/IP equality, `ipaddress`-based CIDR membership (non-IP
  never matches a CIDR), suffix-only wildcard matching (no parent/sibling over-match), empty scope ⇒
  always in scope; tolerant parsing never throws (Risk 1/2/6);
- (e) **engagement isolation + no-redaction** — the scope read is the caller's own
  membership-verified engagement only; `scope_checked_against` / `out_of_scope_host` are echoed
  verbatim (§5.5) and carry no other engagement's data (§17.1, Risk 7);
- (f) **audit unchanged + chain intact** — no new audit action/table; the `out_of_scope` reason rides
  the existing `approval_*` payload; `make verify-audit` / `test_audit_chain_intact_after_out_of_
  scope_decisions` pass (§14).

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-05T23:04:49Z — 384a83b Slice 16: Two-tier autonomy + approval flow (#45)
- 2026-06-05T23:04:56Z — 384a83b Slice 16: Two-tier autonomy + approval flow (#45)
- 2026-06-05T23:27:31Z — 485d0dc test(slice-17): ApprovalQueue renders out-of-scope request (task 9)
