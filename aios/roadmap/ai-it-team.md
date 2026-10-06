# The AI IT team — design & build order (decided 2026-10-04)

**Vision (Chad):** "a whole IT team running in AI for my home lab." Reach it one hire at a time. This is the concrete form of AIOS Level 5.

## Architecture
- **Front door:** Open WebUI (ai.caehomelab.com) for all devices via LAN/Tailscale; later Teams (and push via Pushover) for chat/alerts.
- **Orchestrator ("IT manager"):** one model behind Bifrost that routes to specialists. This role needs reliable multi-step tool calling — test local models against a fixed multi-tool task set before trusting one; fall back to a frontier model via Bifrost for this role only if needed. Specialists can use local models via Ollama.
- **Tools:** MCP servers, bridged to Open WebUI with `mcpo` (already scoped in ce-ai-lab, README only — not built).
  - **UPDATE 2026-10-04 (spike #14): `mcpo` is dead.** Bifrost (`llm.caehomelab.com`, v2.2.3) is itself a **full MCP gateway + agent runtime** — MCP client (STDIO/HTTP/SSE), gateway mode, Agent Mode, per-Virtual-Key tool allow-lists, Virtual MCPs, Code Mode. The tool plane is *configuration of Bifrost*, not a new component. See `bifrost-mcp-spike.md`.
  - **Tier model is native:** `tools_to_execute` (what the model may see) vs `tools_to_auto_execute` (what runs without approval) + one Virtual Key per agent = R0/R1/R2 + per-agent least privilege, inside Bifrost. Second scoping layer alongside each agent's Infisical identity.
- **Runtime placement (leaning):** agent/MCP runtime as Docker Compose on the Mac Studio (Chad's own Feb design; no k3s, no VM), Ollama stays native; ingress through existing cluster Traefik so TLS/URLs don't change. Secrets injected at start from Infisical (`infs`/CLI), never baked into compose files. UNDECIDED — confirm with Chad.
- **Observability of the agents themselves:** tool calls → Loki; token/latency → InfluxDB/Grafana.

## Roster & order
| # | Role | Job | Data/tools | Tier | Access needed | Status |
|---|---|---|---|---|---|---|
| 0 | Prereq | Close Level 2: placeholders rotated, Influx cut over, secrets canonicalized, per-agent identities | Infisical | — | human | OPEN |
| 1 | **NOC tech** (hire #1) | "What's broken / what changed?" — triage alerts, LogQL/Flux queries, summarize | Loki, Grafana, InfluxDB read, kubectl read-only | R0 | Infisical identity `aios-noc` (proposed): INFLUXDB_READ_TOKEN + Grafana viewer + Loki read + k8s read-only SA. VLAN 30 only | NEXT |
| 2 | Patch tech | Report image/firmware/OS currency vs digest-pinned versions; "tell me, don't apply" | registry/GitHub release feeds, kubectl read | R0→R1 | read-only + internet egress | after #1 |
| 3 | Facilities tech | Home Assistant re-design help, entity/automation audit, proposed YAML PRs via the HA repo deploy pipeline | HA REST/MCP, HA repo | R0→R1 | read-only HA token; **scope exception + firewall allow pending** | blocked on approval |
| 4 | Helpdesk / ops coordinator | Email triage (move to folders, flag/highlight important) and Teams/text interface | Microsoft Graph (M365), Teams, Pushover | R1→R2 | Entra app registration(s) — see below | UNBLOCKED (tenant confirmed); waits for hires #1–#3 |
| 5 | Network tech | Segmentation plan execution support, firewall-rule review, UDM config diffing | UDM read-only API key, home-network-config repo | R0→R1 | UDM_API_KEY read-only; never SSH/root | later |
Orchestrator work starts alongside #1 (route NOC questions to the NOC tool).

## Tool plane status (updated 2026-10-04 after spike #14)
**Bifrost IS the tool plane.** No `mcpo`, no new gateway. Remaining work is configuration:
- `T4` define MCP client entries (prefer HTTP/SSE) + one Virtual Key per agent.
- `T5` prove one read-only tool end-to-end before building more.
- **Hardening before any write tool:** `enforce_auth_on_inference` is currently **OFF**, and Agent Mode does not support streaming (Open WebUI streams by default).
- Server credentials come from **Infisical**, never plaintext in Bifrost config (`config.db` on the PVC does not survive PVC recreation — re-apply after rebuild).

## T4 design result (2026-10-04) — see `t4-noc-mcp-design.md`
**No blocker (corrected).** Bifrost supports private/loopback MCP clients with dashboard auth **off** — via **`config.json`**. The `rejectPrivateMCPTargetIfAuthBypassed` guard fires only on the HTTP API *and only when auth is bypassed*; the `config.json` startup path never calls it. Docs: *"…or define the client in `config.json` instead."* So the declarative git-tracked config.json is both the right choice **and** the unblocking one. Enabling dashboard auth (T6) is now **optional hardening**, not a prerequisite.
**One server covers NOC:** `grafana/mcp-grafana` (official, `--disable-write`, `streamable-http`) = **Loki + Grafana + InfluxDB**. Old "build MCP servers" (H2) becomes "deploy one container."
**Next:** deploy `mcp-grafana` read-only → write the git-tracked `config.json` (`grafana-noc` client + `aios-noc` VK, explicit `mcp_configs`, **no `version: 1`**) → prove one LogQL query (T5).

## Helpdesk on M365 (personal tenant `engelmn.com`; Chad creates app registrations)
- **Mail:** Microsoft Graph via an Entra app registration (one per agent). Needs `Mail.ReadWrite` to move/flag; app-only permissions are tenant-wide by default, so **restrict to a single mailbox** (Exchange Online RBAC for Applications / application access policy — verify current Microsoft guidance). Prefer certificate or short-lived secret stored in Infisical, with expiry + rotation. v1: read + categorize + flag + move (moves are reversible); **no send, no delete**. Treat message content as untrusted data (prompt injection).
- **Teams:** two-way chat needs an Azure Bot (single-tenant) whose messaging endpoint is a public HTTPS URL — this conflicts with the lab's no-inbound-ports posture. Options, in order: (1) outbound-only first (Pushover now; Teams incoming webhook/Workflow), (2) later add a Cloudflare Tunnel (Chad already uses Cloudflare) exposing only the bot endpoint, (3) avoid exposing anything by polling. Decide when we get there.
- **Tenant CONFIRMED (2026-10-04, per Chad):** `engelmn.com` is a **personal** tenant running **M365 Business Premium**, with Teams licensing and "Azure Plan 2" (meaning UNCONFIRMED — see open-items Q5). The employer tenant is NOT used for any of this and stays off-limits.
- **Caveats to verify:** Business Premium includes Entra ID P1 (not P2); Azure Bot needs an Azure subscription (Q5); app-only Mail permissions are tenant-wide unless scoped to specific mailboxes (Exchange RBAC for Applications); Teams two-way needs a public HTTPS endpoint, so outbound-only (Pushover/webhook) first.
- **Hygiene:** one single-tenant Entra app registration per agent, named `aios-<role>`; cert credential preferred, else short-expiry secret stored only in Infisical; Chad (not the AI) creates registrations and grants consent; review sign-in/audit logs for each app; no `Mail.Send`, no delete.
- **Teams inbound options (researched 2026-10-04; no decision yet):**
  1. *Azure Bot + Cloudflare Tunnel* (leaning): free, no new vendor, no inbound ports; bot endpoint validates Microsoft's JWT. Needs an Azure subscription for the Azure Bot resource (Q5).
  2. *Azure relay*: Azure Function (consumption free grant) receives bot messages, drops them in a queue; the agent on the Studio polls outbound and replies via the Bot Connector API. Zero inbound at home; more moving parts.
  3. *Graph change notifications*: webhook needs a public URL; Event Hubs delivery avoids a public endpoint but needs Event Hubs + Key Vault + Storage (not free; check pricing). Channel-message APIs may be protected/metered — verify before relying.
  4. *Polling a private channel via Graph*: no endpoint, ~30–60 s latency, but app-only channel read needs `ChannelMessage.Read.All` (tenant-wide; possible protected-API approval) — weaker than a bot.
  - **Oracle Cloud free tier: not recommended.** Halved to 2 OCPU/12 GB Ampere A1 in mid-2026 without announcement; reclamation/signup risk; adds another vendor and credential set.
- Text/SMS: Pushover for push; true SMS would need a provider (Twilio etc.) — not started.

### App registration register
| App | Purpose | Graph perms (app) | Scope limit | Status |
|---|---|---|---|---|
| `aios-helpdesk-mail` | read, categorize, flag, move mail | Mail.ReadWrite (no Mail.Send) | single mailbox via Exchange RBAC | not created |
| `aios-teams-bot` | two-way chat | none initially | endpoint via Cloudflare Tunnel (path-limited) | not created |
| `aios-calendar` (optional) | read calendar | Calendars.Read | single mailbox | not created |

## Definition of "hired" (promotion gate per agent)
1. Own Infisical identity, least privilege, documented here. 2. Firewall allow-list in place. 3. Runs R0 for 2 weeks with logged calls Chad has reviewed. 4. Failure-mode list written. 5. Only then R1/R2, one action type at a time.
