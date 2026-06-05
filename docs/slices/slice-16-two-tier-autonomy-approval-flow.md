# Slice 16: Two-tier autonomy + approval flow

**Branch**: `slice-16-two-tier-autonomy-approval-flow`
**GitHub Issue**: #44
**Status**: in-progress
**Risky**: yes (approval flow, audit — step-gated per CLAUDE.md)

---

## Goal

When the AI proposes a command via **native tool-calling** (a `propose_command` tool), a
dangerous proposal posts an inline approval card that any engagement member can approve or
reject — commands that are **not known-dangerous** run autonomously — and every
approve/reject decision is recorded in the hash-chained audit log with the acting user and
the `self_approved` flag.

## User-visible demo

After this slice is merged, with `make dev` up (Ollama reachable, `qwen3.5:9b` pulled;
an engagement that already works as in Slices 11–15):

- Open an engagement workspace. The left chat pane works exactly as in Slice 11–15.
- Ask the AI to do something **autonomous** (recon / passive), e.g. "run httpx against the
  target to see what's up". The AI emits a **native tool-call** to `propose_command`; because
  the proposed tool is **not known-dangerous** (a `light` tool with declared, non-dangerous
  capability flags), an **inline command card** appears in chat marked **"running
  automatically"** and the existing tool-run pipeline (Slice 04/05) executes it — no approval
  gate (§5.2 "Autonomous: recon and passive operations").
