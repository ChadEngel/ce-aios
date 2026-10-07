# Decision log (AIOS) — newest first. Why we chose X, so nobody re-litigates it.

## 2026-10-07 — T2 decided: background agent loop runs in-cluster; the Mac Studio stays the inference provider, not a runtime host
**Chosen: Option B (in-cluster), `aios-manager` in ns `ai`** (Manager Phase 1, and the same loop-home pattern for future personal agents). Supersedes the Feb Compose-on-Studio design — that predates the T1 spike; scope shrank to "where does the background LOOP run" after `#14` proved Bifrost already is the tool plane.

- **One secret path:** the InfisicalSecret → operator → k8s Secret sync is already proven in production here (`bifrost-secrets`, `mcp-grafana-secrets`, 9 syncs). Compose-on-Studio would mean a second client + a second audit surface.
- **One observability path:** util-server already runs Loki + Grafana; manifests route agent logs there by default. Studio would duplicate pipeline config.
- **One network fabric:** util-server sits in the lab VLAN today; the agent loop needs egress to `llm.caehomelab.com` (Bifrost), `secrets.caehomelab.com` (Infisical), `github.com` (issues API) — all reachable, one NetworkPolicy away. Studio placement would add another firewall allow-list to maintain.
- **Survives the Studio:** the Studio is where interactive pi sessions and Ollama live; it gets rebooted/updated/idled by a human. The background loop must be always-on — that's the util-server's job description.
- **Load fact (measured 2026-10-07):** util-server at 6% CPU, ~1.9Gi/4.9Gi allocatable used — the loop (~100–200Mi) fits trivially.
- **Rejected: 2Gi limits on the same host** for the same reason (`#34` chose 1536Mi). Option A would additionally couple NOC uptime to Docker Desktop licensing/updates on macOS.
- **What stays on the Studio:** Ollama native/Metal (the inference provider for local models), interactive pi sessions, anything Chad runs by hand. The loop only consumes them over the network.
- **Phase-1 shape (unchanged from `manager-agent.md`):** `aios-manager` Deployment (or CronJob for the poller — decide at build time), GitHub App (not PAT) for Issues read/write, private key via InfisicalSecret sync, LLM via Bifrost `/v1` with the agent's own VK, tools via `/mcp/*`, R0/R1 only, every tool call → Loki.

## 2026-10-04 (CORRECTED) — `config.json` is how private-network MCP clients get registered
**Correction:** I first claimed Bifrost blocks private-IP MCP clients until dashboard auth is on. **Wrong.** The `rejectPrivateMCPTargetIfAuthBypassed` guard fires **only** on the HTTP management API and **only when auth is bypassed** (`BifrostContextKeyAuthBypassed`); the `config.json` startup path (`loadMCPConfig` → `CreateMCPClientConfig`) never calls it. Docs: *"…or define the client in `config.json` instead."* So **`config.json` (git-tracked, declarative) both satisfies the reproducible-from-git rule and bypasses the auth gate.** Enabling dashboard auth is now **optional hardening**, not a prerequisite. Also: `source_of_truth: split` (default) merges DB + file per-section, so adding `mcp.client_configs` won't drop existing DB virtual keys. Trap: never set `version: 1` — `applyV1Compat` backfills all clients into any VK with empty `mcp_configs`. (2) **`grafana/mcp-grafana`** (official, Go) covers **Loki + Grafana + InfluxDB** in one server, read-only via `--disable-write`, HTTP via `streamable-http`. Design: `roadmap/t4-noc-mcp-design.md`.

## 2026-10-04 — Spike #14: Bifrost IS the tool plane; drop `mcpo`
Bifrost v2.2.3 (live at `llm.caehomelab.com`) is a full **MCP gateway + agent runtime**: MCP client (STDIO/HTTP/SSE), gateway mode, Agent Mode (auto-execute), per-Virtual-Key tool allow-lists (deny-by-default), Virtual MCPs, Code Mode. The `mcpo`/unpublished-image blocker was a red herring — **remove `mcpo` from the plan.** The R0/R1/R2 tiers map natively to `tools_to_execute` vs `tools_to_auto_execute` + one Virtual Key per agent. Remaining tool-plane work is *configuration* (T4/T5), not a new component. Caveats: Agent Mode has no streaming; `enforce_auth_on_inference` is currently **OFF** (harden before write tools); server creds from Infisical only; prefer HTTP/SSE servers (STDIO needs deps in the container). Full write-up: `roadmap/bifrost-mcp-spike.md`.

## 2026-10-04 — Grafana password rotation is end-of-project, not a blocker
Chad: Grafana `admin`/`admin` is not a blocker for any of this work and should not be tracked on the critical path — it gets done at the very end. Issue #4 closed (labeled `prio:parked`). The secrets critical path is the **Infisical-internal** items (AUTH_SECRET, Postgres password, ENCRYPTION_KEY) + the Cloudflare token, not Grafana.

