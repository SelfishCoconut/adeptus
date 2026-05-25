# Adeptus — Consolidated Requirements & Architecture Document

**Version:** 1.0
**Date:** 2026-05-12
**Status:** Requirements locked, build-ready.

---

# 1. Vision & Scope

Adeptus is a locally-deployable, AI-assisted penetration testing platform for a small team (2–5 people). It pairs each pentester with a private AI conversation while maintaining a shared knowledge graph of findings across the engagement.

The system emphasizes engagement isolation, human-in-the-loop safety for dangerous actions, and extensibility via MCP servers.

**Primary use case:** Web application engagements.

**Designed for future expansion:** Internal network mapping and lateral movement (informs architecture today).

---

# 2. Users & Deployment

| Aspect          | Decision                                                              |
| --------------- | --------------------------------------------------------------------- |
| Team size       | 2–5 people                                                            |
| Deployment      | Local / LAN server, single Docker Compose stack                       |
| OS target       | Linux (Kali, Parrot, Ubuntu)                                          |
| Modes           | LAN multi-user (default) + optional single-user dev mode without auth |
| Update strategy | Out of scope for v1                                                   |

---

# 3. Authentication, Roles & Access Control

* Username + password authentication.
* Long-lived sessions (stay logged in for days).
* **Two roles:** Admin and User.
* Admins create users (no self-signup).
* Admins configure cloud LLM API keys for the instance.
* **Engagement membership:** explicit invite per user per engagement. Users only see engagements they are members of.
* **Legal gate:** one-time terms-of-use accepted on first login.
* **TLS:** HTTPS with a self-signed cert by default, easy to swap for a real one.

---

# 4. Engagement Lifecycle

* **States:** `Active` → `Archived`.
* **Closing (archiving)** makes the engagement read-only: graph, chats, and report remain browsable; no new tools or AI actions can run.
* **No deadlines.** Engagements run as long as they run.
* **Creation flow:** wizard — name, scope (IPs/domains), client info, privacy mode, AI persona → create.
* **Retest workflow:** an archived engagement's graph can be loaded as background context (RAG-style) into a new engagement. The new engagement starts with a fresh visible graph; prior knowledge informs AI reasoning without contaminating the new map.
* **Deletion / retention:** manual delete by admin only; no auto-deletion.

---

# 5. AI Backend & Autonomy

## 5.1 LLM Strategy

* **Local-first:** Ollama with a small quantized model as the default.
* **Cloud fallback:** Claude API allowed when the engagement's privacy mode permits it.
* **Per-engagement privacy toggle:** strict local-only mode disables cloud calls entirely.
* **Default privacy mode for new engagements:** strict local-only. Cloud must be explicitly enabled by an admin at engagement creation or in engagement settings.
* **Pattern-friction layer for cloud egress:** when an engagement has cloud enabled, outgoing messages are scanned with a lightweight heuristic regex pass for likely-secret patterns (API keys, JWTs, `password=`, `BEGIN PRIVATE KEY`, etc.). If a match fires, the UI presents a confirmation modal ("this message looks like it may contain a secret — send anyway?") before the message leaves the local network. This is friction, not redaction — the message is sent unmodified if confirmed.
* **If local LLM is unreachable:** prompt the user to choose an alternative (manual switch, no automatic fallback).
* **If all backends are unreachable:** show "AI is offline" banner; manual tool use and UI navigation remain available.
* **Slow local model:** acceptable — does not trigger any fallback behavior.
* **Cloud cost:** token usage displayed in the UI, no enforcement / hard caps.

## 5.2 Autonomy Model

* **Two-tier risk model:**

  * **Autonomous:** recon and passive operations.
  * **Approval-gated:** dangerous commands.

* **Dangerous commands (require approval):**

  * Writes/modifications to the target (exploits, uploads, persistence).
  * Active scans likely to trigger IDS/IPS or cause DoS (aggressive nmap, heavy fuzzing).
  * Credential attacks (brute force, password spraying).
  * Anything against a target outside the explicit scope list.

* **Approval flow:** inline in chat — AI posts the command, any engagement member clicks approve/reject.

* **Who approves:** any member of the engagement (including the initiator). Approvals do not time out (sit in the queue until acted on).