- Ask the AI to do something **dangerous**, e.g. "brute-force the login with this wordlist"
  or "run an aggressive nmap scan". The AI proposes the command via the same tool-call, but
  now an **inline approval card** appears in the chat thread showing: the proposed
  `server`/`tool`/`args`, **why it is dangerous** (e.g. "credential attack", "aggressive
  scan", "modifies target"), and two buttons — **Approve** and **Reject**. The command does
  **not** run yet (§5.2 "Approval-gated: dangerous commands").
- The approval card is **pending** until someone acts. It does not time out (§5.2 "Approvals
  do not time out"). Click **Approve**: the card flips to **"Approved by @you"** inline (so
  the initiator is never surprised who signed off, §5.2), and the command is then handed to
  the tool-run pipeline and executes; the result appears in the bottom Console pane as usual.
- Click **Reject** on a different dangerous proposal: the card flips to
  **"Rejected by @you"** and the command is **never** executed.
- **Any member can act.** Open the same engagement as a **second member**: that member sees
  the **same pending approval cards** in a **per-engagement Approvals tab** (visible to all
  members) and can approve or reject the first user's pending proposals. The decision shows
  **"Approved by @second-member"**. (Approval requests are an engagement-shared queue, not
  private chat — see Design notes.)
- **Self-approval is allowed and labeled.** The initiator approving their own proposal
  works; the audit entry for it carries `self_approved=true`. A cross-member approval carries
  `self_approved=false` (§5.2 attribution).
- As an **admin**, open the **Audit tab** (Slice 10): each decision produced exactly one
  `approval_granted` or `approval_rejected` audit entry, attributed to the **decider**, with
  the `self_approved` boolean populated. The executed `tool_run` audit entry is attributed to
  the **initiator** (Resolved decision 3). Toggle the Slice-10 `self_approved` filter to see
  cross-member vs self-approvals (§5.2 "reviewers can filter").
- Run `make verify-audit`: the chain still verifies OK with the new approval entries woven
  in (the Slice-10 tamper-evidence guarantee is preserved — §14).

## Out of scope

This slice ships the **two-tier classification + the approve/reject gate + attribution**.
It deliberately does NOT do the following (each is separately tracked):

- Does **NOT** implement **soft scope enforcement** (Slice 17, `Depends on: 16`). §5.2's
  fourth dangerous category — "anything against a target outside the explicit scope list" —
  is the scope arm; the **classifier in this slice reserves a `out_of_scope` reason as a
  documented seam** but does NOT compute it (no scope-list matching here). Slice 17 plugs the
  scope check into the same classifier and the same approval gate. The three non-scope
  dangerous categories (target writes, aggressive scans, credential attacks) ARE classified
  and gated here.
- Does **NOT** implement the **delegation / standing-autonomy pattern** (Slice 18,
  `Depends on: 16`). §5.2 "Delegation pattern (generalized)" — granting the AI standing
  approval on a category of commands for the rest of the engagement — is Slice 18. This slice
  always gates every dangerous command (no per-category auto-approve toggle); the
  classification → decision seam is built so Slice 18 can short-circuit it later.
- Does **NOT** add the **notifications panel / bell** (Slice 32, `Depends on: 16, 27, 31`).
  §11.7 lists "approval requests" as a notification trigger; this slice surfaces pending
  approvals **inline in chat** and in the per-engagement **Approvals tab** (the §5.2
  mechanism), but the global notification bell that pings teammates is Slice 32. (The
  Approvals tab's list endpoint this slice exposes is the exact data source Slice 32 will
  consume — Resolved decision 4.)
- Does **NOT** build out the **heavy MCP tools themselves** (nmap/gobuster — Slice 26) or
  add new tool servers. It uses whatever tools the MCP config already exposes (Slice 03's
  shell-exec + Slice 04's light tools); the dangerous classifier reads the existing
  manifest `weight` / `capability_flags` (Slice 03/07). The end-to-end demo of a *dangerous*
  run uses the existing shell-exec server (a `shell-exec`-flagged tool) — heavy nmap presets
  arrive in Slice 26 and will classify the same way with no change here.
- Does **NOT** let the AI **write the graph** or take any non-tool destructive action. §8.2
  "AI writes automatically, flagging low-confidence inferences" (graph writes) is a separate
  surface; this slice gates only **AI-proposed tool commands** (the §5.2 "dangerous
  commands" are tool invocations). Graph writes still go only through the single writer
  (ADR-0001) and are not AI-initiated in v1 scope here.
- Does **NOT** change the **audit hash-chain mechanism** (Slice 10). It only *calls* the
  already-reviewed `audit.service.record` chokepoint with the reserved `approval_granted` /
  `approval_rejected` actions and the `self_approved` value. No new audit table, no new
  hashing, no change to the chain construction or the verifier.
- Does **NOT** implement **token/cost tracking** (Slice 36), **@-mentions / presence**
  (Slice 31), or **conversation fork/reset** (deferred §5.4).
- Does **NOT** add **provenance columns** to graph/finding/chat entities (§8.2 / §17.4 /
  CLAUDE.md anti-pattern). Who approved/rejected lives in the **audit log** (the source of
  truth) and, for live rendering, denormalized **on the approval-request row itself** (the
  approval request IS the attribution table — `acted_by_user_id` is its own concept, not a
  provenance tag bolted onto shared truth; same pattern as `chat_messages.user_id`).
- Does **NOT** widen `core/` or `shared/` — all backend code lives under
  `app/features/approvals/` (the new feature) plus thin call-sites in
  `app/features/chat/` and `app/features/mcp/`; all frontend code under
  `src/features/approvals/` plus a thin render hook into `src/features/chat/`.

### Reserved seams / future dependencies (recorded, NOT built here)

- **Native tool-calling reliability fallback (built in this slice).** The proposal mechanism
  is native tool-calling (Resolved decision 1). For a backend/model with weak or no
  tool-calling support, this slice ships a **deterministic instructed-block fallback** so the
  feature still functions — described in Design notes. This is part of THIS slice, listed
  here only to flag the dependency on per-backend tool-calling capability.
- **Future shared/collaborative chat (NOT in this slice, NOT in PROJECT_PLAN yet).** The
  product owner has decided a future direction: chat will become private-by-default with an
  explicit "share" action that turns a chat into a multi-writer collaborative thread for the
  whole engagement. That **reverses §5.4** ("private chat per user") and will be its **own
  ADR + its own slice** (a Slice-11 rework: a `conversations` table, a shared/private flag,
  multi-author message attribution, engagement-write access). **This slice does NOT build it,
  does NOT depend on it, and does NOT add it to PROJECT_PLAN.** For Slice 16, chat stays
  **private per §5.4**; "any engagement member approves" is satisfied entirely by the
  engagement-shared `approval_request` row + the Approvals tab (no shared chat needed — see
  Design notes). When the shared-chat slice lands, the approval card's inline render will be
  visible to every member of a shared thread too, but no Slice-16 contract changes for that.

## Requirements traceability

§5.2 is the headline section and is implemented in full **except the scope arm** (deferred to
Slice 17 by the PROJECT_PLAN dependency) and the delegation arm (Slice 18). Each §5.2 clause:

- **§5.2 — Two-tier risk model** — quoted:
  > **Two-tier risk model:** **Autonomous:** recon and passive operations.
  > **Approval-gated:** dangerous commands.

  A pure classifier (`approvals/classifier.py`) maps a proposed command
  (`server`, `tool`, `args`) to **`autonomous`** or **`requires_approval`** using the MCP
  manifest's `weight` and `capability_flags` (Slice 03/07) plus the dangerous-tool/arg
  heuristics below. The slice adopts the **autonomous-unless-known-dangerous** default
  (Resolved decision 2): a command runs autonomously unless it is explicitly classified
  dangerous — with the strict fail-safe escape hatch that a tool with a **missing/empty
  manifest classification** (no `weight` AND no `capability_flags`) is treated as dangerous.
  Autonomous commands flow straight to the existing tool-run pipeline; approval-gated commands
  create a pending approval request and **block** execution until a decision.

- **§5.2 — Dangerous commands (require approval)** — quoted:
  > * Writes/modifications to the target (exploits, uploads, persistence).
  > * Active scans likely to trigger IDS/IPS or cause DoS (aggressive nmap, heavy fuzzing).
  > * Credential attacks (brute force, password spraying).
  > * Anything against a target outside the explicit scope list.

  The classifier emits a typed **reason** per dangerous command: `target_write` (tool
  declares a target-write / `filesystem-write` / `shell-exec` capability, or is on the
  dangerous-tool list), `aggressive_scan` (tool `weight=heavy`, or an `aggressive`-class
  preset, or matches the aggressive-tool list), `credential_attack` (tool on the
  credential-attack list, e.g. `hydra`/`medusa`/`ffuf`-against-auth, or a `brute`/`spray` arg
  signal), or `unclassified_manifest` (the fail-safe reason for a tool whose manifest carries
  no weight and no capability flags — Resolved decision 2's escape hatch). The fourth §5.2
  bullet — **`out_of_scope`** — is **reserved in the reason enum but not computed here**
  (Slice 17, see Out of scope). The reason is shown on the approval card ("why this needs
  approval") and recorded in the approval-request payload + the audit payload.

- **§5.2 — Approval flow** — quoted:
  > **Approval flow:** inline in chat — AI posts the command, any engagement member clicks
  > approve/reject.

  The AI's proposed command is read from a **native tool-call** to `propose_command`
  (Resolved decision 1; see Design notes), with the instructed-block fallback for weak-tool
  backends. A dangerous proposal creates an **approval request** linked to the assistant turn;
  the frontend renders it as an **inline approval card** in the chat thread (and in the
  per-engagement Approvals tab so a second member can act — Resolved decision 4).
  Approve/reject are two clicks.

- **§5.2 — Who approves** — quoted:
  > **Who approves:** any member of the engagement (including the initiator). Approvals do
  > not time out (sit in the queue until acted on).

  The approve/reject endpoints require **engagement membership only** (not the initiator, not
  admin) — `404` for non-members (§17.1). A pending request has **no expiry** column and no
  reaper; it stays `pending` until acted on. The initiator may approve their own request
  (self-approval allowed, labeled — see attribution).

- **§5.2 — Attribution** — quoted:
  > **Attribution:** every approval and rejection records the acting user. The approval
  > event in chat shows "Approved by @user" inline so the initiator is never surprised about
  > who signed off. The audit log includes a `self_approved` boolean (true when initiator ==
  > approver) so reviewers can filter for cross-member approvals vs self-approvals.

  On decision the request row records `acted_by_user_id` + `decided_at`; the card renders
  "Approved by @user" / "Rejected by @user" inline (resolving the username via the existing
  user lookup). The **audit entry** (`approval_granted` / `approval_rejected`) is attributed
  to the **decider** and carries `self_approved = (acted_by_user_id == initiator_user_id)` —
  the Slice-10 column the read API already filters on (§5.2 "reviewers can filter"). The
  executed `tool_run` and its Slice-10 `tool_run` audit entry are attributed to the
  **initiator** (Resolved decision 3 — "who asked" vs "who signed off" stay distinct).

- **§5.2 — Rejections** — quoted:
  > **Rejections:** symmetric with approvals — any engagement member can reject, attribution
  > recorded the same way.

  Reject is the symmetric path: same membership gate, same attribution, same `self_approved`
  recording, a distinct `approval_rejected` audit action, and the command is never executed.

- **§5.2 — Delegation pattern (generalized)** — reserved seam, **not implemented** (Slice
  18). The classifier → gate boundary is the exact point Slice 18's standing-autonomy toggles
  will short-circuit; documented in Design notes, no toggle UI/storage here.

- **§5.2 — Scope enforcement (soft)** — reserved seam, **not implemented** (Slice 17). See
  Out of scope.

- **§7 — MCP manifest capability hints (load-bearing here for the inverted default)** — quoted:
  > Capability hints in the manifest: every MCP server's manifest declares … (b) each tool's
  > `weight: light | heavy` …, (c) declared capability flags: `network`, `filesystem-write`,
  > `shell-exec`, etc. … The flags are informational, not enforced.

  Slice 16 makes the manifest classification **load-bearing for the autonomy default**
  (Resolved decision 2). A tool is dangerous if its manifest `weight` is `heavy` OR it carries
  a dangerous capability flag OR it is on an explicit dangerous list; everything else is
  autonomous. To make "autonomous-unless-known-dangerous" safe, the slice **requires a present
  manifest classification**: a tool with no `weight` and no `capability_flags` is treated as
  dangerous (`unclassified_manifest`). The §7 flags stay "informational, not enforced" for
  tool execution itself — this slice does not block any tool from running; it only decides
  whether a human gate is interposed.

- **§14 — Audit log records every approval/rejection with attribution; approval entries
  include `self_approved`** — quoted:
  > Records every tool run, AI call, graph edit, login, and approval/rejection — with user
  > attribution. Approval entries include the `self_approved` boolean (§5.2).

  Wires the **reserved Slice-10 seam**: each decision calls `audit.service.record` with
  `action=approval_granted|approval_rejected`, `actor_user_id=<decider>`,
  `engagement_id`, `self_approved=<bool>`, `target_type="approval_request"`,
  `target_id=<request id>`, `payload={tool, server, reason, initiator_user_id, decision}`.
  No new audit table/hashing (Slice-10 integrity surface untouched).

- **§17.2 — Human in the loop where it matters; any member can act** — quoted:
  > Anything that touches a target destructively, expands scope, or alters shared truth
  > involves a human. Any engagement member can act as that human — attribution is recorded,
  > not gated.

  The gate is **attribution, not authorization-narrowing**: any member (not a privileged
  approver role) can sign off; the system records *who*. Recon/passive stays autonomous.

- **§17.3 — Delegation as a first-class pattern** — the classifier is a reusable
  decision-category boundary; Slice 18 reuses it. Noted as a seam (not built here).

- **§4 — archived engagements are read-only** — a dangerous command cannot be proposed,
  approved, or executed in an archived engagement (chat POST already 409s when archived,
  Slice 11; the approve endpoint also rejects with 409 if the engagement is archived). A
  request that was pending when the engagement archived can still be **rejected** (a no-op
  cleanup) but not **approved-and-run** (no new tool runs in an archived engagement, §4).

- **§17.1 — engagement isolation** — approval requests are engagement-scoped; read/act
  endpoints require membership (`404` for non-members / missing engagement, no disclosure);
  the Approvals-tab list returns only the caller's-engagement requests.

- **§5.4 — private chat per user** — unchanged by this slice. The initiator's chat stays
  private; the shared safety artifact is the `approval_request` row (carrying only the
  command), surfaced via the per-engagement Approvals tab, NOT a shared chat. (The future
  shared-chat rework that reverses §5.4 is its own ADR + slice — see Reserved seams.)

- **ADR-0001 — single-writer** — approvals do NOT touch the graph and never go through the
  single writer; they write only the new `approval_requests` table (+ the audit row).
  Executing an approved command goes through the existing `mcp.service.execute_tool_run`
  pipeline exactly as a manual run does.

- **§6.2 — execution semantics** — an **approved** dangerous command enters the existing
  concurrency model unchanged (heavy tools take a slot + per-(engagement, target-host) lock,
  light bypasses, FIFO when full — Slice 05). Approval is a gate *in front of* admission, not
  a replacement for it.

## Design notes (load-bearing decisions)

### How the AI proposes a command: native tool-calling + an instructed-block fallback

**Resolved decision 1: native tool-calling.** Slices 11–13 streamed free-text prose plus a
trailing, server-parsed `<adeptus-meta>` JSON block (Slice 13's `plan` + `claims`). This slice
is the first AI-initiated *action* surface, and the human has chosen **native
function/tool-calling** (NOT the previously-proposed instructed `<adeptus-meta>` `actions`
block). The model is given a single tool:

```jsonc
// The propose_command tool, presented to BOTH backends (mapped to each wire format).
{
  "name": "propose_command",
  "description": "Propose a single pentest tool command to run against the engagement target. \
The platform classifies it; dangerous commands require human approval before execution.",
  "input_schema": {
    "type": "object",
    "required": ["server", "tool", "args"],
    "properties": {
      "server":  { "type": "string", "description": "MCP server name (must exist in config)." },
      "tool":    { "type": "string", "description": "Tool name on that server." },
      "args":    { "type": "object", "additionalProperties": true,
                   "description": "Tool arguments, verbatim — no redaction (§5.5)." },
      "preset":  { "type": "string", "description": "Optional named preset (stealth/normal/aggressive)." },
      "rationale": { "type": "string", "description": "Why this command, in one sentence." }
    }
  }
}
```

Each parsed `propose_command` call maps 1:1 onto the internal `ProposedAction`
(`server_name`, `tool_name`, `args`, `preset_name`, `rationale`).

**Per-backend parsing (the two LLM clients gain a structured-tool-call path):**

- **Ollama (local) — `chat/ollama_client.py`.** Ollama's `/api/chat` accepts a top-level
  `tools` array and, when the model supports it, emits `message.tool_calls` on a frame
  (`{"function": {"name": "propose_command", "arguments": {...}}}`). The client today is a
  pure `AsyncIterator[str]` of text; this slice extends it to **also surface tool-calls**
  out-of-band (a `tool_calls` holder analogous to the existing `OllamaUsage` holder, populated
  as frames arrive) so the streamer can read proposed actions after the text stream ends. Text
  tokens still stream as before (the `token` WS frame is unchanged for prose).
- **Anthropic (cloud) — `chat/anthropic_client.py`.** The Messages API accepts a top-level
  `tools` array and emits `tool_use` content blocks: `content_block_start` with
  `type: tool_use` (name + id), then `input_json_delta` deltas accumulating the JSON input,
  closed by `content_block_stop`. The client (which today yields only `text_delta` text) gains
  a parallel accumulator for `tool_use` blocks, surfaced through the same holder the streamer
  reads. `tool_choice` is left default (`auto`) so the model may answer in prose, propose a
  command, or both.
- **Shared streamer.** Both clients keep their identical `stream_chat(...)` signature; the new
  out-of-band holder (e.g. `ProposedCalls`) is the single seam the chat streamer reads. The
  branch in the streamer stays "which client is iterated"; tool-call surfacing is symmetric.

**Defined fallback for weak/no tool-calling support (part of this slice).** A small quantized
local model may not reliably emit `tool_calls`. The fallback is the **instructed-block path**:
when `ADEPTUS_TOOLCALL_MODE` is `fallback` (or `auto` detects the backend/model advertises no
tool support), the system instead appends an **action-proposal instruction** to the
structured-output system prompt and parses a tolerant `<adeptus-meta>` `actions` array from
the streamed text (the same tolerant rule as Slice 13: malformed/missing → no actions, the
turn never fails). Both paths normalize to the same `ProposedAction` list before
classification, so **the classifier, gate, audit, and frontend are identical regardless of
which mechanism produced the proposal**. This guarantees the slice functions on a backend with
weak or no native tool-calling. The mode default is `auto` (native when advertised, otherwise
the instructed-block fallback).

**Validation before classification (both paths).** Each parsed action is validated (`server` +
`tool` must exist in the live MCP config; unknown ones are dropped — §17.1, no
hallucinated-tool execution) **before** classification.

### The classifier is pure and reason-typed (§5.2 + the Slice-17/18 seam)

`approvals/classifier.py` exposes a pure function
`classify(action, *, tool_config) -> ClassificationResult` returning `tier`
(`autonomous` | `requires_approval`) and, when gated, a list of `reason`s
(`target_write` | `aggressive_scan` | `credential_attack` | `unclassified_manifest` |
reserved `out_of_scope`). Inputs are the parsed action + the resolved manifest `ToolConfig`
(`weight`, `capability_flags`).

**Resolved decision 2: autonomous unless known-dangerous (inverted default), with a strict
fail-safe escape hatch.** This INVERTS the safe-by-default bias of the original plan. The
single dangerous predicate is explicit and conservative — a command is dangerous if **any** of:

- the manifest marks `weight = heavy`; OR
- the tool carries a **dangerous capability flag** — the configured dangerous-flag set
  (`shell-exec`, `filesystem-write`, `credential-attack`, `target-write`, plus any future
  destructive flag); OR
- the `tool` (or `server/tool` pair, or resolved `preset`) is on the explicit
  **dangerous list** (the dangerous-write / aggressive-scan / credential-attack tool lists +
  the aggressive-preset set + the brute/spray arg-signal list).

Each true predicate appends its typed reason (`aggressive_scan` for heavy/aggressive,
`target_write` for shell-exec/filesystem-write/target-write, `credential_attack` for the
credential flag/list/arg signal). **Everything else is `autonomous`.**

**Fail-safe escape hatch (the load-bearing safety compensation for the inverted default).**
A tool whose manifest classification is **missing or empty** — `weight is None` AND
`capability_flags` is empty/absent — is treated as **dangerous** with reason
`unclassified_manifest`. So the "unknown classification" case still gates, even though a known
benign-but-heavy tool runs… no: a `heavy` tool is dangerous (it has a classification), and a
`light` tool with declared non-dangerous flags is autonomous. The only autonomous tools are
ones with a **present, validated, non-dangerous** classification. This is enforced two ways:

- **Live enforcement — fail-closed at config load (authoritative).** The MCP registry parser
  (`mcp/registry.py`) **requires** a present, valid `weight` (`light`/`heavy`) for every tool:
  a tool with no/invalid weight raises `ConfigError` at startup and is **never registered**, so
  it can never be proposed or run **at all** — strictly stronger than gating. This is the real
  live guarantee that an un-manifested tool cannot run ungated (proven by
  `mcp/tests/test_registry.py::TestFailClosedOnMissingWeight`).
- **Defense-in-depth — runtime guard in the classifier.** The classifier additionally gates any
  `ToolConfig` with `weight is None` as `requires_approval` + `unclassified_manifest`
  (never silently autonomous). Because layer 1 prevents a weightless tool from ever reaching the
  live `_resolve_tool_config` path, this hatch is a *belt-and-suspenders* guarantee covering the
  pure-classifier boundary and any future/alternate resolver; paired with
  `validate_tool_manifests()` (a loud load-time warning), it ensures "never silently autonomous"
  even if layer 1 is ever relaxed. (Resolution of the code-review finding that the runtime hatch
  alone is unreachable in the live path — the *system* is fail-closed; the hatch is defensive.)

`out_of_scope` is **reserved, never returned here** (Slice 17 adds the scope check that
appends it). The dangerous lists/sets/flag-set live in config (`approvals/config.py`
constants, overridable by env) so adding a dangerous tool does not require a code change. The
classifier is the single boundary Slice 18 will short-circuit for standing autonomy and Slice
17 will extend for scope.

> **Residual risk of the inverted default (documented, see Risk 2 + the threat model).** A
> tool that is genuinely dangerous but is (a) marked `light`, (b) carries no dangerous
> capability flag, and (c) is not on any dangerous list will run **ungated**. Under the old
> safe-by-default this would have gated; under the inverted default it does not. The
> compensations are: the dangerous capability-flag check, the explicit dangerous lists, the
> `unclassified_manifest` escape hatch for empty manifests, and `validate_tool_manifests()`.
> The security reviewer must specifically confirm the completeness of the dangerous-flag set +
> dangerous lists and the correctness of the escape hatch (threat-model item (a)/(j)).

### Approval requests are an engagement-shared queue (not private chat)

Although the card renders *inline in chat*, the **approval request is shared across the
engagement** — §5.2 "any engagement member clicks approve/reject" requires a second member to
see and act on the first user's pending request. So `approval_requests` is engagement-scoped
(NOT user-private like `chat_messages`). It links back to the **initiating assistant turn**
(`chat_message_id`) so the initiator's chat renders the card inline, and it is independently
listable via `GET .../approvals?status=pending` so any member's per-engagement Approvals tab
shows the queue. **Chat itself stays private per §5.4** — only the **approval request**, a
deliberately-shared safety artifact, crosses the per-user boundary, and it carries only the
command (server/tool/args/reason/rationale), not the chat prose. This is exactly how "any
engagement member approves" is satisfied **without** a shared chat (the future shared-chat
rework is a separate slice — see Reserved seams).

### Execution happens on approval, through the existing pipeline, exactly once

On **approve**, after recording the decision + audit atomically, the service hands the
command to `mcp.service.execute_tool_run(...)` (async path, attributed to the **initiator**
as the run's `user_id` — Resolved decision 3: the run is the AI's-on-behalf-of-the-initiator
action; the *approver* attribution lives on the approval audit entry). The request transitions
`pending → approved` and is marked **executed** so a double-approve or a
concurrent-approve can never run the command twice (the decision transition is guarded — see
Risk 1: a single-row state machine with a conditional UPDATE
`WHERE status='pending'`). On **reject** the request transitions `pending → rejected` and
**no run is created**.

State machine: `pending → approved` (and then the run executes) | `pending → rejected`.
Terminal states are immutable; a second decision on a terminal request returns `409`
(already decided) and does not re-run or re-audit (idempotency / double-decision guard,
Risk 1). Autonomous commands never create a request (they run immediately, Slice 04/05) —
but for a **uniform audit trail** the demo's autonomous path still produces the usual
`tool_run` audit entry (Slice 10), just no `approval_*` entry.

### Audit emission timing (atomic with the decision)

The `approval_granted` / `approval_rejected` audit entry is written **in the same DB
transaction** as the request's `pending → approved|rejected` transition (Slice-10
Decision-1 policy: atomic — no decided-but-unaudited gap, no audited-but-undecided orphan).
`self_approved` is computed at decision time (`acted_by == initiator`); the entry is
attributed to the **decider** (Resolved decision 3). The subsequent `execute_tool_run` (on
approve) emits its own `tool_run` entry via the Slice-10 mcp wiring, attributed to the
**initiator**; the approval entry and the tool-run entry are two distinct, correctly-ordered
chain links with deliberately different actors.

## Contract

OpenAPI delta. **New feature `approvals`** with a list + two decision endpoints. **Two
changed chat schemas** (`ChatMessageRead` gains an optional `approval_requests` array so a
reloaded conversation re-renders its inline cards; the WS `done` frame likewise carries any
approval requests created this turn). A contract change means `make generate-api` is required.
All endpoints require `cookieAuth`; engagement-scoped reads/acts require **membership**
(`404` for non-members / missing engagement, §17.1).

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 16 delta
  version: "0.16.0"

paths:
  /api/v1/engagements/{engagement_id}/approvals:
    get:
      operationId: list_approval_requests
      summary: >-
        List the engagement's approval requests (shared across members, §5.2),
        newest-first, optionally filtered by status. Requires membership. This is
        the data source for the per-engagement Approvals tab AND the future Slice-32
        notifications bell (Resolved decision 4).
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - name: status
          in: query
          required: false
          schema: { $ref: "#/components/schemas/ApprovalStatus" }
          description: Filter by status (e.g. pending for the Approvals tab).
        - { name: cursor, in: query, required: false, schema: { type: string } }
        - { name: limit, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 100, default: 50 } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ApprovalRequestPage" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }

  /api/v1/engagements/{engagement_id}/approvals/{request_id}/approve:
    post:
      operationId: approve_request
      summary: >-
        Approve a pending dangerous command (any engagement member, including the
        initiator — §5.2). Records attribution + self_approved (attributed to the
        DECIDER), then executes the command via the tool-run pipeline (the run is
        attributed to the INITIATOR — Resolved decision 3). Idempotent against
        double-decision (409).
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: request_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: Approved; the command was handed to the tool-run pipeline.
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ApprovalRequest" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/request not found or caller not a member }
        "409":
          description: >-
            Request already decided (terminal), OR the engagement is archived
            (no new runs, §4).
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ApprovalConflict" }

  /api/v1/engagements/{engagement_id}/approvals/{request_id}/reject:
    post:
      operationId: reject_request
      summary: >-
        Reject a pending dangerous command (any engagement member, §5.2). Records
        attribution + self_approved (attributed to the DECIDER); the command is never
        executed. Symmetric with approve.
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: request_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: Rejected; no command executed.
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ApprovalRequest" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/request not found or caller not a member }
        "409":
          description: Request already decided (terminal).
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ApprovalConflict" }

components:
  schemas:
    ApprovalStatus:
      type: string
      enum: [pending, approved, rejected]
      description: >-
        pending until a member acts; then terminal approved (command executes) or
        rejected (command never runs). No expiry — approvals do not time out (§5.2).

    ApprovalReason:
      type: string
      enum: [target_write, aggressive_scan, credential_attack, unclassified_manifest, out_of_scope]
      description: >-
        Why the command needs approval (§5.2 dangerous categories) plus
        unclassified_manifest — the fail-safe reason for a tool with a missing/empty
        manifest classification (Resolved decision 2 escape hatch). out_of_scope is
        RESERVED for Slice 17 (soft scope enforcement) and is never produced in this slice.

    ApprovalTier:
      type: string
      enum: [autonomous, requires_approval]
      description: The two-tier risk classification (§5.2). Autonomous commands never create a request.

    ApprovalRequest:
      type: object
      required:
        [id, engagement_id, chat_message_id, server_name, tool_name, args, reasons,
         status, created_at]
      properties:
        id: { type: string, format: uuid }
        engagement_id: { type: string, format: uuid }
        chat_message_id:
          type: string
          format: uuid
          description: The assistant turn that proposed this command (drives the inline card).
        initiator_user_id:
          type: string
          format: uuid
          description: >-
            The user whose chat turn proposed the command. Used to compute self_approved
            on decision and to attribute the executed run (Resolved decision 3). (Ownership
            concept of the approval request, NOT a provenance tag on a shared entity —
            §8.2 / §17.4.)
        server_name: { type: string }
        tool_name: { type: string }
        args:
          type: object
          additionalProperties: true
          description: The proposed command arguments, verbatim (no redaction, §5.5).
        preset_name:
          oneOf: [{ type: string }, { type: "null" }]
        rationale:
          oneOf: [{ type: string }, { type: "null" }]
          description: The AI's stated reason for the command (verbatim).
        reasons:
          type: array
          items: { $ref: "#/components/schemas/ApprovalReason" }
          description: Why this command was gated (§5.2). Non-empty for every request.
        status: { $ref: "#/components/schemas/ApprovalStatus" }
        acted_by_user_id:
          oneOf: [{ type: string, format: uuid }, { type: "null" }]
          description: The member who approved/rejected; null while pending (§5.2 attribution).
        acted_by_username:
          oneOf: [{ type: string }, { type: "null" }]
          description: >-
            Display name of the decider, resolved for the inline "Approved/Rejected by @user"
            label (§5.2). Null while pending.
        self_approved:
          oneOf: [{ type: boolean }, { type: "null" }]
          description: true when decider == initiator (§5.2); null while pending.
        tool_run_id:
          oneOf: [{ type: string, format: uuid }, { type: "null" }]
          description: The tool run created when approved; null while pending/rejected.
        created_at: { type: string, format: date-time }
        decided_at:
          oneOf: [{ type: string, format: date-time }, { type: "null" }]

    ApprovalConflict:
      type: object
      required: [reason]
      properties:
        reason:
          type: string
          enum: [already_decided, engagement_archived]
          description: Distinguishes the two 409 cases (double-decision vs archived).
        status:
          $ref: "#/components/schemas/ApprovalStatus"
          description: The request's current terminal status (for already_decided).

    ApprovalRequestPage:
      type: object
      required: [items, next_cursor]
      properties:
        items:
          type: array
          items: { $ref: "#/components/schemas/ApprovalRequest" }
        next_cursor:
          oneOf: [{ type: string }, { type: "null" }]

    # CHANGED (chat feature): an assistant turn now carries the approval requests it
    # created, so a reloaded conversation re-renders its inline approval cards.
    ChatMessageRead:
      type: object
      # ... existing Slice 11/13/15 fields (id, engagement_id, role, content, status,
      #     created_at, plan, claims, persona_id, persona_name) ...
      properties:
        approval_requests:
          type: array
          items: { $ref: "#/components/schemas/ApprovalRequest" }
          default: []
          description: >-
            Approval requests created by this assistant turn (§5.2). Empty for user/pending
            rows and turns that proposed no dangerous command. Each renders an inline card.
```

WebSocket frame contract (not in OpenAPI; mirrored in the frontend hook to match the backend
`WebSocketChatChunk` value object). **A new `proposed_action` frame is added** (previously the
contract was only `token`/`done`/`error`); it announces a command the AI proposed this turn as
soon as it is classified, so the inline card can appear before the stream's `done`. The `done`
frame still carries the final list for reliable reconciliation:

```typescript
// frontend/src/features/chat/hooks/useChatStream.ts — matches backend chat WS frames.
interface WebSocketChatChunk {
  type: 'token' | 'proposed_action' | 'done' | 'error'
  data?: string                       // token: incremental prose (block/tool-call already stripped)
  message?: string                    // error: stable, non-leaky reason
  plan?: PlanStep[]                   // done: parsed plan (Slice 13)
  claims?: Claim[]                    // done: parsed claims (Slice 13)
  approval_request?: ApprovalRequest  // proposed_action: a single gated request just created (Slice 16)
  autonomous_action?: AutonomousAction // proposed_action: an autonomous command running now (Slice 16)
  approval_requests?: ApprovalRequest[] // done: all gated requests this turn created (Slice 16, reconcile)
}

// Lightweight value object for the "running automatically" card (no DB row — Slice 16).
interface AutonomousAction {
  server_name: string
  tool_name: string
  args: Record<string, unknown>
  preset_name: string | null
  rationale: string | null
  tool_run_id: string                 // the run already handed to the pipeline
}
```

Frame semantics: as each `propose_command` (or fallback action) is parsed, validated, and
classified, the backend emits one `proposed_action` frame — `approval_request` set for a gated
command (a `pending` row was created; render the approval card) or `autonomous_action` set for
a command running immediately (render the "running automatically" card). The terminal `done`
frame repeats the full `approval_requests` list for idempotent reconciliation; on history
reload the cards re-render from `ChatMessageRead.approval_requests`; the live decision state
(after another member acts) refreshes via the `GET .../approvals` query (polled/invalidated —
see frontend tasks).

## Data model changes

Alembic migration written via the `write-alembic-migration` skill during implementation
(register the new `app/features/approvals/models.py` import in `backend/alembic/env.py` first
— per the Alembic-autogenerate memory; recreate the autogenerated file as the non-root user).

One new table. **No columns added to any existing table** (anti-pattern guard — the migration
touches no `graph_*` / `findings` / `chat_messages` / entity tables; the chat→approval link is
a column on `approval_requests`, not on `chat_messages`; decision attribution lives on
`approval_requests` + the audit log, §8.2 / §17.4).

- `approval_requests` — one row per AI-proposed dangerous command, engagement-shared (§5.2):
  - `id` UUID PK (`gen_random_uuid()`).
  - `engagement_id` UUID NOT NULL — FK → `engagements.id` `ON DELETE CASCADE` (a request is
    meaningless without its engagement; engagement-scoped, §17.1).
  - `chat_message_id` UUID NOT NULL — FK → `chat_messages.id` `ON DELETE CASCADE`. The
    initiating assistant turn (drives the inline card). (This is the *request's* link to its
    origin turn, not a provenance column on `chat_messages`.)
  - `initiator_user_id` UUID NOT NULL — FK → `users.id` `ON DELETE CASCADE`. The chat owner
    who proposed it; used to compute `self_approved` and attribute the executed run (Resolved
    decision 3). (An ownership concept of the request, like `chat_messages.user_id`, not a
    provenance smear.)
  - `server_name` VARCHAR(100) NOT NULL, `tool_name` VARCHAR(100) NOT NULL.
  - `args` JSONB NOT NULL — the proposed args, verbatim (no redaction, §5.5). (Use the
    `JSONB().with_variant(JSON(), "sqlite")` pattern from `mcp/models.py` so the in-memory
    SQLite unit-test engine can render the DDL.)
  - `preset_name` VARCHAR(100) NULL, `rationale` TEXT NULL.
  - `reasons` JSONB NOT NULL — the list of `ApprovalReason` values (§5.2 +
    `unclassified_manifest`). Non-empty.
  - `status` VARCHAR(16) NOT NULL DEFAULT `'pending'` — CHECK IN (`pending`, `approved`,
    `rejected`).
  - `acted_by_user_id` UUID NULL — FK → `users.id` `ON DELETE SET NULL` (a deleted decider
    must not erase the request; the *audit log* keeps the immutable hashed attribution —
    §17.4). Null while pending.
  - `self_approved` BOOLEAN NULL — `acted_by_user_id == initiator_user_id`; null while
    pending. (Mirrors the §5.2 audit column for convenient live rendering; the audit row is
    still the source of truth.)
  - `tool_run_id` UUID NULL — FK → `tool_runs.id` `ON DELETE SET NULL`; set when approved.
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`, `decided_at` TIMESTAMPTZ NULL.
  - Indexes:
    - `ix_approval_requests_engagement_status_created` on
      `(engagement_id, status, created_at DESC)` — the Approvals tab's "pending" query +
      engagement-scoped newest-first listing (load-bearing access path).
    - `ix_approval_requests_chat_message_id` on `(chat_message_id)` — render a turn's cards.

No new audit table/column: §5.2 attribution + `self_approved` reuse the Slice-10
`audit_entries.self_approved` column and the reserved `approval_granted`/`approval_rejected`
actions. The chat turn → approval link is read by joining `approval_requests` on
`chat_message_id` (no column added to `chat_messages`).

## Tasks

Tasks are tracked by git, not checkboxes — every commit subject cites its task id, e.g.
`feat(slice-16): add approvals classifier (task 3)`. Numbered continuously across the whole
slice (backend then frontend).

### Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[S]** Add `app/features/approvals/models.py` — the `ApprovalRequest` ORM model on the
   shared `Base` (columns, `CheckConstraint` on `status`, FKs + on-delete policies, the two
   indexes above; JSONB-with-SQLite-variant for `args`/`reasons`). Register the module import
   in `backend/alembic/env.py`. **No columns added to existing models.**
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_models.py`).

2. **[S]** Add `app/features/approvals/schemas.py` — `ApprovalStatus`, `ApprovalReason`
   (now incl. `unclassified_manifest`), `ApprovalTier` (StrEnums matching the contract;
   `status` mirrors the DB CHECK vocabulary, guarded by a parity test like the chat/audit
   features), `ApprovalRequestRead` (`from_attributes=True`, incl. `acted_by_username`
   populated at read time), `ApprovalRequestPage`, `ApprovalConflict`, and the internal
   `ProposedAction` / `ClassificationResult` / `AutonomousAction` value objects. Tests in
   `tests/test_schemas.py` (enum/DB parity; reasons non-empty for a gated request;
   `unclassified_manifest` present; `out_of_scope` present in the enum but documented as
   Slice-17-only).
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_schemas.py`).

3. **[M]** Add `app/features/approvals/classifier.py` + `app/features/approvals/config.py` —
   the **pure** `classify(action, *, tool_config) -> ClassificationResult` (§5.2 two-tier;
   reasons `target_write` / `aggressive_scan` / `credential_attack` /
   `unclassified_manifest`; `out_of_scope` **reserved, never returned**). **Inverted default
   (Resolved decision 2):** a command is `autonomous` unless it matches a dangerous predicate
   — `weight=heavy`, a dangerous capability flag (configured dangerous-flag set: `shell-exec`,
   `filesystem-write`, `credential-attack`, `target-write`, …), or membership on a dangerous
   list/preset/arg-signal — in which case it is `requires_approval` with the matching
   reason(s). **Fail-safe escape hatch:** a tool with `weight is None` AND empty/absent
   `capability_flags` returns `requires_approval` + `unclassified_manifest`. Also add
   `validate_tool_manifests(tools)` (used at registry load) that flags any tool missing
   weight/flags. Config holds the dangerous-flag set + dangerous-write / aggressive-scan /
   credential-attack tool lists + arg-signal list + aggressive-preset set (env-overridable).
   Heavily unit-tested in `tests/test_classifier.py`:
   `test_light_with_safe_flags_is_autonomous`, `test_shell_exec_flag_is_target_write`,
   `test_filesystem_write_flag_is_target_write`, `test_target_write_flag_is_target_write`,
   `test_heavy_tool_is_aggressive_scan`, `test_aggressive_preset_is_aggressive_scan`,
   `test_credential_flag_is_credential_attack`,
   `test_credential_tool_list_is_credential_attack`,
   `test_brute_arg_signal_is_credential_attack`,
   `test_empty_manifest_gates_as_unclassified` (the escape hatch — load-bearing),
   `test_missing_weight_only_gates_as_unclassified`,
   `test_unknown_but_light_and_safe_runs_autonomously` (the INVERTED default — load-bearing),
   `test_validate_tool_manifests_flags_unclassified`,
   `test_out_of_scope_never_returned_in_this_slice`, `test_multiple_reasons_combine`.
   **[Risky — this is the §5.2 safety boundary AND the inverted-default; reviewer focus.]**
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_classifier.py`).

4. **[M]** Add `app/features/approvals/repository.py` — `create_request(...)`,
   `get_request_for_engagement(db, *, engagement_id, request_id)`,
   `list_for_engagement(db, *, engagement_id, status, cursor, limit)` (newest-first,
   status-filtered), `list_for_chat_message(db, *, message_id)`, and the **guarded decision
   transition** `decide_request(db, *, request_id, status, acted_by_user_id, self_approved,
   tool_run_id=None) -> ApprovalRequest | None` implemented as a conditional UPDATE
   `... WHERE id=:id AND status='pending'` returning the updated row (or `None` if it was
   already terminal — the double-decision guard, Risk 1). Tests in `tests/test_repository.py`:
   `test_create_persists_pending_request`, `test_list_pending_for_engagement`,
   `test_list_by_chat_message`, `test_decide_transitions_pending_to_approved`,
   `test_decide_on_terminal_returns_none` (idempotency),
   `test_concurrent_decide_only_one_wins` (interleave two decides; assert exactly one updates
   the row). **[Risky — the no-double-decision test is load-bearing.]**
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_repository.py`).

5. **[M]** Add `app/features/approvals/service.py`:
   - `create_requests_for_turn(db, *, engagement_id, chat_message_id, initiator_user_id,
     actions) -> ClassifiedTurnResult` — for each parsed+validated action: resolve its
     `ToolConfig` (via `mcp.service`), `classify(...)`; **autonomous** actions are returned in
     a `autonomous` list for the chat service to execute immediately (NO request row);
     **gated** actions create a `pending` `approval_requests` row and are returned in a
     `gated` list. Drops actions whose server/tool is unknown (§17.1).
   - `list_requests(db, *, engagement_id, requester, status, cursor, limit)` — membership
     chokepoint (`NotFoundError`→404 for non-members/missing, §17.1).
   - `decide(db, *, engagement_id, request_id, requester, decision) -> ApprovalRequest` —
     membership chokepoint; load the request (404 if not in engagement); for **approve** also
     guard archived engagement (raise `EngagementArchivedError`→409, §4); compute
     `self_approved = (requester.id == initiator_user_id)`; call the guarded
     `decide_request(...)` (raise `AlreadyDecidedError`→409 if it returns `None`); **emit the
     `approval_granted|approval_rejected` audit entry atomically** (`audit.service.record`,
     `actor_user_id=requester.id` — the DECIDER, `self_approved=...`, §14/§5.2); on
     **approve** then hand the command to `mcp.service.execute_tool_run(...,
     user_id=initiator_user_id` — the INITIATOR, Resolved decision 3`, async_mode=True,
     preset_name=...)` and store the returned `tool_run_id`.
   - Tests in `tests/test_service.py` (mcp `execute_tool_run` + audit `record` **mocked**):
     `test_autonomous_action_returns_no_request`, `test_gated_action_creates_pending_request`,
     `test_unknown_tool_action_dropped`, `test_unclassified_manifest_action_gated`,
     `test_list_non_member_404`,
     `test_approve_audit_attributed_to_decider_self_approved_true_for_initiator`,
     `test_approve_audit_attributed_to_decider_self_approved_false_for_other_member`,
     `test_approve_executes_tool_run_attributed_to_initiator`,
     `test_reject_records_audit_and_does_not_execute`,
     `test_decide_on_terminal_409`, `test_approve_archived_engagement_409`,
     `test_args_not_redacted` (verbatim, §5.5).
     **[Risky — attribution split (decider vs initiator) + audit emission.]**
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_service.py`).

6. **[M]** Add `app/features/approvals/router.py` — `GET .../approvals`,
   `POST .../approvals/{id}/approve`, `POST .../approvals/{id}/reject` (depending on
   `get_current_user`). Membership/archived/already-decided domain exceptions translate via
   the registered handlers (`NotFoundError`→404); the 409s (`already_decided`,
   `engagement_archived`) are translated inline with the `ApprovalConflict` body (same inline
   pattern as the chat/mcp 409). Tests in `tests/test_router.py` (`AsyncClient` + session
   override; mcp/audit mocked): `test_list_200_for_member`, `test_list_404_for_non_member`,
   `test_approve_200_for_member`, `test_approve_by_initiator_self_approved`,
   `test_approve_by_other_member_cross_approval`, `test_reject_200`,
   `test_decide_409_already_decided`, `test_approve_409_archived`,
   `test_decide_404_for_non_member`, `test_unauthenticated_401`.
   - Test command: `make test-backend` (`pytest app/features/approvals/tests/test_router.py`).

7. **[S]** Wire the approvals router in `app/main.py` (`include_router`). No new error
   handler (existing `NotFoundError` handler covers 404; the 409s are inline). Call
   `approvals.classifier.validate_tool_manifests(...)` at MCP registry load (a thin hook in
   `mcp/registry.py`) so a mis-manifested tool is logged loudly at startup (Resolved
   decision 2).

8. **[L]** Extend `app/features/chat/` + the two LLM clients for native tool-calling
   (Resolved decision 1) with the instructed-block fallback:
   - **LLM clients.** Add the `propose_command` tool to the `tools` array sent by
     `ollama_client.stream_chat` and `anthropic_client.stream_chat`, and surface parsed
     tool-calls out-of-band via a new `ProposedCalls` holder (analogous to `OllamaUsage`):
     Ollama reads `message.tool_calls`; Anthropic accumulates `tool_use` content blocks
     (`content_block_start` + `input_json_delta` + `content_block_stop`). Text tokens stream
     unchanged. Mocked tests in `chat/tests/test_ollama_client.py` /
     `test_anthropic_client.py`: a faked tool-call frame populates `ProposedCalls`; a
     text-only stream leaves it empty.
   - **Fallback.** Add `ADEPTUS_TOOLCALL_MODE` (`auto` | `native` | `fallback`, default
     `auto`). In `fallback`, append the action-proposal instruction to the structured-output
     system prompt and parse a tolerant `<adeptus-meta>` `actions` array (extend
     `plan_parser.py` or add `action_parser.py`; malformed/missing → `[]`, the turn never
     fails). **Both paths normalize to the same `ProposedAction` list.** Test:
     `test_fallback_mode_parses_actions_block`, `test_native_mode_reads_tool_calls`,
     `test_auto_mode_prefers_native_then_falls_back`.
   - **Streamer wiring.** In `stream_assistant_reply` finalize: call
     `approvals.service.create_requests_for_turn(...)`; for each **autonomous** action execute
     it via `mcp.service.execute_tool_run(...)` (attributed to the turn owner = initiator) and
     emit a `proposed_action` frame with `autonomous_action`; for each **gated** action emit a
     `proposed_action` frame with the created `approval_request`; the terminal `done` frame
     repeats the full `approval_requests` list. Reads (`list_messages`, `get_turn_debug`)
     populate `ChatMessageRead.approval_requests` via
     `approvals.repository.list_for_chat_message`.
   - Widen the `_emit_ai_call` payload with `proposed_actions` / `gated_actions` counts
     (forensic record; no new audit action).
   - Tests in `chat/tests/test_service.py` (mcp/approvals/audit mocked):
     `test_turn_parses_native_tool_calls`, `test_autonomous_action_runs_immediately`,
     `test_autonomous_action_emits_proposed_action_frame`,
     `test_gated_action_emits_proposed_action_frame`,
     `test_done_frame_repeats_approval_requests`,
     `test_no_tool_call_degrades_cleanly`, `test_list_messages_includes_approval_requests`,
     `test_action_args_not_redacted` (§5.5).
     **[Risky — the AI-initiated-action seam; reviewer focus on the autonomous-immediate-
     execute path under the inverted default.]**
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_service.py`).

9. **[S]** Add Alembic migration for `approval_requests` via the `write-alembic-migration`
   skill. Confirm `make migrate` applies it cleanly against a fresh DB and
   `alembic downgrade -1` reverts it. Confirm `make verify-audit` still returns OK on the
   empty chain (no audit-schema change).
   - Test command: `make migrate` then `alembic downgrade -1` (in the backend container).

### Frontend tasks

Numbering continues from the backend tasks.

10. **[S]** Run `make generate-api` to regenerate types into `frontend/src/shared/api/`;
    commit the updated `frontend/openapi.json` snapshot (adds `ApprovalRequest`,
    `ApprovalStatus`, `ApprovalReason`, `ApprovalTier`, `ApprovalRequestPage`,
    `ApprovalConflict`; extends `ChatMessageRead`). Note the OpenAPI-default→required-TS-field
    memory: any request field with a literal default may generate as required — thread it
    through consumers in the same commit.
    - Test command: `make generate-api` then `make lint`.

11. **[M]** Add `frontend/src/features/approvals/api.ts` — `useApprovalRequests(engagementId,
    { status })` (`GET`, cursor pagination, `approvalKeys` factory), `useApproveRequest(...)`
    and `useRejectRequest(...)` mutations (`POST .../approve` / `.../reject`, invalidate the
    approvals + chat-messages queries on settle; surface the `409` conflict body so the UI can
    show "already decided by @other"). Tests in `__tests__/api.test.tsx` (mock `api.GET`/
    `api.POST`): pending filter query string; approve/reject mutations; `404`/`409` surfaced
    with the conflict reason.
    - Test command: `make test-frontend` (`vitest run src/features/approvals/api.test.tsx`).

12. **[M]** Add `frontend/src/features/approvals/components/ApprovalCard.tsx` + test — the
    inline card: shows `server`/`tool`/`args`, the human-readable **reason(s)** ("credential
    attack", "aggressive scan", "modifies target", "tool not classified in its manifest"),
    Approve + Reject buttons while `pending`; once decided, renders **"Approved by @user"** /
    **"Rejected by @user"** (from `acted_by_username`) and disables the buttons; an
    "autonomous — running automatically" variant (driven by `AutonomousAction`) for the
    no-gate path. Buttons call the Slice-16 mutations. Tests: pending shows both buttons;
    reason labels render (incl. `unclassified_manifest`); approve→ shows "Approved by @user";
    reject→ "Rejected by @user"; decided card disables buttons; 409 surfaces "already
    decided"; autonomous variant shows "running automatically" with no buttons.
    - Test command: `make test-frontend` (`vitest run src/features/approvals/components/ApprovalCard.test.tsx`).

13. **[M]** Wire the inline card into the chat thread — render each assistant message's
    `approval_requests` (from `ChatMessageRead.approval_requests` on reload and from the
    stream `proposed_action`/`done` frames live) as `<ApprovalCard>` beneath the reply in
    `ChatMessageList.tsx` / `ChatPanel.tsx`; render the `autonomous_action` "running
    automatically" variant for the no-gate path. Consume the new `proposed_action` WS frame in
    `useChatStream.ts`. Refresh decision state by invalidating/refetching `useApprovalRequests`
    when another member acts (poll the pending query while a card is pending, or invalidate on
    decision). Update `ChatMessageList.test.tsx` / `ChatPanel.test.tsx`: a dangerous turn shows
    a pending card; an autonomous turn shows the "running automatically" variant; a
    `proposed_action` frame renders a card mid-stream; reload re-renders cards; a card reflects
    another member's decision after refetch.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatPanel.test.tsx`).

14. **[M]** Add the **per-engagement Approvals tab** (Resolved decision 4) so a second member
    can act without reading the initiator's private chat — `frontend/src/features/approvals/
    components/ApprovalQueue.tsx` + test, listing the engagement's `pending` requests with
    Approve/Reject per row, mounted as an **"Approvals" tab/panel in the workspace, visible to
    all members** of the open engagement. (This list endpoint is also the data source the
    Slice-32 notifications bell will later consume.) Test: lists pending requests;
    approve/reject from the queue; empty state when none pending; updates after a decision.
    - Test command: `make test-frontend` (`vitest run src/features/approvals/components/ApprovalQueue.test.tsx`).

15. **[S]** Verify coverage ≥ 60% on `src/features/approvals/`; `make lint` clean (no `any`;
    narrow via generated types). Confirm the Slice-02 banner, Slice-08 graph store, Slice-12/13
    debug/plan panels, and Slice-15 persona chip are untouched except for the additive card
    render + the new Approvals tab.
    - Test command: `make test-frontend` then `make lint`.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/approvals/` and the new chat
  call-site):
  - `tests/test_classifier.py` — the `test_*` names in backend task 3; this is the §5.2 +
    inverted-default safety boundary and gets the densest coverage (each dangerous category,
    the dangerous-flag set, the `unclassified_manifest` escape hatch, the
    `test_unknown_but_light_and_safe_runs_autonomously` inverted default, the
    `validate_tool_manifests` warning, the reserved `out_of_scope`, multi-reason combination).
  - `tests/test_repository.py` — create/list/list-by-message; the guarded decision
    transition; `test_decide_on_terminal_returns_none` and
    `test_concurrent_decide_only_one_wins` (the no-double-decision guard).
  - `tests/test_service.py` — the `test_*` names in backend task 5, incl. the decider-attributed
    audit with self_approved-true (initiator) / false (other member), the initiator-attributed
    run, the `unclassified_manifest` gating, approve-executes / reject-does-not, archived 409,
    already-decided 409, and the no-redaction assertion (§5.5).
  - `tests/test_router.py` — the ten HTTP `test_*` names in backend task 6.
  - `chat/tests/test_ollama_client.py` / `test_anthropic_client.py` — the native tool-call
    surfacing tests (faked tool-call frame populates `ProposedCalls`; text-only leaves it empty).
  - `chat/tests/test_service.py` — the action-routing `test_*` names in backend task 8
    (native parse + fallback parse, autonomous-immediate-execute vs gated-creates-request,
    `proposed_action`/`done`-frame delivery, graceful degradation, read inclusion, no-redaction).
- **Unit — frontend** (coverage ≥ 60% on `src/features/approvals/`):
  - `api.test.tsx` — pending filter; approve/reject; 404/409 surfacing.
  - `ApprovalCard.test.tsx` — pending buttons; reason labels (incl. `unclassified_manifest`);
    "Approved/Rejected by @user"; disabled-after-decision; autonomous variant; 409 already-decided.
  - `ApprovalQueue.test.tsx` — pending list; act from queue; empty + post-decision states.
  - `ChatPanel.test.tsx` / `ChatMessageList.test.tsx` — inline card render (gated vs
    autonomous); `proposed_action` mid-stream render; reload re-render; reflects another
    member's decision after refetch.
- **Integration** (`@pytest.mark.integration`, real Postgres; **Ollama + MCP subprocess
  mocked** — external services never hit, CLAUDE.md):
  - `test_dangerous_command_gated_then_approved_executes` — POST a chat message; stream a
    **faked Ollama reply carrying a `propose_command` tool-call** for a credential-attack tool;
    assert a `pending` `approval_requests` row is created and **no** tool run yet; approve as
    the initiator; assert one `approval_granted` audit entry **attributed to the decider** with
    `self_approved=true`, the request `approved`, and exactly one `tool_run` created via the
    pipeline **attributed to the initiator**. **Headline §5.2 + §14 + Resolved-decision-3
    happy-path.**
  - `test_fallback_block_path_gates_then_approves` — same as above but with
    `ADEPTUS_TOOLCALL_MODE=fallback` and a faked reply carrying an `<adeptus-meta>` `actions`
    block; assert the gate/approve/audit behavior is identical (Resolved-decision-1 fallback).
  - `test_cross_member_approval_audit_attributed_to_approver_self_approved_false` — member A's
    turn proposes a dangerous command; member B approves; assert the `approval_granted` audit
    entry is attributed to **B** with `self_approved=false` and the card shows "Approved by @B"
    (§5.2 cross-member + Resolved decision 3).
  - `test_rejection_does_not_execute` — propose a dangerous command; reject; assert an
    `approval_rejected` audit entry (attributed to the decider), request `rejected`, and
    **zero** tool runs created.
  - `test_autonomous_command_runs_without_request` — faked reply proposes a `light` tool with
    safe declared flags; assert NO `approval_requests` row and the tool run executes (a
    `tool_run` audit entry, no `approval_*` entry) (§5.2 autonomous / inverted default).
  - `test_unclassified_manifest_command_is_gated` — faked reply proposes a tool whose manifest
    has no weight and no flags; assert a `pending` request with reason
    `unclassified_manifest` and **no** tool run (Resolved decision 2 escape hatch).
  - `test_double_approve_runs_only_once` — fire two concurrent approves on the same pending
    request; assert exactly one wins, one `approval_granted` entry, one tool run; the loser
    gets `409` (Risk 1).
  - `test_audit_chain_intact_after_approvals` — after a mix of approve/reject/autonomous
    actions, `verify.run()` returns OK and the chain weaves the `approval_*` + `tool_run`
    entries correctly (§14 — Slice-10 guarantee preserved).
  - `test_non_member_cannot_act` — a non-member POSTing approve/reject gets `404` (§17.1).
- **E2E** (Playwright, opt-in stack; Ollama stubbed with a deterministic reply carrying a
  `propose_command` tool-call) — `approvals.spec.ts`: log in as member A, send a message that
  yields a dangerous proposal, see the inline approval card pending; log in as member B (second
  context), approve from the **Approvals tab**, see "Approved by @B"; member A's card reflects
  the decision; the bottom Console shows the executed run. A separate path rejects a proposal
  and asserts no run appears. (Ollama/MCP stubbed — no real model/subprocess in CI;
  pentest/external-service rule.)

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage
  gates hold (≥80% backend `approvals` feature + the new chat call-site, ≥60% frontend
  `approvals` feature).
- `make lint` passes with no new errors.
- `make migrate` applies the new `approval_requests` migration cleanly against a fresh
  Postgres container; `alembic downgrade -1` reverts it.
- `make generate-api` produces an updated `frontend/openapi.json` containing the approval
  schemas and the extended `ChatMessageRead`; regenerated types committed.
- `make verify-audit` exits `0` on a chain that includes `approval_granted` /
  `approval_rejected` entries (Slice-10 tamper-evidence preserved, §14).
- `make dev` brings up the stack; manual demo:
  1. Ask for a passive recon command → inline card marked **"running automatically"**, the
     run executes, result in the Console (§5.2 autonomous / inverted default; no gate).
  2. Ask for a dangerous command (credential attack / aggressive scan / target write) → an
     inline **approval card** appears with the reason; the command does **not** run yet
     (§5.2 approval-gated).
  3. **Approve** → the card flips to **"Approved by @you"**, the command executes, result in
     the Console; the `approval_granted` audit entry has `self_approved=true` and is attributed
     to you (the decider); the `tool_run` audit entry is attributed to the initiator.
  4. As a **second member**, the same pending card is visible in the per-engagement
     **Approvals** tab; approve member A's request → "Approved by @second-member"; the
     `approval_granted` audit entry has `self_approved=false`, attributed to the second member
     (§5.2 any member / cross-member + Resolved decision 3).
  5. **Reject** a dangerous proposal → "Rejected by @you"; the command is never executed
     (§5.2 rejections).
  6. As an admin, open the **Audit tab** → each decision shows one `approval_granted` /
     `approval_rejected` entry attributed to the **decider** with `self_approved` populated,
     and the executed `tool_run` entry attributed to the **initiator**; toggle the Slice-10
     `self_approved` filter to separate cross-member from self-approvals (§14 / §5.2).
  7. `make verify-audit` → OK with the approval entries woven into the chain.
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — Double-execution on concurrent / repeated approve (load-bearing).** Two members
  (or a double-click) approving the same pending request could run the dangerous command
  twice. Mitigation: a single-row state machine; the decision is a conditional UPDATE
  `WHERE status='pending'` that atomically claims the request (returns the row only to the
  winner); the loser sees `None` → `409 already_decided` and never executes. Guarded by
  `test_concurrent_decide_only_one_wins` (unit) and `test_double_approve_runs_only_once`
  (integration). **Reviewer must confirm the transition is atomic and the run is created only
  inside the winning branch.**
- **Risk 2 — Misclassification under the INVERTED default (§5.2 safety, elevated by Resolved
  decision 2).** Because the default is now **autonomous-unless-known-dangerous**, a dangerous
  command that is mis-manifested (`light`, no dangerous flag) and absent from every dangerous
  list runs with **no** human gate — the worst failure for this slice, and a strictly larger
  exposure than the old safe-by-default. Mitigations: (i) the dangerous predicate covers
  `weight=heavy`, the configured dangerous capability-flag set, and the explicit dangerous
  lists/presets/arg-signals; (ii) the **`unclassified_manifest` escape hatch** gates any tool
  whose manifest carries no weight and no flags, so the genuinely-unknown case still gates;
  (iii) `validate_tool_manifests()` loudly flags a mis-manifested tool at startup so the admin
  notices; (iv) the classifier is pure and densely tested per category and for the inverted
  default + escape hatch. **Residual risk remains** (a deliberately/accidentally mis-manifested
  dangerous tool not on any list). **Reviewer focus — confirm the dangerous-flag set + lists
  are complete and the escape hatch is correct (threat-model (a)/(j)).**
- **Risk 3 — Authorization narrowing instead of attribution (§17.2).** §5.2/§17.2 require
  that **any member** can approve (attribution, not a privileged approver role). A bug adding
  an admin-only or initiator-only check would violate the spec. Mitigation: the decision
  endpoints gate on **membership only**; `test_approve_by_other_member_cross_approval` and the
  cross-member integration test guard it. Self-approval is explicitly allowed and labeled.
- **Risk 4 — Audit gap or mis-attribution on a decision (incl. the decider/initiator split).**
  A decided-but-unaudited request, or an `approval_*` entry attributed to the initiator instead
  of the decider (or a `tool_run` entry attributed to the decider instead of the initiator),
  would corrupt the §14/§5.2 record and Resolved decision 3. Mitigation: the `approval_*` audit
  entry is written **atomically** with the decision transition (Slice-10 Decision-1 policy),
  attributed to the **decider**, with `self_approved=(decider==initiator)`; the executed
  `tool_run` is attributed to the **initiator**;
  `test_approve_audit_attributed_to_decider_*`,
  `test_approve_executes_tool_run_attributed_to_initiator`, and
  `test_reject_records_audit_and_does_not_execute` guard it; `make verify-audit` /
  `test_audit_chain_intact_after_approvals` confirm the chain stays intact.
- **Risk 5 — The AI proposes a hallucinated / out-of-config tool.** A parsed tool-call (native
  or fallback) could name a server/tool that does not exist. Mitigation: every proposed action
  is validated against the live MCP config before classification; unknown server/tool actions
  are dropped (no request, no execution) — `test_unknown_tool_action_dropped` (§17.1).
- **Risk 6 — Approval request leaks chat content across the per-user boundary (§5.4).** The
  request is deliberately engagement-shared, but it must carry **only the command** (server/
  tool/args/reason/rationale), not the initiator's private chat prose. Mitigation: the request
  schema carries no chat content beyond the command + the AI's short rationale; the Approvals
  tab never exposes the chat thread; per-user chat reads (Slice 11) are unchanged. (§5.4 is
  preserved; the future shared-chat rework is a separate slice — see Reserved seams.)
- **Risk 7 — Redaction temptation on `args` (§5.5).** Args may contain secret-looking values
  (a password list, a credential). Forbidden to strip (§5.5 / CLAUDE.md). Mitigation: args are
  stored and shown verbatim; `test_args_not_redacted` / `test_action_args_not_redacted` assert
  it. (The cloud egress-friction layer — Slice 14 — is a separate, already-shipped concern; it
  does not redact either.)
- **Risk 8 — Executing an approved command in an archived engagement (§4).** Approving a
  stale request after the engagement archived would create a new run in a read-only
  engagement. Mitigation: the approve path guards `archived → 409 engagement_archived`; reject
  remains allowed (cleanup); `test_approve_archived_engagement_409` guards it.
- **Risk 9 — The local model never emits a tool-call (native-tool-calling reliability).** A
  small quantized model may not reliably produce native `propose_command` tool-calls (Resolved
  decision 1). Mitigation: the **instructed-block fallback** (`ADEPTUS_TOOLCALL_MODE`) and the
  tolerant parser keep the feature functional on weak/no-tool-support backends — both paths
  normalize to the same `ProposedAction`; a turn with neither a tool-call nor an actions block
  is plain chat and never fails. Guarded by `test_auto_mode_prefers_native_then_falls_back`
  and `test_no_tool_call_degrades_cleanly`.
- **Risk 10 — Native tool-calling changes the two LLM client wire contracts.** Adding the
  `tools` array and surfacing `tool_calls` / `tool_use` blocks touches the single egress points
  (`ollama_client.py`, `anthropic_client.py`). A regression here could break plain chat.
  Mitigation: text-token streaming is unchanged (the new tool-call holder is out-of-band, like
  `OllamaUsage`); both clients keep their identical `stream_chat` signature; mocked client
  tests assert text-only streams still work and tool-call frames populate the holder without
  perturbing prose.

## Open questions for the human

None. The four prior open questions have been resolved by the human and folded into the design,
tasks, and threat model — see "Resolved decisions" below.

## Resolved decisions

1. **Proposal mechanism → native tool-calling** (NOT the previously-proposed instructed
   `<adeptus-meta>` `actions` block). *Rationale:* native function/tool-calling is the
   first-class action surface; a defined instructed-block **fallback** (`ADEPTUS_TOOLCALL_MODE`)
   keeps the slice functional on a backend/model with weak or no tool-calling support, with
   both paths normalizing to the same `ProposedAction`. *Consequences threaded:* the
   `propose_command` tool schema (Design notes); per-backend parsing for Ollama
   (`message.tool_calls`) vs Anthropic (`tool_use` blocks) + the fallback (Design notes, task
   8); the new `proposed_action` WS frame (Contract); client-contract Risk 10; tests across
   tasks 8 and the client test files.

2. **Unknown-tool default → autonomous unless known-dangerous** (INVERTS the original
   "fail toward the gate" bias). *Rationale:* on this step-gated, security-sensitive slice the
   human chose to run tools automatically unless explicitly classified dangerous, compensated
   by an explicit conservative dangerous rule + a strict fail-safe escape hatch. *Definition:*
   dangerous if `weight=heavy` OR a dangerous capability flag (`shell-exec`,
   `filesystem-write`, `credential-attack`, `target-write`, …) OR on the explicit dangerous
   list/preset/arg-signal; everything else autonomous. *Escape hatch:* a tool with a
   missing/empty manifest classification (no weight AND no capability flags) is treated as
   dangerous (`unclassified_manifest`); `validate_tool_manifests()` requires/validates the
   manifest classification for every tool at registry load. *Consequences threaded:* the
   classifier rewrite + the new `unclassified_manifest` reason (Design notes, schema/contract,
   task 3); the registry-load validation hook (task 7); the explicitly-documented **residual
   risk** (Design-notes callout, Risk 2, threat-model (a)/(j)); a security-reviewer
   confirmation item targeting the inverted default + the completeness of the dangerous-list +
   manifest validation (Security review section).

3. **Run attribution → initiator for the executed `tool_run` (and its Slice-10 `tool_run`
   audit entry); decider/approver for the `approval_granted`/`approval_rejected` entry, with
   `self_approved=(decider==initiator)`.** *Rationale:* keeps "who asked" and "who signed off"
   both recorded and distinct (matches the original proposal). *Consequences threaded:* service
   `decide(...)` attributes the audit entry to the requester and the run to the initiator (task
   5); endpoint summaries (Contract); Risk 4; acceptance-criteria demo steps 3/4/6; integration
   tests `..._audit_attributed_to_decider_*` + `..._tool_run_attributed_to_initiator`.

4. **Approvals surface → per-engagement Approvals tab/panel (visible to all members) PLUS the
   inline chat card.** *Rationale:* the shared, engagement-scoped `approval_request` row + the
   Approvals tab satisfy "any engagement member approves" without touching the §5.4 private
   chat model; the inline card still renders in the initiator's private chat. The Slice-32
   notifications bell will later consume the same list endpoint. *Consequences threaded:* the
   `list_approval_requests` endpoint is the tab's (and Slice-32's) data source (Contract);
   frontend task 14 mounts the Approvals tab; Out-of-scope notes Slice-32 reuse; E2E uses the
   Approvals tab for the second-member path.

