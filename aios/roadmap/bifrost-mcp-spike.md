# Spike: Bifrost native MCP support — result (2026-10-04)

**Ticket:** #14 (T1). **Verdict: PASS — Bifrost is a full MCP gateway/agent runtime. The `mcpo` path is unnecessary; delete it from the plan.**

## What was tested
- `llm.caehomelab.com` live, `/health` 200, **v2.2.3** (`/api/version`).
- `/api/config` returns real JSON containing a full `mcp_*` config block.
- `/api/mcp/clients` returns `{"clients":[],"count":0,...}` — **MCP API is live, just unconfigured.**
- `/api/governance/virtual-keys` returns 2 keys (`ce-key`, `ce-pi-macbook`), both `mcp_configs: []` (no MCP today).
- Upstream docs + OpenAPI (`docs/mcp/*`, `docs/openapi/paths/management/mcp.yaml`) confirm the contract.

## Findings (what Bifrost actually provides)
| Capability | Detail | How it maps to us |
|---|---|---|
| **MCP client** | Connect to external MCP servers over **STDIO / HTTP / SSE**; auth `none`/`headers`/`oauth`/`per_user_*`/`token_exchange` | How the NOC/patch/HA agents reach Loki, Grafana, Graph |
| **MCP server (gateway mode)** | Expose connected tools to external MCP clients | Lets Claude Desktop / other clients use the same tools |
| **Agent Mode** | Auto-executes tools in `tools_to_auto_execute`; everything else returns for approval; loops to `max_agent_depth` | **This is the R0/R1/R2 tier engine** (see below) |
| **Tool execution API** | `POST /v1/mcp/tool/execute`; stateless, explicit-approval by default | The "propose vs act" boundary, native |
| **Per-Virtual-Key tool allow-list** | `mcp_configs: [{mcp_client_name, tools_to_execute[]}]`; deny-by-default; `*` = all | **Per-agent least privilege**, native |
| **Virtual MCPs (vMCP)** | Curated bundle of tools behind `/mcp/<slug>`, stable slug, VK-scoped | One endpoint per agent role |
| **Code Mode** | LLM writes Python (Starlark) in a sandbox; ~92% fewer input tokens at 150+ tools | Later — only once many servers exist |
| **Open WebUI integration** | Explicitly documented (`docs/cli-agents/open-webui.mdx`); OWUI already points at `/v1` | Tools surface in the existing front door with no new component |
| **Observability** | MCP logs + sessions + per-request filtering built in | Feeds the "tool calls → Loki" requirement |

## The tier model is native (better than we planned)
| Our tier | Bifrost config | Semantics |
|---|---|---|
| **R0 read-only** | `tools_to_execute: [read tools]`, `tools_to_auto_execute: []` | Only read tools even exist for the model |
| **R1 propose** | `tools_to_execute: ["*"]`, `tools_to_auto_execute: []` | Model can *suggest* any call; nothing runs without an explicit `tool/execute` |
| **R2 act (confirmed)** | add the specific action tools to `tools_to_auto_execute` | Those run; the rest still require approval |

Combined with **one Virtual Key per agent** (`mcp_configs` = that agent's allow-list, deny-by-default), this gives per-agent least privilege with independent revocation **inside Bifrost**, alongside the Infisical identity. Two independent scoping layers.

## Consequences for the plan
1. **`mcpo` is dead — remove it.** `ghcr.io/open-webui/mcpo` unpublished was a red herring; Bifrost already is the MCP gateway. Delete the mcpo dependency from `ai-it-team.md`, `open-items.md`, and T3.
2. **T3 "stand up the tool plane" is mostly satisfied.** The tool plane *is* Bifrost (already running). What remains is *configuration*, not a new deployment: MCP client configs + virtual keys + the first connected server.
3. **T2 (runtime placement) shrinks.** It's no longer "where do tools execute" — only "where does the **background agent loop** live" (manager polling, scheduled digests, the scheduler watching a calendar). Still a real decision, much smaller.
4. **H2 (MCP servers) gets a constraint:** prefer **HTTP/SSE** servers. STDIO spawns subprocesses *inside the Bifrost container*, which likely lacks `npx`/`python` — the upstream docs warn about exactly this ("build a custom image or use HTTP/SSE").
5. **H1 (NOC) access is now concrete:** create MCP client(s) for Loki/Grafana/Influx read tools → bundle into a `noc` vMCP → attach to a `aios-noc` Virtual Key with `tools_to_execute` = read tools only, `tools_to_auto_execute: []`.

## Caveats / risks (write these into every agent's failure-mode list)
- **Agent Mode does not support streaming.** Open WebUI streams by default — auto-execution and OWUI streaming interact. Confirm behavior before relying on Agent Mode through OWUI.
- **Inference auth is currently OFF** (`enforce_auth_on_inference: false` in `/api/config`). `/v1/mcp/tool/execute` uses the *same* auth as inference, so today there is **no endpoint auth gate** — the only control is the Virtual Key tool allow-list. Acceptable on VLAN 30 + Tailscale, but this should be turned on before agents get write tools. **Flagged as a hardening item.**
- **Secrets in MCP configs:** server credentials (Graph, Grafana tokens) must come from **Infisical**, never pasted into Bifrost config as plaintext. Bifrost config persists to `config.db` on the PVC (survives restarts, **not** PVC recreation — re-apply after rebuild).
- **Tool-name collisions** across servers are auto-prefixed with the client name (`filesystem_list_directory`) — design names accordingly.

## Recommended next steps
1. **T4** — Define the MCP client configs + per-agent Virtual Keys (start with `aios-noc`, R0).
2. **T5** — Prove end-to-end with **one** read-only HTTP MCP server before building more (smallest possible: one tool, called successfully through OWUI/API).
3. **Hardening** — enable `enforce_auth_on_inference` before any R2/write tools exist.
4. **Later** — evaluate Code Mode once server count/tool count grows (parked).

**Not done here (deliberately):** no MCP client was created and no config was written — that's a write to shared infra and needs Chad's go-ahead (R1/R2). This spike was read-only.
