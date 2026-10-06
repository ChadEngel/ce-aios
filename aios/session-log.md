# Session log

## 2026-10-06 (session 5)
- **Chad said "go" → executed T4/T5 Step 1–2 live.** Infisical sync, mcp-grafana deploy, Bifrost ConfigMap + restart all applied. Full state handoff: `roadmap/t4-t5-execution-state.md` (authoritative).
- **Bug 1 (mine, fixed): MCP client names forbid hyphens** — `grafana-noc` rejected at startup, so the VK's `mcp_configs` also failed to resolve. Renamed to `grafana_noc` everywhere; re-applied → registered, 20 tools discovered. VK names still allow hyphens.
- **Bug 2 (upstream, open): Bifrost `latest`+Always pulled v2.2.6 (released that day) which adds an OSS `/api` setup lock** — all management routes 403 while dashboard auth is off and no `setup_token` exists. Inference + `/mcp/*` unaffected. Options A (setup_token from Infisical) vs B (enable dashboard auth = T6). Awaiting Chad.
- **Merge safety proven at the DB layer:** `ce-key` + `ce-pi-macbook` VKs unchanged, `aios-noc` auto-generated. Verified via sqlite on a copied config.db (`/api` was locked).
- **T5 half-proven:** `/mcp/grafana-noc` initialize → 200; `tools/list` → 20 read-only-tiered tools. Actual tool **call** still pending.
- **Next:** T5 tool call → close #31/#30; Chad decides setup_token + image pinning (latest+Always caused silent v2.2.3→v2.2.6 drift); then Chad's unrelated Grafana task.

