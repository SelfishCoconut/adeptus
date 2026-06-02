---
name: add-mcp-server
description: |
  Scaffolds a new internal MCP server for Adeptus (one that ships as part
  of the product, not a Claude Code MCP). Copies the template at
  mcp-servers/_template/, fills in the manifest with tools, weight, and
  capability flags, and adds the server to the static MCP config. Use
  when the user says "add an MCP server for X", "wrap tool Y as MCP",
  or when a slice introduces a new pentest tool category.
allowed-tools: Read, Write, Edit, Bash
---

# Add an internal MCP server

## Steps

1. Confirm `mcp-servers/_template/` exists. If not, STOP — the template should have been created in slice 03.

2. Confirm the new server name doesn't collide with an existing folder under `mcp-servers/`.

3. Copy the template:
   ```
   cp -r mcp-servers/_template mcp-servers/<name>
   ```

4. Update the manifest at `mcp-servers/<name>/manifest.json`. Required fields per requirement §7:
   - `name`: server name (kebab-case)
   - `description`: one-line purpose
   - `tools`: array of tools provided. For each tool:
     - `name`
     - `description`
     - `weight`: `"light"` or `"heavy"` (see requirement §6.2 — light tools bypass per-target lock and use a dedicated lane)
     - `input_schema`: JSON Schema for arguments
   - `capabilities`: declared flags. Truthful — these are surfaced in the admin install UI as informational warnings.
     - `network`: makes outbound network calls
     - `filesystem-write`: writes to disk
     - `shell-exec`: spawns subprocesses

5. Implement the server's main file at `mcp-servers/<name>/server.py` (or `.ts` if the template is TypeScript) following the template's structure:
   - stdio transport
   - Handle `tools/list` from the manifest
   - Handle `tools/call` by dispatching to the appropriate handler
   - Stream output for long-running tools so the FastAPI side can show progress
   - Honor a kill signal (SIGTERM should terminate the whole process group)

6. Add the server to the static MCP config at `backend/config/mcp-servers.yaml`:
   ```yaml
   - name: <name>
     command: ["python", "-m", "mcp_servers.<name>"]   # or equivalent
     enabled: true
   ```

7. Write tests at `mcp-servers/<name>/tests/`:
   - One unit test per tool that mocks the underlying CLI
   - One integration test (marked `@pytest.mark.integration`) that runs against the Juice Shop sandbox if applicable

8. Update `docs/runbooks/mcp-servers.md` with a one-paragraph entry about the new server and its capabilities.

9. Stage and commit:
   ```
   git add mcp-servers/<name> backend/config/mcp-servers.yaml docs/runbooks/mcp-servers.md
   git commit -m "feat(slice-NN): add <name> MCP server (task N)"
   ```

## Hard rules
- The `weight` and `capabilities` fields are NOT enforced by the system (per requirement §7), but they MUST be truthful. The admin UI displays them to users — lying defeats the purpose of the trust model.
- Pentest tools wrapped in an MCP server must STILL respect the sandbox-only rule during development and testing. The pre-bash-guard hook will block accidental runs, but the integration tests must explicitly target juice-shop.
- Never invoke a pentest tool with `shell=True` and unsanitized arguments. Argument lists only.
- Never write to filesystem paths the user didn't authorize. If a tool produces large output (>10MB), stream it to the artifact storage path the FastAPI side passes in, not random temp files.
- The MCP server runs with the same privileges as the FastAPI process. Don't add features that require root unless absolutely necessary, and document them prominently in the manifest's description.
