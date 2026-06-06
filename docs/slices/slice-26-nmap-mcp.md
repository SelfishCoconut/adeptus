# Slice 26: Heavy tool — nmap MCP

**Branch**: `slice-26-nmap-mcp`
**GitHub Issue**: #49
**Status**: done
**Risky**: yes (MCP execution + approval classification)

---

## Goal

Wrap nmap as an in-tree Adeptus MCP server so a user (or the AI) can run a port scan
against the sandbox with stealth/normal/aggressive presets, gated through the existing
two-tier approval + audit pipeline.

> **Plan change:** PROJECT_PLAN's Slice 26 was "nmap + gobuster". Per decision on
> 2026-06-06 this slice is **re-scoped to nmap only**; gobuster moves to a new
> follow-up slice (26b / next free number). The PROJECT_PLAN edit lands at start-slice
> time. This is the "option A" path — wrap one real tool natively, the Adeptus way
> (no third-party orchestrator).

## User-visible demo

After merge, on the running stack against the Juice Shop sandbox:
- `run_nmap` appears in the tool list (`GET /api/v1/mcp/tools`) with stealth/normal/aggressive presets.
- Proposing/launching `run_nmap` against `juice-shop:3000` (or its host) **gates as an
  approval request** with reason `aggressive_scan` (nmap is `weight=heavy`).
- After approval, the scan runs, streams output, and the completed `tool_run` shows the
  open-port / service table in its captured stdout.
- Running nmap against a **non-sandbox** target is refused by the central sandbox guard
  (dev/test), same as every other targeted tool.

## Out of scope

- **Parsing nmap output into the graph** (hosts/ports/services as entities) — separate slice.
- **gobuster** — moved to its own follow-up slice.
- **Privileged scans** requiring raw sockets (`-sS` SYN scan, `-O` OS detection) — all
  presets use unprivileged TCP connect (`-sT`) so the tool works without `CAP_NET_RAW`.
- **New frontend components** — nmap surfaces through the existing registry-driven tool
  runner + approval card; no bespoke UI.

## Requirements traceability

- §6.4 — nmap as a supported pentest tool
- §6.2 — tool presets (stealth / normal / aggressive)
- §5.2 — heavy/aggressive scans require approval (reuses Slice 16/17 pipeline)

## Contract

No new HTTP endpoints. nmap is exposed entirely through the existing MCP registry +
tool-run endpoints:
- `GET /api/v1/mcp/tools` — now includes `run_nmap` (ToolDescriptor shape unchanged).
- `POST /api/v1/tool-runs` — accepts `server=nmap, tool=run_nmap` like any other tool.

The `ToolDescriptor` / `McpServerInfo` schemas are unchanged, so **no OpenAPI client
regeneration is needed** unless `make generate-api` reports a diff (it should not).

## Data model changes

None. nmap runs reuse the existing `tool_runs` table and the approval pipeline. No migration.

## Manifest + config sketch

`mcp-servers/nmap/manifest.yaml` (and mirrored into `mcp-servers/config/mcp.yaml`):

```yaml
name: nmap
tools:
  - name: run_nmap
    weight: heavy            # → always gates as aggressive_scan (§5.2)
    capability_flags: [network]
    presets:
      - name: stealth
        description: Slow top-100 TCP connect scan, no host discovery
        args: {flags: ["-Pn", "-sT", "-T2", "--top-ports", "100"]}
      - name: normal
        description: Top-1000 TCP connect scan
        args: {flags: ["-Pn", "-sT", "-T3", "--top-ports", "1000"]}
      - name: aggressive
        description: Top-1000 connect scan with service/version detection
        args: {flags: ["-Pn", "-sT", "-sV", "-T4", "--top-ports", "1000"]}
    arg_schema:
      type: object
      required: [target]
      properties:
        target: {type: string, title: Target host or URL}
        flags: {type: array, items: {type: string}, title: Extra flags}
        timeout_seconds: {type: integer, minimum: 1, maximum: 600, default: 120, title: Timeout (s)}
```

All presets are unprivileged (`-sT`, no `-O`), so nmap runs without `CAP_NET_RAW`.
`-Pn` skips host discovery (sandbox containers often drop ICMP). The `aggressive`
preset name is already in `AGGRESSIVE_PRESETS`, and `weight=heavy` gates regardless.

## Tasks

Numbered continuously across the slice. Every commit subject cites `(task N)`.

### Backend / MCP tasks

