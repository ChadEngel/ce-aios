# Decision log (AIOS) — newest first. Why we chose X, so nobody re-litigates it.

## 2026-10-04 — T4: Bifrost dashboard auth is a prerequisite; `mcp-grafana` is the one NOC server
Two T4 findings. (1) **Bifrost refuses MCP clients pointing at private/loopback addresses unless dashboard auth (admin password) is enabled** — reproduced live (403). Every lab service is RFC1918 → **enabling Bifrost auth (T6, plus `enforce_auth_on_inference`) is a hard prerequisite** for any MCP client. Bonus: it closes the no-auth-gate-on-tool-execution hole from #14. (2) **`grafana/mcp-grafana`** (official, Go) covers **Loki + Grafana + InfluxDB** in one server, read-only via `--disable-write`, HTTP via `streamable-http` → old H2 "build MCP servers" becomes "deploy one container." Recommend moving Bifrost config to a **git-tracked config.json ConfigMap** (declarative) instead of config.db-only, pending a precedence check. Design: `roadmap/t4-noc-mcp-design.md`.

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
