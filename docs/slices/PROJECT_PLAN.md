# Adeptus — Project Plan

Source of truth for vertical slice ordering. Mirrored to GitHub Issues at finish-slice time.

**Status values**: `todo` | `planned` | `in-progress` | `done` | `blocked`

**Phase legend**:
- A: Foundation
- B: Core mechanics (graph, tools, audit)
- C: AI integration
- D: Findings & attack paths
- E: RAG & retest
- F: Collaboration & polish
- G: Reporting & ops

---

## Phase A — Foundation

### Slice 00: Walking skeleton
- **Goal**: Login → empty 3-pane workspace → backend healthcheck round-trip
- **Requirements**: §2, §3 (auth minimal), §11.1, §11.2
- **Depends on**: —
- **Risky**: yes (auth)
- **Status**: done

### Slice 01: Engagement CRUD + membership
- **Goal**: Create engagement, invite a user, list mine
- **Requirements**: §4 (creation, lifecycle bare), §3 (membership)
- **Depends on**: 00
- **Risky**: no
- **Status**: done

### Slice 02: Privacy mode + persistent banner
- **Goal**: Engagement carries privacy mode; banner always visible in workspace
- **Requirements**: §5.1, §5.5, §17.5
- **Depends on**: 01
- **Risky**: no
- **Status**: done

### Slice 39: TLS + self-signed by default
- **Goal**: App reachable via HTTPS with bundled self-signed cert; doc swap procedure
- **Requirements**: §3 (TLS)
- **Depends on**: 00
- **Risky**: no
- **Status**: todo

### Slice 40: Single-user dev mode (no auth)
- **Goal**: Compose flag drops auth for local dev only
- **Requirements**: §2 (dev mode)
- **Depends on**: 00
- **Risky**: yes (auth bypass — must be impossible to enable in production builds)
- **Status**: todo

---

## Phase B — Core mechanics

### Slice 03: Static MCP config + shell-exec server
- **Goal**: Admin sees declared capabilities; can invoke a shell command via the system
- **Requirements**: §6.1, §6.2 (light path), §7
- **Depends on**: 00
- **Risky**: yes (MCP, shell-exec capability)
- **Status**: in-progress

### Slice 04: Tool runner panel (light tools only)
- **Goal**: Run httpx against sandbox; output appears in bottom pane
- **Requirements**: §6.2, §6.3, §11.2 (bottom pane), §11.4 (partial)
- **Depends on**: 03
- **Risky**: no
- **Status**: todo

### Slice 05: Concurrency model + per-target lock
- **Goal**: Two heavy tools against same host serialize correctly; FIFO queue when full
- **Requirements**: §6.2 (fully)
- **Depends on**: 04
- **Risky**: no
- **Status**: todo

### Slice 06: Kill switches + timeout-confirm
- **Goal**: Per-tool stop button works; engagement-wide pause halts all in-flight; timeout prompts kill/extend/wait
- **Requirements**: §6.3
- **Depends on**: 05
- **Risky**: no
- **Status**: todo

### Slice 07: Graph data model + single-writer process
- **Goal**: Manual node create/edit; per-engagement writer process owns in-memory NetworkX; soft-delete + per-entity undo
- **Requirements**: §8.1, §8.2 (writer + soft-delete + per-entity undo)
- **Depends on**: 01
- **Risky**: yes (single-writer is critical invariant)
- **Status**: todo

### Slice 08: Graph visualization (Cytoscape)
- **Goal**: Right pane shows force-directed graph; pinning works; pinned nodes act as implicit @-mentions
- **Requirements**: §8.3, §5.4 (pinning)
- **Depends on**: 07
- **Risky**: no
- **Status**: todo

### Slice 09: Personal undo stack
- **Goal**: Each user has 20-deep undo of their own writes; never reverts teammate work
- **Requirements**: §8.2 (personal undo)
- **Depends on**: 07
- **Risky**: no
- **Status**: todo

### Slice 10: Audit log + hash-chain
- **Goal**: Every action recorded; verify-chain CLI detects tampering; includes self_approved boolean
- **Requirements**: §14 (audit + tamper-evident), §5.2 (self_approved)
- **Depends on**: 01
- **Risky**: yes (audit log integrity)
- **Status**: todo