1. **[M]** Add `mcp-servers/nmap/server.py` — JSON-RPC stdin/stdout server modeled on
   `mcp-servers/httpx/server.py`: streaming `tools/output` notifications, 1 MB output
   cap + sentinel, `timeout_seconds` → `killpg(SIGKILL)`, `start_new_session=True`.
   **Apply the PR #48 lessons from day one:** resolve the binary by absolute path via
   `_resolve_nmap_binary()` (`$ADEPTUS_NMAP_BIN` → `/usr/bin/nmap` → `shutil.which`),
   and pass `stdin=DEVNULL`. nmap takes its target as a **positional** arg (unlike
   httpx's `-u`), so `argv = [bin, *flags, target]` is correct here.
2. **[M]** Flag **allowlist** in the nmap server (security review changed this from a
   denylist — see Risk 2). nmap accepts unambiguous long-option abbreviations and treats
   any bare token as an extra target, so a denylist is bypassable. `_validate_flags`
   permits only an exact set of bare flags (`-Pn -n -sT -sV -F -6 --open --reason -T0..5
   -v -vv -d`) and value flags with validated values (`-p`, `--top-ports`,
   `--version-intensity`, `--max-retries`, `--min/max-rate`, `--host-timeout`). Everything
   else — unknown/abbreviated flags, NSE (`-sC`/`--script*`), aggregate `-A`, privileged
   scans (`-sS`/`-sU`/`-O`), file output (`-oN`…), egress/pivot (`--proxies`/`-b`), and
   **bare positional targets** — is rejected before exec. Case-sensitive. Tests for each.
3. **[S]** Add `mcp-servers/nmap/manifest.yaml` (per sketch) and register the server in
   `mcp-servers/config/mcp.yaml`.
4. **[S]** Install nmap in `backend/Dockerfile` (`apt-get install -y --no-install-recommends nmap`),
   following the existing curl/unzip cleanup pattern.
5. **[S]** Defense-in-depth classification: add `"nmap"` to `AGGRESSIVE_SCAN_TOOLS` in
   `backend/app/features/approvals/config.py` (weight=heavy already gates; this is
   belt-and-suspenders) + a classifier test asserting `run_nmap` → `aggressive_scan`.
6. **[S]** Verify nmap is covered by the central sandbox guard
   (`_enforce_sandbox_guard` in the tool-run service): add/confirm a test that
   `run_nmap` against a non-sandbox target is refused, and against `juice-shop` is allowed.
7. **[S]** nmap server unit tests (`mcp-servers/nmap/tests/test_nmap_server.py`) — mirror
   the httpx suite: happy path (mocked subprocess), denylist, missing/empty target,
   binary-resolution, timeout→124, output cap, JSON-RPC dispatch. Coverage ≥80%.

### Frontend tasks

8. **[S]** Confirm `run_nmap` renders in the existing tool runner / approval card from
   the registry (no new components). Regenerate OpenAPI types only if `make generate-api`
   shows a diff. Add a minimal RTL assertion only if a rendering gap is found.

### Integration / E2E

9. **[M]** Integration test (sandbox compose stack): propose `run_nmap` against the
   sandbox → assert it gates (`aggressive_scan`) → approve → assert the run executes and
   captured stdout contains a port/service line. Tool NEVER runs against non-sandbox.

## Test plan

- **Unit (mcp-servers)**: `nmap` server suite ≥80% (`make test-mcp-servers`); ruff + mypy clean per-server.
- **Unit (backend)**: classifier test (`run_nmap` → `aggressive_scan`); sandbox-guard test.
- **Integration**: the gate→approve→run happy path against Juice Shop only.
- **E2E**: none new unless task 8 finds a UI gap.

## Acceptance criteria

- `make test` passes (backend + mcp-servers + frontend + lint + typecheck).
- On the running stack: launching `run_nmap` against the sandbox gates, and after
  approval returns a real nmap port/service table in the tool-run output.
- Non-sandbox target is refused in dev/test.
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — privileges.** SYN scan (`-sS`) and OS detection (`-O`) need raw sockets
  (`CAP_NET_RAW`), which the backend container may lack. *Mitigation:* every preset uses
  unprivileged `-sT` connect scan and omits `-O`; no capability change to the container.
- **Risk 2 — flag containment (denylist → allowlist).** Security review showed a denylist
  is unsafe for nmap: it accepts unambiguous long-option **abbreviations** (`--proxi` ≡
  `--proxies`) and treats any bare token as an **extra target** (smuggling a host past the
  single-`target` guard). *Mitigation:* the server uses a strict **allowlist**
  (`_validate_flags`) — only known bare/value flags pass; NSE (`-sC`/`--script*`),
  aggregate `-A`, privileged scans, file output, egress/pivot, and bare positionals are
  all rejected before exec. Presets use no NSE (aggressive = `-sV` only). A curated
  safe-category NSE allowlist can be added later.
- **Risk 3 — output volume / runtime.** Full scans are slow and verbose. *Mitigation:*
  `--top-ports` bounds the scan; 1 MB output cap; `timeout_seconds` (default 120, max 600)
  kills the process group; heavy-tool admission (Slice 05) serializes per host.
- **Risk 4 — binary-resolution regression.** Repeat of the httpx PATH bug. *Mitigation:*
  absolute-path resolver + `stdin=DEVNULL` built in from task 1, with tests.

## Open questions for the human

- Aggressive preset is unprivileged (`-sT -sV`, no NSE, no `-O`). Want a privileged
  variant later (OS detection / SYN) that requires granting the container `CAP_NET_RAW`?
- Default timeout 120 s / max 600 s acceptable for sandbox scans?

## Security review required?

**Yes — completed.** security-reviewer (2026-06-06) found two Criticals on the original
denylist design: positional-target injection bypassing the sandbox guard, and long-option
abbreviation bypassing the denylist (plus `-sC`/`-A` NSE gaps). All remediated by
switching to a strict **flag allowlist** (`_validate_flags`) that rejects bare positionals
and any non-allowlisted/abbreviated flag, plus a server-side `timeout_seconds` clamp.
Verified: filesystem-write / NSE / egress-pivot / sandbox-bypass / positional-injection
all refused before exec; `weight=heavy` + `run_nmap` in `AGGRESSIVE_SCAN_TOOLS` gate;
binary resolved by absolute path.

## Progress

(Leave empty at planning time.)
- 2026-06-06T10:16:59Z — 1c37d96 docs(slice-26): nmap slice spec + plan re-scope to nmap only
- 2026-06-06T10:35:34Z — 5c98647 docs(slice-26): mark slice done in plan + spec