* **Attribution:** every approval and rejection records the acting user. The approval event in chat shows "Approved by @user" inline so the initiator is never surprised about who signed off. The audit log includes a `self_approved` boolean (true when initiator == approver) so reviewers can filter for cross-member approvals vs self-approvals.

* **Rejections:** symmetric with approvals — any engagement member can reject, attribution recorded the same way.

* **Delegation pattern (generalized):** the user can grant the AI standing autonomy on a category of decisions for the rest of the engagement (e.g. dedup, certain command classes). This is a reusable mechanism, not a one-off.

* **Scope enforcement:** **soft** — AI warns and asks for explicit confirmation before touching out-of-scope targets.

## 5.3 AI Behavior

* **Proactive clarification:** when ambiguous, the AI asks clarifying questions rather than guessing silently.

* **Visible plan:** the AI maintains an explicit running plan / todo list visible to the user, updated as it progresses.

* **Uncertainty signaling:** AI proactively flags low-confidence claims with a stated certainty percentage (in chat and on graph items).

* **Graph access — "relevant subset" definition:** on every turn, the AI is given an explicitly-scoped subset of the graph rather than the whole graph. The subset is the union of:

  * all pinned nodes for the engagement (always included);
  * the last N nodes touched in the current conversation;
  * nodes referenced by @-mention in the last K messages;
  * nodes matching keywords in the current user message (cheap full-text match).

  A hard token budget applies; overflow content is summarized rather than dropped.

  The exact subset that went into the prompt is visible in the AI debug panel (§14) for transparency and debugging.

* **Personas (specialized agents):** named personas with distinct system prompts.

* **Seeded out-of-the-box:**

  * `recon`
  * `web-exploit`
  * `report-writer`
  * `general`

* Users can create, edit, and delete their own personas.

## 5.4 Personas, Sessions & Mentions

* **Private chat per user**, scoped to the engagement.
* **Optional sharing:** users can @-mention or share specific messages into a shared engagement channel.
* **Reset and branching:** users can reset their conversation and fork/branch a chat to explore alternative approaches.
* **Context strategy (hybrid):** recent messages verbatim + AI-generated summaries of older context + graph queried on demand (per the "relevant subset" rules in §5.3).
* **Pinned graph nodes** behave as implicit @-mentions: pinning a node tells the AI to weight it heavily in subsequent reasoning.

## 5.5 Sensitive Data Handling

* **No redaction** before sending to the LLM — the AI needs full context to be useful.
* Privacy is enforced at the engagement level via the local-only toggle (strict local-only by default; see §5.1).
* **Persistent visual indicator:** a banner shows the current engagement's privacy mode at all times.
* The pattern-friction layer in §5.1 catches accidental egress of likely secrets when cloud mode is enabled, without lying to the AI by silently rewriting content.

---

# 6. Tool Execution

## 6.1 Where Tools Run

* **Inside Docker containers** managed by the core system (isolated, reproducible, easy cleanup).
* **Docker networking:** configurable per engagement (some scopes need VPN-bound containers, others bridged, etc.).
* **Proxy support:** per-engagement SOCKS/HTTP proxy configuration for tunneling through jump hosts or VPNs.

## 6.2 Execution Semantics

* **Bounded concurrency per engagement** with a per-target lock:

  * Each engagement has a fixed pool of concurrent execution slots (default: 3, configurable).
  * Tools declare a `weight: light | heavy` in their MCP manifest.
  * Heavy tools consume a slot for their full lifetime; light tools (`httpx`, `curl`, single-request operations) may use a dedicated light-lane that does not consume the heavy pool.
  * A **per-(engagement, target-host) lock** prevents two heavy tools from running against the same host simultaneously, regardless of slot availability.
  * Light tools bypass this lock.
  * When all slots are busy or a per-target lock is held, additional runs queue FIFO.

* **No rate limits** on outgoing scans beyond the concurrency model above — trust the pentester.

* **Long-running tools continue in the background** when the user closes the browser; results appear in the UI when ready.

* **Timeout behavior:** when a tool hits its timeout, the system pauses and asks the user what to do (kill / extend / let it finish).

* **Tool profiles / presets:** every tool exposes configurable presets (e.g. `stealth`, `normal`, `aggressive`) that the AI or user can pick.

* **Manual tool use:**

  * Structured "tool runner" panel where users pick a tool, fill args, and run it.
  * Raw terminal/shell access to the execution environment.