### Slice 26: Heavy tools — nmap + gobuster MCPs
- **Goal**: Run nmap against sandbox with stealth/normal/aggressive presets; gobuster wired in
- **Requirements**: §6.4 (nmap, gobuster), §6.2 (presets)
- **Depends on**: 06
- **Risky**: no
- **Status**: todo

### Slice 27: Background tasks + completion notifications
- **Goal**: Close browser; long tool finishes; on return, notification shown
- **Requirements**: §6.2 (background), §11.7
- **Depends on**: 26
- **Risky**: no
- **Status**: todo

### Slice 29: Embedded terminal (xterm.js)
- **Goal**: Shell into the engagement's container directly from the UI
- **Requirements**: §6.2 (raw shell)
- **Depends on**: 03
- **Risky**: yes (shell access)
- **Status**: todo

---

## Phase C — AI integration

### Slice 11: Local LLM via Ollama + private chat
- **Goal**: Send message in left pane; streamed reply; per-user conversation persisted
- **Requirements**: §5.1 (local path), §5.4 (private chat)
- **Depends on**: 02
- **Risky**: no
- **Status**: todo

### Slice 12: "Relevant subset" graph injection
- **Goal**: Debug panel shows exact subset of graph sent to LLM per turn
- **Requirements**: §5.3 (graph access rules), §14 (debug panel)
- **Depends on**: 08, 11
- **Risky**: no
- **Status**: todo

### Slice 13: Visible plan + certainty signaling
- **Goal**: AI's running plan visible to user; certainty % on claims and graph items
- **Requirements**: §5.3 (visible plan + uncertainty)
- **Depends on**: 11
- **Risky**: no
- **Status**: todo

### Slice 14: Cloud LLM + pattern-friction egress
- **Goal**: With cloud enabled, secret-looking content triggers confirmation modal before send
- **Requirements**: §5.1 (cloud + friction), §5.5
- **Depends on**: 11, 02
- **Risky**: yes (egress, secret detection)
- **Status**: todo

### Slice 15: Personas (CRUD + seeded)
- **Goal**: Switch persona mid-chat; create custom; recon/web-exploit/report-writer/general seeded
- **Requirements**: §5.3 (personas), §5.4
- **Depends on**: 11
- **Risky**: no
- **Status**: todo

### Slice 16: Two-tier autonomy + approval flow
- **Goal**: Dangerous command posts inline approval card; any engagement member approves/rejects; attribution recorded
- **Requirements**: §5.2 (fully)
- **Depends on**: 11, 10
- **Risky**: yes (approval flow, audit)
- **Status**: todo

### Slice 17: Soft scope enforcement
- **Goal**: Out-of-scope target triggers AI warning + explicit confirmation prompt
- **Requirements**: §5.2 (scope soft)
- **Depends on**: 16
- **Risky**: no
- **Status**: todo

### Slice 18: Delegation pattern (standing autonomy)
- **Goal**: "Always approve dedup" or "always approve light recon" toggles for the engagement
- **Requirements**: §5.2 (delegation), §17.3
- **Depends on**: 16
- **Risky**: no
- **Status**: todo

---

## Phase D — Findings & attack paths

### Slice 19: Findings model + lifecycle
- **Goal**: Create finding with Simple severity; verification status (unverified/verified/false-positive); remediation status (open/fixed/risk-accepted)
- **Requirements**: §9.1 (Simple), §9.2
- **Depends on**: 07
- **Risky**: no
- **Status**: todo

### Slice 20: Findings advanced classifications
- **Goal**: CVSS v3.1/v4 + OWASP Risk on advanced panel; MITRE ATT&CK tagging
- **Requirements**: §9.1 (CVSS + OWASP + ATT&CK)
- **Depends on**: 19
- **Risky**: no
- **Status**: todo

### Slice 21: Dedup proposal + merge
- **Goal**: AI flags potential duplicates; user merges; delegate full dedup autonomy via slice 18 mechanism
- **Requirements**: §9.2 (dedup)
- **Depends on**: 19, 18
- **Risky**: no
- **Status**: todo

### Slice 22: Attack paths (manual + AI proposals)
- **Goal**: Drag-link nodes to create attack paths; AI proposes new paths proactively
- **Requirements**: §9.3, §8.3
- **Depends on**: 19
- **Risky**: no
- **Status**: todo

### Slice 30: Burp project import
- **Goal**: Drop Burp project file; HTTP history and scanner findings populate graph
- **Requirements**: §6.4 (Burp import)
- **Depends on**: 19
- **Risky**: no
- **Status**: todo