## 2026-10-04 — Work tracking moves to GitHub Issues + a manager agent
Use **GitHub Issues** on `ChadEngel/ce-aios` as the durable work queue (labels + milestones), and build a **manager agent** to groom/dispatch it. Rationale: auditable, versioned, lives with the docs, no new database. Manager runs **Phase 0 now** as an in-session `gh` protocol (R0 read / R1 label+comment, never closes or executes); Phase 1 (autonomous in-cluster service, GitHub App auth) is blocked on the tool plane. GitHub Actions rejected as the runtime (would need an LLM key as a GitHub secret; Bifrost is LAN-only). Detail: `roadmap/manager-agent.md`.

## 2026-10-04 — InfluxDB migration DEFERRED (working as-is, not a blocker)
Chad: the aiserver→cluster InfluxDB migration is **not cut over**, but it is working as-is and should **not** be treated as an open priority or a blocker. Level 2 remaining scope = Infisical secrets consolidation only. Do not re-litigate Influx placement unless Chad raises it.

## 2026-10-04 — Personal agent roster added (scheduler, travel, financial)
Beyond the IT team, add a *personal* roster on the same platform: scheduler, vacation/travel agent, financial planner. Not IT — life admin — but same prerequisites (Level 2, per-agent Infisical identity, Entra app per agent, R0→R2). Recommended order: scheduler bundled with the helpdesk Graph work (protects family time + attacks the finishing problem), travel episodic (first real job = March 2027 MSP→OGG), financial last and R0-only (highest risk; data source unconfirmed). Flagged once: three new threads compete with Level 2 — park travel/finance until Level 2 closes. Detail: `roadmap/personal-agents.md`.

## 2026-10-04 — Tenant confirmed: personal `engelmn.com` (M365 Business Premium)
Chad confirmed the tenant is personal, with Teams licensing and "Azure Plan 2" (meaning TBD). Employer tenant remains off-limits. Per-agent Entra app registrations, mailbox-scoped, no send/delete; Teams two-way via Azure Bot only after outbound-only phase. Chad creates registrations. See `roadmap/ai-it-team.md`.

## 2026-10-04 — Email/Teams are Microsoft 365, not Google
Chad runs M365 and can create Entra app registrations. Use Microsoft Graph (not Gmail API). Tenant (personal vs employer) still UNANSWERED; employer tenant is off-limits without explicit policy clearance. See `roadmap/ai-it-team.md`.

## 2026-10-04 — Goal is an "AI IT team"; build one hire at a time
Order: NOC (read-only) → Patch → Facilities (HA) → Helpdesk (M365 email/Teams) → Network. Orchestrator alongside #1. Level 2 (secrets) is the literal prerequisite — agents need scoped, revocable, audited credentials like any new IT hire.

## 2026-10-04 — Reject ownjarvisai.com
It's a hosted SaaS (Gmail/Stripe/LinkedIn/HubSpot via their cloud), $47 "founding" offer with deadline, stacked bonuses, unverifiable backing claim, upsell. Not self-hostable, no framework to conform to, opposite of the Infisical/VLAN-scoped trust model. Decision: build the equivalent ourselves on Open WebUI + Bifrost + Ollama + MCP. Do not log into or give credentials for third-party member portals.

## 2026-10-04 — No k3s on the Mac Studio; k3s node idea withdrawn
k3s needs Linux (so macOS would need a VM) and nothing in Chad's goal requires it: Ollama already runs there and Open WebUI is reachable everywhere. If an agent runtime moves to the Studio, use Docker Compose (Chad's Feb 2026 design), keep Ollama native (Metal GPU), front it with the existing Traefik. (Earlier "join as k3s worker" advice was over-engineered.)

## 2026-10-04 — Home Assistant is outside scope → exception required
HA = 192.168.250.168 (VLAN 1). Needs Chad's explicit approval + narrow firewall allow before any agent touches it. PENDING.

## 2026-10-04 — Don't roll back UDM firmware 5.1.33 (from home-network-config)
Port flap root-caused to Tablo t4g → STP TCN on CAESW002; fixed by locking port 9 to 100M FDX. Source: that repo's decision log.

## 2026-08-17 — Claude vs. Bifrost-routed models
Keep strategy/orchestration/long-horizon work on a frontier model (open models still lose coherence in long agent loops); route narrow, repeatable, private, or high-volume work (Lauren's workspace, scripted homelab tasks) to local models via Bifrost. Self-hosting only pays economically above ~5–10M tokens/month. Re-test the orchestrator role empirically.

## 2026-08-17 — Kids' AI-literacy idea: build for Lauren first, not a business
Market is not empty (Learning.com, Day of AI, Common Sense, state policy). Niche = local model on family hardware with parent-controlled filtering. Do not scope a company before one kid uses it.

## 2026-08-17 — Level 2 target: close Influx migration + all secrets into Infisical
Chosen over new builds because it's nearly done, Chad named it himself, and it gates safe agent access.

## 2026-08-16 — Hard rules established
Everything through Infisical (no exceptions); infra work scoped to 192.168.30.0/24 and ask before leaving it; direct, low-fluff voice; one recommendation not a menu; "unconfirmed" over guessing.

## 2026-08-16 — Pattern identified
Chad has many good ideas and finishes few, limited by unscheduled free time (full-time job, kid's sports, family). Default to finishing the nearly-done thing; flag competing new threads once.