## 6.3 Output Handling

* **Large outputs (e.g. 200 MB nmap):** stored as an artifact; the AI receives a summary; the user can view the full output on demand.
* **Unparseable output:** raw output displayed; the AI interprets on demand rather than auto-parsing speculatively.
* **Kill switches:** per-tool stop button + global engagement pause that halts all in-flight tools and pauses the AI.

## 6.4 Initial Tool Set (v1)

* nmap, gobuster / ffuf, httpx, curl.
* **Burp Suite integration: project file import only.** Users export a Burp project file and drop it into the engagement; Adeptus parses HTTP history and scanner findings as a one-shot import. No live extension and no REST polling in v1.
* Generic "shell exec" MCP server as a fallback for anything not yet wrapped.

---

# 7. MCP Server Architecture

* **Extensibility model:** one MCP server per tool category (web recon, network scanning, exploitation, etc.). New capabilities are added by deploying a new MCP server — no core code changes.

* **Both** pre-built specific servers (for the tools above) **and** a fallback shell server are shipped.

* **Discovery:** static config file at startup. The admin edits the config and restarts to add/remove servers.

* **Transport:** local subprocess over stdio.

* **Trust model:** no signing / verification — the admin is trusted to install only safe servers.

* **Capability hints in the manifest:** every MCP server's manifest declares:

  * (a) the tools it provides,
  * (b) each tool's `weight: light | heavy` (used by §6.2),
  * (c) declared capability flags: `network`, `filesystem-write`, `shell-exec`, etc.

  The admin "add MCP server" UI surfaces these declared capabilities on install with a clear warning that MCP servers run with full system privileges and that the admin is responsible for vetting them.

  The flags are informational, not enforced.

---

# 8. Knowledge Graph

## 8.1 Entities (Node Types)

* Hosts and open ports
* Services and versions
* Discovered URLs and endpoints
* Vulnerabilities (CVEs and findings)
* Credentials and hashes
* Notes and manual findings
* Attack paths and chains

## 8.2 Mechanics

* **Shared across all engagement members** (single source of truth).

* **Storage:** NetworkX in-memory for traversal, persisted to PostgreSQL.

* **Concurrency — single-writer process per engagement:** each active engagement has exactly one writer process that owns the in-memory NetworkX graph for that engagement. All graph writes (from users, the AI, and tool result ingestion) are serialized through that writer via an internal queue. Reads can be served from Postgres or from the in-memory graph.

  This eliminates write races entirely; merge mediation by the AI is reserved for **semantic** conflicts (e.g. "this service is Apache" vs "this service is nginx"), not for write ordering.

* **Writes:** AI writes automatically, flagging low-confidence inferences for human review.

* **Conflict resolution:** when two writers propose semantically incompatible values for the same node field (not a write race — the single-writer model eliminates those), the AI mediates and proposes a merge for human confirmation.

* **History:** soft delete with history — nodes can be archived and recovered. No full versioning / time-travel.

* **Manual undo (two layers):**

  * **Per-entity undo:** any node, edge, or finding can be reverted to a prior state from its soft-delete history.
  * **Personal undo stack:** each user has a personal stack of their own last ~20 graph writes that they can undo one-by-one. This is scoped to the individual user's actions and never silently reverts a teammate's work.

* There is no global "revert the whole graph to a timestamp."

* **Imports:** none. Each engagement starts with a clean graph (retest exception: prior graph available as AI context only).

* **No provenance attribution:** nodes, findings, and chat messages are not tagged with who/what created them. The audit log is the source of truth for that.

## 8.3 Visualization

* **Force-directed graph** (Cytoscape via `react-cytoscapejs`), interactive and draggable.
* **Manual annotation:** users can add free-form notes to any node and pin nodes as "interesting." Pinned nodes also act as implicit AI mentions.
* **Manual attack paths:** users can drag/drop or click-link nodes to construct attack paths.

---

# 9. Findings & Vulnerabilities

## 9.1 Severity & Classification

* **Primary classification:** Simple (Critical / High / Medium / Low / Info). This is what the UI defaults to and what the report renders.

* **Optional classifications** available on a per-finding advanced panel:

  * CVSS v3.1 / v4.0 (with vector strings)
  * OWASP Risk Rating (likelihood × impact)