---

## Phase E — RAG & retest

### Slice 23: RAG curated knowledge base
- **Goal**: pgvector store; OWASP/CVE/ATT&CK corpus embedded with nomic-embed-text; semantic retrieval
- **Requirements**: §10 (curated, pgvector, isolation)
- **Depends on**: 11
- **Risky**: yes (RAG isolation enforcement)
- **Status**: todo

### Slice 24: RAG per-engagement uploads
- **Goal**: Upload writeup/document; retrievable in that engagement only; strict WHERE engagement_id filter
- **Requirements**: §10, §11.4
- **Depends on**: 23
- **Risky**: yes (isolation)
- **Status**: todo

### Slice 25: Retest workflow
- **Goal**: Archived engagement's graph available as RAG context in a new engagement (opt-in only)
- **Requirements**: §4 (retest), §10 (retest exception)
- **Depends on**: 23
- **Risky**: yes (isolation exception)
- **Status**: todo

---

## Phase F — Collaboration & polish

### Slice 28: File uploads per engagement
- **Goal**: Upload wordlist/payload; AI can suggest using it in tools (e.g. ffuf wordlist)
- **Requirements**: §11.4
- **Depends on**: 04
- **Risky**: no
- **Status**: todo

### Slice 31: Presence + typing + @-mentions
- **Goal**: See who's online; per-user typing indicator; share message into engagement channel via @-mention
- **Requirements**: §11.3, §5.4 (mentions)
- **Depends on**: 11
- **Risky**: no
- **Status**: todo

### Slice 32: Notifications panel
- **Goal**: Bell icon; in-app notifications for approval requests, long-tool completion, mentions
- **Requirements**: §11.7
- **Depends on**: 16, 27, 31
- **Risky**: no
- **Status**: todo

### Slice 33: Session replay (timeline scrubber)
- **Goal**: Timeline scrubber UI browses engagement event-by-event after the fact
- **Requirements**: §11.5, §14 (audit feeds it)
- **Depends on**: 10
- **Risky**: no
- **Status**: todo

---

## Phase G — Reporting & ops

### Slice 34: Report generation (Markdown)
- **Goal**: "Generate report" produces complete 6-section Markdown (exec summary, methodology, findings, evidence, remediation, appendix) + attack-paths section
- **Requirements**: §12
- **Depends on**: 19, 22, 33
- **Risky**: no
- **Status**: todo

### Slice 35: Admin dashboard
- **Goal**: Active sessions, tool runs, queue depth, errors visible to admins
- **Requirements**: §14 (admin dashboard)
- **Depends on**: 27
- **Risky**: no
- **Status**: todo

### Slice 36: Token + cost tracking
- **Goal**: Token usage displayed per engagement and per user; no enforcement
- **Requirements**: §14 (cost), §5.1 (display)
- **Depends on**: 14
- **Risky**: no
- **Status**: todo

### Slice 37: Backups — snapshots + per-engagement export
- **Goal**: Automatic periodic snapshots (DB + uploads) to configurable path; manual per-engagement export
- **Requirements**: §13 (backup, export)
- **Depends on**: 10, 19
- **Risky**: no
- **Status**: todo

### Slice 38: Crash recovery semantics
- **Goal**: In-flight commands marked failed on restart; state (graph, chat, findings) survives via normal DB persistence
- **Requirements**: §13
- **Depends on**: 27
- **Risky**: no
- **Status**: todo

---

## Risky slice summary

These slices REQUIRE security-reviewer at finish-slice time:

00, 03, 07, 10, 14, 16, 23, 24, 25, 29, 40

---

## Ambiguities flagged by plan-project

Things the requirements doc leaves open. Resolve via ADR before the relevant slice starts:

1. **Admin bootstrap mechanism** — env-var vs. interactive first-boot. (Default decision: env-var seeded; ADR-0002.)
2. **Session storage** — server-side table vs. signed cookie. (Default decision: server-side; ADR-0003.)
3. **Default Ollama model pin** — specific model + quantization. (Default: `qwen3.5:9b
`; ADR-0004.)
4. **License** — Apache-2.0 chosen; ADR-0005 documents.
5. **Frontend test depth** — RTL + Playwright split. (Default: RTL for components, Playwright for critical journeys only; ADR-0006.)