## 2026-10-06 (session 4)
- **T4 (#30) R1 artifacts written** to `ce-ai-home-lab` (nothing applied): `applications/mcp-grafana/` (Deployment+Service+NetworkPolicy, pinned `grafana/mcp-grafana:2.0.1`), `bifrost-config` ConfigMap + `subPath` mount at `/app/data/config.json`, and `mcp-grafana-secrets-sync` in `infisical-secrets-sync.yaml`. All pass `kubectl apply --dry-run=client`.
- **Grafana service-account token verified live**: `sa-1-ai-token` (`service-account:2`, not admin) — Loki labels via datasource proxy → 200; `POST /api/folders` → 403 (read-only confirmed).
- **Two traps found in Bifrost/mcp-grafana source** (both would have caused silent failure):
  1. Bifrost sends header values **verbatim** (no auto-`Bearer`) and resolves `env.X` only when the whole value is `env.X` (no `${}` interpolation); mcp-grafana **requires** `Bearer `. → caller auth needs **two** derived secret values. Ran with caller auth off + NetworkPolicy instead.
  2. mcp-grafana `--allowed-hosts` defaults to loopback and validates **every** route: Bifrost's Service-DNS Host and k8s `httpGet` probe Host (pod IP) both 403. Fixed with explicit `--allowed-hosts` + `tcpSocket` probes.
- **Correction (assistant was wrong):** asserted HTTP/SSE is unsupported on the config.json path — false (guard is management-API-only; `IsPublicIP` appears once in the tree, in `handlers/mcp.go`). Also asserted mcp-grafana can't do stdio — false (stdio is its default). Chose HTTP anyway because Bifrost's stdio client exec()s a *local* binary not in its image. Lesson repeated: read the load path before declaring unsupported.
- Pinned image tags on Docker Hub **omit the `v`** (GitHub `v2.0.1` = image `2.0.1`).
- **Where we left off:** artifacts are R1/files-only. Next: Chad applies `mcp-grafana` + restarts Bifrost → **#31 (T5)** one-LogQL end-to-end proof. Follow-ups: Q-T4-5 caller auth (`MCP_GRAFANA_SERVER_TOKEN`), Q-T4-6 remove orphan `GRAFANA_API_TOKEN` from Infisical.

## 2026-10-04 (session 3)
- Ran spike **#14 (T1)**: **PASS — Bifrost v2.2.3 is a full MCP gateway + agent runtime.** `mcpo` is dead (removed from plan). R0/R1/R2 tiers map natively to `tools_to_execute` vs `tools_to_auto_execute` + per-agent Virtual Keys. Write-up: `roadmap/bifrost-mcp-spike.md`. Filed T4 (MCP client/VK config) + T5 (one-tool proof).
- Ran **T4 (#30)** design: **no blocker (corrected)** — Bifrost supports private/loopback MCP clients with auth off via **`config.json`** (the API guard only fires on the auth-bypassed HTTP path). Deploy **`grafana/mcp-grafana`** read-only (covers Loki+Grafana+Influx in one container); register via git-tracked config.json with `aios-noc` VK (explicit `mcp_configs`, no `version: 1`). Enabling dashboard auth = optional hardening (T6), downgraded. Design: `roadmap/t4-noc-mcp-design.md`.
- Flagged hardening: `enforce_auth_on_inference` is **OFF**; Agent Mode has no streaming; prefer HTTP/SSE MCP servers.
- Stood up **GitHub Issues as the work queue** on `ChadEngel/ce-aios`: 31 issues, label taxonomy (area/prio/tier/status/type), 6 milestones.
- Added `roadmap/manager-agent.md`; decided manager **Phase 0 = in-session `gh` protocol** (no runtime needed), Phase 1 blocked on the tool plane.
- Answered "do I need something to run these agents?": yes — the **tool/execution plane** is missing (mcpo blocked on unpublished image; test Bifrost native MCP first). Logged T1–T3.
- InfluxDB migration corrected to **DEFERRED / not a blocker** (earlier DONE note was wrong).
- **Where we left off:** #14 spike passed (Bifrost IS the tool plane). **T4 (#30) designed and NOT blocked** — register `grafana-noc` via git-tracked `config.json` (private-IP API path 403s without auth; config.json path does not). Next: deploy `mcp-grafana` read-only → `grafana-noc` client + `aios-noc` VK → T5 one-LogQL proof. T2 runtime-placement still open. Grafana password deferred to end (#4 closed).

## 2026-10-04 (session 2)
- Re-scanned ce-ai-lab: Influx migration still not cut over; four placeholder credentials still live; Tailscale HA, UniFi DR, syslog receiver shipped. Updated context.md.
- Clarified topology: chad-studio-home = client; aiserver.home = aibeasts-mac-studio (Ollama only, 192.168.30.10). Withdrew k3s-on-Studio idea.
- Evaluated ownjarvisai.com → rejected (SaaS/funnel; wrong trust model).
- Defined vision: AI IT team with orchestrator + agents; sequencing agreed.
- Chad connected home-network-config and home-assistant-config; read all four repos. Found HA at 192.168.250.168 (out of scope → exception pending), prior MCP prototype, unredacted-looking psk/iapp keys in network exports, unconfirmed HA secret rotations, allow-all inter-VLAN firewall.
- Chad: M365 shop (no Google); can create app registrations. Later: tenant is personal `engelmn.com`, M365 Business Premium + Teams + "Azure Plan 2" (TBD).
- Built this knowledge base in `jarvis/aios/` (README, operating-rules, context, environment/*, roadmap/*, decisions, session-log).
- Added personal-agent roster (scheduler, travel, financial) in `roadmap/personal-agents.md`; same platform/guardrails as the IT team. Recommended scheduler bundles with helpdesk Graph work; travel/finance parked until Level 2 closes. 5 scoping questions added.
- **InfluxDB migration: NOT cut over, deferred** (Chad, 2026-10-04). It is working as-is and is **not a blocker** — removed from the open-priority framing. Corrected the earlier "DONE" note I wrote from a stale repo date. Level 2 remains = secrets consolidation only.
- **Where we left off:** waiting on answers to the 4 questions in `roadmap/open-items.md`; recommended next action = S1 (rotate Grafana admin password) then build NOC agent R0.

## 2026-08-16/17 (session 1)
- Level 1 discovery; read jarvis + ce-ai-lab; named Level 2 target; created `aios/context.md` and README; researched kids-AI market and Claude-vs-local models.