* **AI behavior:** the AI proposes a Simple severity by default. It only proposes CVSS or OWASP Risk when the user opens the advanced panel or explicitly asks. The pentester confirms or overrides any proposed severity.

* **MITRE ATT&CK tagging** on findings (no explicit kill-chain modeling).

## 9.2 Lifecycle

* **Verification status:** every finding carries a status:

  * `unverified`
  * `verified`
  * `false positive`

* **Remediation status:**

  * `open`
  * `fixed`
  * `risk-accepted`
  * (updatable for retest workflows)

* **Deduplication:** AI flags potential duplicates and the user confirms merges. The user may delegate full dedup autonomy to the AI for the remainder of the engagement (instance of the general autonomy-delegation pattern).

## 9.3 Attack Paths

* AI proactively proposes new attack paths as the engagement evolves.
* Attack paths get their own section in the report with reproduction steps.

---

# 10. RAG (Retrieval-Augmented Generation)

* **Two knowledge sources:**

  * **Curated, built-in:** OWASP Top 10, CVEs, MITRE ATT&CK, attack technique references.
  * **User-supplied:** custom writeups, client documents, notes.

* **Structure:** global library + per-engagement uploads.

* **Engagement isolation:** strict — past engagements **never** feed into RAG for new ones (retest workflow is the only exception, and only on opt-in).

* **Embedding model:** local, `nomic-embed-text` via Ollama.

* **Vector store:** **pgvector** extension on the existing PostgreSQL instance.

  RAG access is enforced as a normal SQL `WHERE engagement_id = ?` filter, colocating engagement ACLs with vector data and avoiding a separate authorization layer.

  Use `hnsw` indexes for ANN search.

---

# 11. Frontend / UX

## 11.1 Tech

* **Stack:** Vite + React + TypeScript SPA.

  * One route per engagement (no full page reload when switching engagements).

* **Libraries:**

  * `react-router` for routing
  * `@tanstack/react-query` for server state
  * `zustand` for client state
  * `react-cytoscapejs` for the force-directed graph
  * `xterm.js` for the embedded terminal
  * `tailwindcss` + `shadcn/ui` for the design system
  * `react-markdown` for chat and report-preview rendering

* **Dark / light mode:** both, with system-preference detection.

* **Accessibility:** basic — usable, no formal target.

* **Languages:** English only.

* **No keyboard shortcuts / command palette** in v1. Mouse/touch only.

## 11.2 Workspace Layout

* **Three-pane layout per engagement:**

  * **Left:** chat with the AI.
  * **Right:** live force-directed graph.
  * **Bottom:** tool output console.

## 11.3 Collaboration Features

* **Presence (scoped):** show who's online in the team and which engagement(s) they currently have open.
* In the chat pane, show a per-user typing indicator.
* No node-level viewing presence and no "what tool is X running" presence indicator in v1.
* **Cross-user message sharing** via @-mention into a shared engagement channel.

## 11.4 Attachments & Files

* **File uploads per engagement** (wordlists, payloads, source code dumps).
* The AI can suggest using them in tools (e.g. as a wordlist for ffuf).
* **Screenshot / snip button** in chat to attach images to messages and graph nodes.
* **No AI vision** in v1 (images are stored and shown to humans, not analyzed by the model).

## 11.5 Session Replay

* A **timeline scrubber** UI lets users browse the engagement event-by-event after the fact.

## 11.6 AI Suggestions

* AI suggestions appear inline in chat (no separate kanban / task queue).

## 11.7 Notifications

* **In-app only** (notifications panel).

* No browser push, no email, no webhooks in v1.

* **Notification triggers:**

  * long-running tool completion,
  * approval requests,
  * mentions from teammates.

---

# 12. Reporting

* **Format:** Markdown only (no PDF / DOCX export in v1).
* **Generation:** on demand — user clicks "generate report" anytime.
* Not continuously, not auto at close.
* **Source material:** session replay + graph + findings.
* **Process:** AI generates the report from scratch — no draft-edit loop, no bullet-expansion mode.

  * Iteration happens by re-running generation, not by editing in-app.

## Mandatory Sections

1. Executive summary
2. Methodology
3. Findings with severity
4. Evidence
5. Remediation
6. Appendix

* **Attack paths:** dedicated section with reproduction steps.
* **Evidence inclusion:** auto-attached only when the user has explicitly flagged a finding for inclusion.