## Security review required?

**Yes — this is a step-gated, risky slice (approval flow + audit), per CLAUDE.md and
PROJECT_PLAN's risky-slice summary (slice 16).** The security-reviewer subagent is required at
finish-slice time. The reviewer must confirm:

- (a) **the INVERTED default does not let a dangerous command run ungated (Resolved decision 2
  — elevated focus).** The classifier maps every §5.2 dangerous category (`target_write`,
  `aggressive_scan`, `credential_attack`) to `requires_approval`; the dangerous capability-flag
  set + dangerous lists/presets/arg-signals are **complete enough** for the shipped tools; the
  `unclassified_manifest` escape hatch gates any empty-manifest tool; `validate_tool_manifests()`
  flags mis-manifested tools at load; and execution for a gated action happens **only** inside
  the approve branch (Risk 2);
- (b) **no double-execution** — the decision is an atomic conditional UPDATE
  (`WHERE status='pending'`); the run is created only by the winning approve; `test_concurrent
  _decide_only_one_wins` + `test_double_approve_runs_only_once` pass (Risk 1);
- (c) **attribution, not authorization-narrowing** — any **member** can approve/reject
  (membership-only gate, not admin/initiator-only); self-approval is allowed and labeled;
  cross-member approval works (Risk 3, §5.2/§17.2);
