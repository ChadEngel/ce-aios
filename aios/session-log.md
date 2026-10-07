# Session log

## 2026-10-06 (session 6) — Grafana: Mac System Monitor keeps disappearing
- **Restored + root-caused.** `mac-system-monitor` (18 panels) was absent from live Grafana. Forensics in the Postgres unified-storage history (`resource_history`, table `dashboards/…` — Grafana 13 no longer uses the legacy `dashboard` table, which is why it reads 0): created 2026-09-19 04:37 (the `5174a68` restore), **deleted 2026-09-27 14:08 = exactly the grafana 2-replica HA cutover**, never re-added after that (10/03 OOMKills didn't matter — state is Postgres-backed).
- **Mechanism:** dashboards are file-provisioned from ConfigMap `grafana-dashboards-json` → mounted at `/var/lib/grafana/dashboards/default`; provider `updateIntervalSeconds: 30` **deletes any provisioned dashboard whose file disappears from the mount**. The 9/27 cutover rebuilt that ConfigMap from a stale checkout that predates `5174a68` (which added `mac-system-monitor.json`) — file gone → dashboard deleted. Recurs every time the CM is rebuilt without the key.
- **Fix applied:** ran current additive `deploy-grafana.sh` → CM now has 7 repo dashboards + preserved `udm-syslog.json` orphan (script warns: commit to git). Verified: API serves 8 dashboards, unified storage has `dashboards/mac-system-monitor`, no pod churn.
- **Why it stays put:** JSON is git-tracked on origin/main; script default is additive/orphan-preserving (deletes only with `--prune`). Residual risks: rebuilding the CM by hand or from a stale checkout, and `udm-syslog.json` existing only in the cluster + script's carry-forward. Flagged optional: both grafana pods OOMKilled 10/03 (exit 137; limit 768Mi) — harmless to state now, but churn. Filed as **#34**.
- Issue sync: closed #18 (H2) + #16 (T3) as delivered by T4/T5 with pointer comments; comment on #2 (tool-plane blocker resolved, Phase 1 now waits only on #15). Lab repo committed + pushed to `origin/headlamp-addition` (4 commits: `952b21d` T4 artifacts, `5b12b38` user's PVC migration, `576e269` grafana_noc rename, `4a8c067` udm-syslog tracking).

## 2026-10-06 (session 6, cont.) — #33 decision A: BIFROST_SETUP_TOKEN applied
- Chad picked **Option A** (setup_token over dashboard auth). Key source finding: Bifrost ≥ 2.2.6 `resolveSetupToken()` reads the `BIFROST_SETUP_TOKEN` env var directly — the design doc's `"setup_token": "env.BIFROST_SETUP_TOKEN"` config.json route works too but is unnecessary; env-only = zero secret-adjacent keys in git.
- Infisical write done by Chad via UI (`human-executed`, identity-token REST write routes 404 on this build; CLI session was logged out). Token generated (`openssl rand -hex 32`), delivered via clipboard (`pbcopy`), never printed in chat. Operator synced into `bifrost-secrets` (resync 60s) → deployment `secretKeyRef` env → rollout.
- **Correction (mine):** first edit added the explanatory comment but omitted the actual secretKeyRef env entry — `kubectl apply` rightly said `unchanged`; caught on live-spec inspection, fixed, re-applied (`5db0fa0`). Verified: `/api/version` 200; `/api/governance/virtual-keys` 401 no-header (was 403) / **200 with `X-Bifrost-Setup-Token`** / 403 wrong-token; `/v1/*` unaffected. Local plaintext copies deleted; token recoverable from Infisical.
- #33: A done (comment recorded); still open = image pin + unauthed `/mcp/*` closure. Header unlocks all of `/api` — bootstrap-grade; T6/#32 remains the end state.

## 2026-10-06 (session 5)
- **Chad said "go" → executed T4/T5 Step 1–2 live.** Infisical sync, mcp-grafana deploy, Bifrost ConfigMap + restart all applied. Full state handoff: `roadmap/t4-t5-execution-state.md` (authoritative).
- **Bug 1 (mine, fixed): MCP client names forbid hyphens** — `grafana-noc` rejected at startup, so the VK's `mcp_configs` also failed to resolve. Renamed to `grafana_noc` everywhere; re-applied → registered, 20 tools discovered. VK names still allow hyphens.
- **Bug 2 (upstream, open): Bifrost `latest`+Always pulled v2.2.6 (released that day) which adds an OSS `/api` setup lock** — all management routes 403 while dashboard auth is off and no `setup_token` exists. Inference + `/mcp/*` unaffected. Options A (setup_token from Infisical) vs B (enable dashboard auth = T6). Awaiting Chad.
- **Merge safety proven at the DB layer:** `ce-key` + `ce-pi-macbook` VKs unchanged, `aios-noc` auto-generated. Verified via sqlite on a copied config.db (`/api` was locked).
- **T5 half-proven:** `/mcp/grafana-noc` initialize → 200; `tools/list` → 20 read-only-tiered tools. Actual tool **call** still pending.
- **Next:** T5 tool call → close #31/#30; Chad decides setup_token + image pinning (latest+Always caused silent v2.2.3→v2.2.6 drift); then Chad's unrelated Grafana task.
- **T5 PASSED + #30/#31 closed (2026-10-06).** VK-authed external chain (Mac → llm.caehomelab.com → bifrost → mcp-grafana → Grafana): `list_datasources` → recorded UIDs; `list_loki_label_names` → real Loki labels. UIDs matched design doc exactly.
- **New finding:** the `/mcp/*` gateway itself accepts **unauthenticated** tool calls (unauthed `tools/list`/`tools/call` → 200). Bounded today (LAN-only DNS A record, read-only tool set) but bypasses VK scoping — candidate fixes: dashboard auth / ingress middleware / cluster-internal-only. Consolidated with the setup-lock + pinning decisions into **#33** (`status:waiting-on-chad`). Handoff state updated in `t4-t5-execution-state.md`.

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

## Session 6, cont. 2 (2026-10-07) — #33 "do it": image pin + /mcp closure → CLOSED
- **Key facts established (source-verified at tag `transports/v2.2.6`, `/tmp/bifrost-src` shallow clone):**
  - Hub tag is `v2.2.6` (with the v) — `2.2.6` does not exist; tag's LIST digest == the running imageID `d0b4708…` (per-arch digests differ; pin the list digest).
  - `enforce_auth_on_inference` lives in ClientConfig; `mcp_server_auth_mode: headers` (current) = default that accepts anonymous MCP callers; no upstream MCP-only caller-auth knob exists.
  - config-vs-DB semantics (`loadClientConfig`): with `source_of_truth: split`, a `client` section present in config.json **force-wins** over DB (`forceClientSync`); an explicit `enforce_auth_on_inference` in the file always wins; DB value persists across restarts while the file has no `client` section. API edits don't bump ConfigHash.
  - `PUT /api/config` round-trips `client_config` built on the live struct with field-explicit merge via `HasInferenceAuthSetting()`; `EnforceGovernanceHeader`/`EnforceSCIMAuth` are synced aliases.
- **Identity resolution:** `BIFROST_API_KEY` = **ce-key** VK; pi Studio default provider key = **ce-pi-macbook** VK; `aios-noc` VK (T5) only in config.db (plaintext) — extracted once from DB copy for testing, temp file deleted. VK↔MCP assignment layer (`governance_virtual_key_mcp_configs`) explains 403s for non-assigned VKs (correct denial, not breakage).
- **Executed:** `PUT /api/config` flip (live, no restart) → image pin in kustomization (tag+digest, IfNotPresent) + CM-header documentation of the no-`client`-section caveat → `kubectl apply -f` (this kustomization.yaml is a multi-doc manifest: `-k` fails with "unknown field spec") → rollout.
- **Verified post-restart:** unauthed /mcp → **401** (was 200 bypass), aios-noc → 200/20 tools, ce-key → 200 /v1 (Open WebUI intact, zero 401s in its logs) + 403 /mcp, unauthed /v1 → 401, /health + /api/version → 200, DB flag = 1. T6's enforce half done as part of this; #32 remains for dashboard auth only (scope-update comment posted).
- **Records:** lab `daa7036` pushed; #33 closed with matrix comment; open-items N2/N6 resolved, N1/N2/N6 consolidation note → #33 CLOSED. Traps hit: SPA catch-all returns 200+HTML for bogus `/api` GETs (probe bodies, not status); `gh issue comment -q .url` prints the URL — don't use for verification (use `gh issue view --json comments`).

## Session 6, cont. 3 (2026-10-07) — git hygiene rule + #34 closed
- **Hard rule 6 added to operating-rules.md:** never commit/push directly to `main` on origin in any repo — branch (`feat/…/fix/…/docs/…/chore/…`) → push → PR → merge via PR; merge commits are the only direct appearances on main. Applies to Chad and all agents. First two dogfood runs: `docs/git-branch-rule` (PR #35, rule itself) and `fix/grafana-oom-limit` (lab PR #10). Not yet hard-enforced (GitHub branch protection needs a paid plan on private repos).
- **Branch cleanup (Chad-initiated, assistant-verified):** his deletion hadn't fully landed — fetch --prune showed `headlamp-addition` + 2 feat branches still on origin. Deleted the three VERIFIED-merged ones (local + remote). KEPT `origin/chore/pre-udm-swap-2026-09-27`: 12 commits not on main (pre-UDM-swap work) — needs Chad's verdict (dead → delete; alive → rebase onto main someday).
- **#34 DONE (lab PR #10, merged `8a22654`):** grafana req 256Mi→512Mi, lim 768Mi→**1536Mi** (below the 2Gi bound — node allocatable ~4.8Gi; steady 250–300Mi, spikes caused both OOMKills). Applied live after merge, rollout clean: 2/2 running, 0 restarts, 8 dashboards intact, #34 closed.
- **Traps:** `git branch --merged/--no-merged origin/main` defaults to LOCAL branches only — remote-branch merge-state needs `git branch -r --merged` or per-branch rev-list counting.

## Session 6, cont. 4 (2026-10-07) — N5 closed, chore branch verdict executed
- **N5 DONE:** Chad deleted orphan `GRAFANA_API_TOKEN` (July-era) from Infisical project `caehomelab-v1q6`, env `prod`, via UI. Assistant could not machine-verify: CLI has zero credentials (unauthed) and all secret-read REST routes 404 for identity tokens on this Infisical build — recorded as confirmation-based, not proof-based. Cluster-side: deletion provably safe (secret never synced into any InfisicalSecret CR, zero references in the lab repo incl. grafana manifests; operator logs clean post-deletion; grafana serving 8 dashboards).
- **`chore/pre-udm-swap-2026-09-27` deleted** on Chad's verdict — 12 unmerged commits. Recovery SHA recorded in chat: `14b3979bd5ba375335317b524b3d4166b959a07e` (GitHub keeps dangling objects until GC).
- Both repos now `main`-only. Remaining queue is all waiting-on-Chad: #15 (T2 runtime), #20/#21 (HA), #9 (rotations), #32 (T6 dashboard auth, human-executed).