---

# 13. Persistence, Backup & Recovery

* **Backup strategy:** automatic periodic snapshots (DB + uploads) to a configurable path **plus** manual per-engagement export.
* **No engagement import** between instances in v1.
* **Crash recovery:** in-flight commands are marked failed on restart. No resume semantics.
* State (graph, chat, findings) survives via normal DB persistence.
* **Credentials storage:** encrypted at rest in the DB; decrypted only when shown in the UI.

---

# 14. Observability, Audit & Compliance

* **Admin dashboard:** active sessions, tool runs in progress, queue depth, errors.

* **AI debug panel:** raw prompts, model outputs, tool calls, and the exact "relevant subset" of the graph used per turn — for power users / debugging.

* **Audit log:** separate from session replay.

  * Records every tool run, AI call, graph edit, login, and approval/rejection — with user attribution.
  * Approval entries include the `self_approved` boolean (§5.2).

* **Tamper-evident audit log:** entries are hash-chained.

* **Token / cost tracking:** displayed per engagement and per user (no enforcement).

---

# 15. Testing & Quality

* **Unit tests** for core logic.
* **Integration tests** for MCP and AI flows.
* **Sandbox engagement** against intentionally-vulnerable targets (`DVWA`, `juice-shop`) for smoke tests and onboarding.

---

# 16. Tech Stack Summary

| Layer                 | Decision                                                                                                                                |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| OS                    | Linux (Kali / Parrot / Ubuntu)                                                                                                          |
| Packaging             | Docker Compose (API + DB + frontend + Ollama in one stack)                                                                              |
| Database              | PostgreSQL with `pgvector` extension                                                                                                    |
| Real-time transport   | WebSockets                                                                                                                              |
| Graph (in-memory)     | NetworkX (single-writer process per engagement)                                                                                         |
| LLM (local)           | Ollama + small quantized model                                                                                                          |
| LLM (cloud, optional) | Claude API                                                                                                                              |
| Embeddings            | Local: `nomic-embed-text` via Ollama                                                                                                    |
| Vector store          | pgvector on the existing PostgreSQL (`hnsw` indexes)                                                                                    |
| Tool execution        | Docker containers (per-engagement networking)                                                                                           |
| MCP transport         | stdio (local subprocess)                                                                                                                |
| Backend framework     | FastAPI (Python)                                                                                                                        |
| Backend libraries     | SQLAlchemy 2.x (async), Alembic, `passlib` + `argon2`, HTTP-only session cookies, FastAPI background tasks for the tool execution queue |
| Frontend framework    | Vite + React + TypeScript                                                                                                               |
| Frontend libraries    | `react-router`, `@tanstack/react-query`, `zustand`, `react-cytoscapejs`, `xterm.js`, `tailwindcss` + `shadcn/ui`, `react-markdown`      |

---

# 17. Cross-Cutting Design Principles

These should guide every implementation decision:

1. **Engagement isolation is sacrosanct.**

   * No data leaks across engagements without explicit, opt-in mechanisms.
   * RAG, attribution, search, and import all respect this.

2. **Human in the loop where it matters; autonomous where it doesn't.**

   * Recon and parsing are AI.
   * Anything that touches a target destructively, expands scope, or alters shared truth involves a human.
   * Any engagement member can act as that human — attribution is recorded, not gated.

3. **Delegation as a first-class pattern.**

   * Users can grant the AI standing autonomy on specific categories of decision per engagement (dedup, certain command classes).
   * This pattern is reusable, not one-off.

4. **The audit log is the source of truth.**

   * Provenance, attribution, and forensic reconstruction live there — not duplicated across every entity.

5. **Privacy posture is visible at all times and safe by default.**

   * Strict local-only is the default privacy mode.
   * The persistent banner shows the current engagement's privacy mode.
   * The pattern-friction layer catches accidental egress when cloud is enabled.
   * The user is never surprised about whether data is leaving the local network.

6. **The AI shows its work.**

   * Visible plan,
   * certainty percentages,
   * explicit clarifying questions when ambiguous,
   * and an inspectable "relevant subset" of the graph in the debug panel.

7. **Design for Red Team, ship for Web.**

   * v1 scope is web app testing,
   * but no architectural choice should make internal network mapping or lateral movement harder later.