- (d) **audit correctness + chain integrity + the decider/initiator split (Resolved decision
  3)** — each decision emits exactly one `approval_granted`/`approval_rejected` entry
  **atomically** with the transition, attributed to the **decider**, with
  `self_approved=(decider==initiator)`; the executed `tool_run` entry is attributed to the
  **initiator**; no audit hashing/table change; `make verify-audit` /
  `test_audit_chain_intact_after_approvals` pass (Risk 4, §14/§5.2);
- (e) **engagement isolation + per-user chat privacy (§5.4)** — approval read/act require
  membership (`404` for non-members, §17.1); the shared request carries only the command, not
  the initiator's private chat prose (Risk 6); the future shared-chat rework is NOT built here;
- (f) **no hallucinated-tool execution** — proposed actions (native tool-call OR fallback
  block) are validated against the live MCP config; unknown server/tool dropped (Risk 5, §17.1);
- (g) **no redaction of `args`** — verbatim storage/display (Risk 7, §5.5);
- (h) **archived-engagement read-only** — approve-and-run is blocked in an archived engagement
  (`409`); reject is allowed as cleanup (Risk 8, §4);
- (i) **the native tool-calling client changes don't regress chat or leak** — the `tools` array
  + tool-call surfacing are out-of-band; plain-chat token streaming is unchanged; no
  request/key content leaks in client error paths (Risk 10, §3/§5.5);
- (j) **the reserved seams are documented, not built** — `out_of_scope` (Slice 17),
  standing-autonomy delegation (Slice 18), and the future shared/collaborative-chat §5.4
  rework are reserved with no scope-matching, auto-approve, or shared-chat logic in this slice.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
</content>
</invoke>
- 2026-06-05T20:45:35Z — 5d185d5 Slice 15: Personas (CRUD + seeded) (#43)
- 2026-06-05T20:46:29Z — 737f85e docs(slice-16): add approval-flow spec, mark in-progress (#44)
