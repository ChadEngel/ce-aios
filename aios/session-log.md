# Session log

## 2026-10-11 (session 7) — Phase 0.5: aios-manager GitHub App end-to-end verified
- **Chad:** "you can ssh to the util server so you can do all 3" — verified Phase 0.5 + write scope + shipped two clean PRs.
- **Phase 0.5 (developer-machine auth, #2 part 1 satisfied for the dev side):** ran `aios/manager/test_github_app.py` against the aios-manager App (id **5218173**, install **168700840**, repo `ChadEngel/ce-aios`, perms `[issues, metadata]`) using the `jarvis-jatg` Infisical project's machine-identity access token. Full chain: RS256 JWT (PyJWT 2.x + `cryptography`) → `GET /app` 200 → `GET /app/installations/168700840` 200 → `POST /app/installations/168700840/access_tokens` 200 → `GET /repos/.../issues` 200 (25 open issues listed).
- **Write smoke test:** posted a comment to issue #2 (id `6110728919`, 201) → GET-back 200 (body matches) → DELETE 204 → GET-after-delete 404 → re-listed comments on #2 → 0 from `aios-manager[bot]`. Zero trace left; GitHub App audit log has the round trip.
- **Two bugs fixed and shipped (PR #41, `fix/test-github-app-pyjwt-2-and-token-endpoint`):**
  1. `make_jwt()`: PyJWT ≥ 2.0 requires `iss` to be a string per RFC 7519. Cast `int(APP_ID)` → `str(int(APP_ID))`.
  2. `get_installation_token()`: wrong endpoint — `/installations/{id}/tokens` 404s; correct is `/access_tokens` (per the install object's own `access_tokens_url`). Updated the script + the docstring; verified.
  3. (bonus) Added `aios/manager/requirements.txt` pinning `PyJWT[crypto]>=2.8.0` and `requests>=2.32.0`. The `[crypto]` extra was a silent runtime miss; documented so the next run doesn't waste 5 min on `NotImplementedError: Algorithm 'RS256'`.
- **Traps hit (worth logging for future sessions):**
  1. **PyJWT 2.x `iss` strictness** — silent in PyJWT 1.x, hard fail in 2.x. The `int(APP_ID)` pattern is everywhere in GitHub App examples; needs `str(...)` now.
  2. **`/tokens` vs `/access_tokens`** — the install object literally tells you the right URL in its `access_tokens_url` field; the docs are also clear but the wrong path is the "obvious" one.
  3. **`infisical run` does NOT auto-detect `INFISICAL_TOKEN` env var** — you must pass `--token "$INFISICAL_TOKEN"` (or the CLI will fall through to the interactive login flow, which fails in any non-TTY session). The `infisical-config.json` only sets the domain.
  4. **`infisical run` strips its own auth env vars** in the child process (so the inner script can't read `INFISICAL_TOKEN` anymore) — fine, just don't try to use it as a passthrough.
  5. **`infisical secrets list` doesn't exist** as a flat command in CLI 0.43.x. Use `infisical run -- sh -c 'env | awk -F= "/^[A-Z_][A-Z0-9_]*=/ {print \$1}" | sort'` to discover secret NAMES without printing values.
  6. **Inline `python3 -c "..."` with apostrophes** — SyntaxError on escaped quotes inside the bash command line. The session trap log already warned about `gh issue comment --body "…\`code\`…"`; same hazard here. Fix: write body to a file, read it (`-F body=@file` / Python `open(...).read()`).
  7. **`repos count: 0` on the install object is misleading** — the install's `repositories` field is paginated/embedded-empty by default; the install can still access the repos it was selected for (proven by 25 issues listed). Not a bug, just confusing.
- **Setup pattern that worked (reusable):** `~/.ai-runner/<project>.env` (`chmod 600`, parent dir `chmod 700`) holds `INFISICAL_TOKEN` + `INFISICAL_PROJECT_ID`; `source` it in the same `bash` call as the `infisical run --token "$INFISICAL_TOKEN" --projectId "$INFISICAL_PROJECT_ID"` invocation. Token never enters chat or any committed file; `infisical run` audit-logs the secret read.
- **PRs opened (rule 6, branch-only workflow):**
  - **#41** `fix/test-github-app-pyjwt-2-and-token-endpoint` → `main` (test script + requirements.txt)
  - **#42** `docs/session-7-github-app-verified` → `main` (this entry + small context.md note)
- **Where we left off:** Phase 0.5 done. Phase 1 in-cluster deploy is now the real next step (still per #2). Remaining blockers / inputs for Phase 1:
  - `infisical-universal-auth` Secret in `ns ai` (for the `InfisicalSecret` operator to sync from) — not yet verified to exist; would check next.
  - NetworkPolicy egress lockdown: `aios/manager/k8s/networkpolicy.yaml` is `0.0.0.0/0:443,80` with a `# TODO: restrict`. Should be `api.github.com` + `secrets.caehomelab.com` + `llm.caehomelab.com` (and DNS at `10.43.0.10`) before any R1/R2.
  - The real `aios-manager` Python service (currently a `time.sleep(86400)` placeholder in `deployment.yaml`).
  - The same two script bugs are on `origin/feat/aios-manager-skeleton` — that branch needs the same fixes before it's rebased/merged; tracked as a follow-up to PR #41.

## 2026-10-11 (session 8) — in-cluster deploy blockers resolved (PR #44)
- **Chad said:** "I want them in Jarvis since this is for the AI agents." — the GitHub App credentials belong in the AIOS project (`jarvis-jatg`), not the lab project (`caehomelab-v1q6`). Boundary rule: agent creds in agent project, infrastructure creds in infrastructure project. (This is the same pattern as `decisions.md` "Tenant confirmed: personal `engelmn.com`".)
- **Found 3 real config gaps blocking Phase 1 in-cluster deploy of #2 (now tracked as #43):**
  1. **Operator template can't parse hyphens in path segments** — `{{ .aios-manager/GITHUB_APP_ID.Value }}` errors with `bad character U+002D '-'`. Confirmed from operator logs: `ReconcileInfisicalSecret: Fetched secrets via machine identity ... ERROR failed to create managed secret [err=unable to compile template: GITHUB_APP_ID [err=template: secret-templates:1: bad character U+002D '-']]`. The secrets in `caehomelab-v1q6` are at `aios-manager/` (folder with hyphens) and the operator's Go-template-based template language rejects those hyphens. Fix: put secrets at the root path with hyphen-free names.
  2. **Source-project scope was wrong** — issue #2 design comment says creds go in `caehomelab-v1q6`, but they're actually in `jarvis-jatg`. Universal-auth credentials are per-project, so the existing `infisical-universal-auth` K8s Secret in `ns ai` (used by 8 other `InfisicalSecret` CRs) only has access to `caehomelab-v1q6`. Fix: separate K8s `Secret` (`jarvis-jatg-universal-auth`) with a universal-auth credential generated from the `jarvis-jatg` machine identity. Requires UI step (CLI doesn't have a subcommand for machine identities / universal auth).
  3. **Real `aios-manager` service code** — `deployment.yaml` was running `time.sleep(86400)`. Fix: new `aios_manager.py` (165 LOC) + `Dockerfile` + updated `deployment.yaml`. Polls every 15 min, lists open issues, logs structured JSON to stdout (Loki-ready), graceful-shutdown on SIGTERM, env-var preflight, transient-error backoff.
- **Bonus fix (not in #43 but found while in there):** skeleton's `networkpolicy.yaml` had `0.0.0.0/0:443,80` egress — real R0 risk (private-key exfil vector). Replaced with: DNS to k3s CoreDNS + GitHub's published `api` IP ranges (fetched from `https://api.github.com/meta`, IPv4 entries). Default-deny ingress. The GitHub IP ranges are pinned at writing time; TODO to re-fetch quarterly OR migrate to Cilium FQDN selector when the lab adopts Cilium (not currently installed — checked).
- **Local end-to-end verify:** `infisical run` from a developer machine, injecting the same 3 secrets the in-cluster `Secret` will have. New service code's `poll_once()` returned 26 open issues in 919ms. Startup-fail path also tested (missing `GITHUB_APP_ID` → exit 2, `startup_failed` event logged). End-to-end Python smoke: `python3 -c "import ast; ast.parse(open('aios_manager.py').read())"` passes; module imports cleanly with `INSTALL_ID=168700840`, `REPO='ChadEngel/ce-aios'`.
- **PR #44 opened:** `fix/aios-manager-deploy-blockers` (8 new files, 617 insertions, 0 deletions). Branches don't touch `main` per rule 6.
- **Issue housekeeping:** #43 relabeled `status:blocked` → `status:review`; status comment posted. Issue #2 got a follow-up comment pointing at #44.
- **What's still needed for in-cluster apply (one-time, ~10 min, can be done by either of us):**
  1. Infisical UI → `jarvis-jatg` machine identity → Universal Auth tab → add config (project=`jarvis-jatg`, env=`prod`, role=read-only) → copy `clientId`/`clientSecret`.
  2. `kubectl -n ai create secret generic jarvis-jatg-universal-auth --type=Opaque --from-literal=clientId=... --from-literal=clientSecret=...`
  3. `docker build -t caehomelab/aios-manager:0.1.0 aios/manager/ && docker save ... | ssh util-server k3s ctr image import -`
  4. `kubectl apply -k aios/manager/k8s/` (from the lab repo `ce-ai-home-lab` per the repo map, or directly with `--dry-run=client` first)
  5. Verify: `kubectl -n ai get secret aios-manager-secrets` populated; pod `Running`; `poll_ok` events every 15 min in Loki.
- **Traps hit (worth logging for future sessions):**
  1. **The infisical-operator's template parser is NOT a Jinja2-compatible Go-template** — it's a custom thing based on text/template but with strict identifier rules. Hyphens in path segments fail. Plain identifier `.FIELD.Value` works. The Infisical docs do mention this in a footnote; not obvious from the operator's spec page.
  2. **Universal-Auth credentials are per-project and per-identity** — they have a `clientId` (UUID) and a `clientSecret` (string), and they're bound to (project, env, role) at generation time. The existing `infisical-universal-auth` K8s Secret in `ns ai` is bound to `caehomelab-v1q6` and cannot be reused for `jarvis-jatg` — even though the human user can read both projects, the machine identity's universal-auth client cannot.
  3. **GitHub IP ranges for the `api` are published at `https://api.github.com/meta`** — they include several `/32` single-host entries plus a few `/20`-`/22` aggregates. Re-fetch on a schedule (or migrate to a Cilium FQDN selector if/when the lab adopts Cilium).
  4. **Cilium is NOT installed in the lab** (verified: `kubectl get ciliumnetworkpolicy` → "the server doesn't have an resource type cil..."). Plain `networking.k8s.io/v1` `NetworkPolicy` is the only network policy primitive. ipBlock-based egress is the only option.
  5. **`infisical identities` is not a CLI subcommand** in CLI 0.43.110 (checked `--help`). Machine identities and universal-auth credentials are UI-only operations.
  6. **Reusable: `polling a GitHub App's installation is best done as a long-running service, not a cron job** — the install token is 1-hour TTL and re-minting requires a fresh JWT, which is fast (~5ms) but easier as a single long-lived process than a per-poll Kubernetes Job.

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

## Session 6, cont. 5 (2026-10-07) — T2 DECIDED: in-cluster, #15 closed
- **Decision (decisions.md 2026-10-07 entry):** background agent loop runs **in-cluster** (`aios-manager`, ns `ai`). Studio keeps Ollama native/Metal (inference provider) + interactive pi sessions only. Rationale: one secret path (operator sync proven ×9), one observability path (Loki), one network policy, always-on host; util-server measured 6% CPU / 1.9Gi of 4.9Gi used. Supersedes Feb Compose-on-Studio design (predates spike #14's scope shrink).
- **#15 closed** with rationale + Phase-1 unblock note. **#2 (Manager Phase 1) now unblocked** — remaining human step: GitHub App (Issues R/W) install + private key into Infisical.
- **Tooling trap:** `gh issue comment --body "…\`code\`…"` inside double quotes — escaped backticks still trigger command substitution in some quoting chains (first attempt substituted `aios-manager` → empty, and `\n` stayed literal). Fixed via `gh api PATCH …/issues/comments/<id> -F body=@/tmp/file.md`. Rule: **always post bodies from files** (`gh api -F body=@file`), never inline.

## Session 6, cont. 6 (2026-10-07) — handoff: everything clean, safe to quit
**Today's full ledger (all merged, all verified):**
1. **#33 closed** — Bifrost `v2.2.6@sha256:d0b4708…` pinned (+IfNotPresent; Hub tag needs the `v`) and unauthed `/mcp` bypass CLOSED via `client_config.enforce_auth_on_inference=true` (setup-token `PUT /api/config`, DB-backed, survives restart; applied live with zero breakage). Verified matrix: unauthed /mcp + /v1 → 401; `aios-noc` VK → 200/20 tools on `/mcp`; `ce-key` (Open WebUI) → 200 `/v1` + 403 `/mcp`; pi's `ce-pi-macbook` VK unaffected. Lab `daa7036`.
2. **Merged PR #9 → `main`** (Headlamp + T4 tool plane + hardening, 10 commits); lab and ce-aios now **`main`-only** — ALL branches deleted incl. `chore/pre-udm-swap-2026-09-27` (Chad's verdict, 12 commits; recovery SHA `14b3979bd5ba375335317b524b3d4166b959a07e`).
3. **Hard rule 6** (operating-rules.md): never commit/push to `main` on origin — branch → PR → merge. Dogfooded across every change since (ce-aios PRs #35/#37/#38/#41, lab PRs #9/#10).
4. **#34 closed** — grafana memory 512Mi req / 1536Mi lim (2Gi rejected: node allocatable ~4.8Gi); applied live, 2/2 running, 8 dashboards. Lab PR #10 → `8a22654`.
5. **N5 closed** — orphan `GRAFANA_API_TOKEN` deleted from Infisical by Chad (UI). Confirmação-based, not proof-based: machine cannot verify (REST 404 for identities, CLI unauthed); cluster side verified zero-risk (never synced, zero refs, operator clean).
6. **T2 DECIDED + #15 closed** — background agent loop runs **in-cluster** (`aios-manager`, ns `ai`); Studio = Ollama provider + interactive only. `decisions.md` 2026-10-07 entry. #2 (Phase 1) UNBLOCKED: Chad = GitHub App (Issues R/W, no webhook) + private key into Infisical; assistant = build `aios-manager` skeleton (python image, Deployment/InfisicalSecret/NetworkPolicy, VK `aios-manager`, Loki).

**Where a new session picks up (verified 2026-10-07):** open issues are ALL waiting-on-Chad or human-executed — priority order: **#2** Manager Phase 1 (Chad: GitHub App + key; assistant: skeleton build — the only item needing a green light), **#32** T6 dashboard auth (human), secrets chain #9/#8/#7/#11/#12 (human-executed rotations, #10 runbook exists), **#20** HA firewall exception, **#21** Entra registration, **#10→#22** docs path drift (assistant-doable, low), #17/#19 = H1/H3 (NOC hire R0 effectively shipped via Phase-0 protocol; orchestrator bake-off still pending). On Chad's "go" after App install: build `aios-manager` skeleton in the LAB repo on a branch (rule 6). Manager Phase 0 ("run the manager" in-session `gh`) remains available anytime.

**New traps logged today (for future sessions):**
- Hub tags carry the `v` (`v2.2.6` exists, `2.2.6` does not); pin tag+LIST-digest (per-arch digests differ from pod imageID).
- `kubectl apply -f kustomization.yaml` (multi-doc manifest) — `-k` fails: `unknown field "spec"`.
- `/api` GET on unknown routes returns **200 + HTML** (SPA catch-all) — verify by content type/body, never status alone.
- `gh issue comment --body "…\`code\`…"` inline mangles backticks + literal `\n`; ALWAYS post bodies from files (`gh api -F body=@file` / `--body-file`).
- `git branch --merged|--no-merged origin/main` covers LOCAL branches only; verify remote merge-state with `-r` or `rev-list --count origin/main..origin/<branch>`.
- `client` section in config.json + `source_of_truth: split` = file force-wins on boot (enforcement flag can be reverted by accident — CM header documents it).
- Reusable: `/tmp/bifrost-src` = full v2.2.6 source clone (public repo); `/tmp/bf` = earlier sparse extract; `/tmp/t5probe.sh` clean (no embedded VK); aios-noc VK material recovers from `governance_virtual_keys` in a fresh config.db copy.

## Session 9 (2026-10-16) — helpdesk mail spike PASS; muse-glimmer verified; Bifrost bootstrap unblocked
1. **Merged:** #41, #42, #44, #45 (drift reconcile), #46 (mail design). Opened #47 (spike verdict + decisions).
2. **T-M1 spike PASS** — Softeria/ms-365-mcp-server adopted in HTTP daemon mode (`--http --trust-proxy-auth --read-only`; NO app-only auth exists in it). Entra registration/cert/RBAC chain dissolved for R0 → #21 commented, `waiting-on-chad` → `in-progress`; registration deferred as optional R2 hardening. Infisical shrinks to one secret (`MS365_MCP_SECRET_KEY`, jarvis-jatg).
3. **Chad decisions recorded** (`decisions.md`): mailbox = primary inbox now, "start slow" (daily digest only, read-only); delegated identity accepted with R2 re-evaluation gate; backfilled 2026-10-11 jarvis-jatg boundary entry (agent creds vs infra creds).
4. **muse-glimmer verified** (Chad's routing pick for the digest): lives on `aiserver.home:11434` = aibeasts-mac-studio M3 Ultra 96GB @ 192.168.30.10 — the INFERENCE Studio, distinct from desk `chad-studio.home` (client). It is a **reasoning model**: without `think:false` the eval budget burns on hidden `thinking` → empty visible content. With `think:false`: correct 2-of-5 triage, 300 tok ≈ 9.7 s. Pinned as the digest default model on the future `aios-helpdesk` VK; DEPLOY-TIME VERIFY: `think:false` pass-through via Bifrost's ollama provider.
5. **Bifrost setup-token bootstrap unblocked** — dashboard's token prompt is T6's human half (#32): admin creation while `BIFROST_SETUP_TOKEN` gates the management `/api`. Extracted from `bifrost-secrets` (b64→plain) → `~/.ai-runner/bifrost-setup-token` (chmod 600, 64-hex verified, never logged).
6. **SSH recipe confirmed working:** `ssh util-server` (alias, user `cengel` @ 192.168.30.217) — manual `chad@IP` attempts fail; always use the alias.

**New traps (session 9):**
- Reasoning-capable Ollama models: empty `message.content` ≠ broken model — check `message.thinking` length and burn rate (`eval_count`) before verdicts; set `think:false` for R0-class tasks.
- Bifrost provider/model registry lives in **config.db** (split source-of-truth) — the git-tracked `bifrost-config` CM carries only `$schema`/`mcp`/`governance`; read live state via setup-token `/api`, not the CM.
- When probing hosts by name, resolve FIRST (`dscacheutil -q host`): `aiserver.home` (.30.10) and `chad-studio.home` are different Mac Studios — assuming "Ollama is local here" from context alone burned time.

**Session 9 addendum (same day) — muse-glimmer verified end-to-end through Bifrost:**
- `ollama/muse-glimmer:latest` confirmed registered (VK-gated `/v1/models`, 11 models; Chad saw it in the dashboard independently). VK-gated list used `BIFROST_API_KEY` from `bifrost-secrets` → `~/.ai-runner/bifrost-ce-vk` (600, the `ce-key`/Open WebUI VK — read-only GET usage only).
- **DEPLOY-TIME VERIFY resolved:** Bifrost v2.2.6 maps `reasoning_effort` → ollama think-level on the ollama provider. `\"none\"` = 0 reasoning chars, 165 completion tok, correct triage (plain = 766 tok incl. 2957ch reasoning); `\"low\"` ≈ 868ch reasoning remains. Prompt-level `/no_think` suffix NOT honored by the model's template. Digest spec pins `reasoning_effort: \"none\"`.

## Session 9, cont. 2 (2026-10-16 PM) — mail agent DEPLOYED; T-M2 PASS; aios-helpdesk VK live

**Deployed (ce-aios PR #48, lab PRs #11 + #12):** `aios-mail-mcp` running in ns `ai` on caelx003 (Softeria 0.168.0, `--http :3000 --trust-proxy-auth --read-only`), 64Mi cache PVC, ingress restricted to `app=bifrost` (verified: unlabeled probe pod denied), 443-egress with RFC1918 except. Bifrost `graph` MCP client live: 75 read-only tools, namespaced `graph-<tool>`.

**T-M2 PASSED (via llm.caehomelab.com/mcp/graph + aios-helpdesk VK):** initialize 200 → `graph-get-my-profile` returns real Graph identity → `graph-list-mail-messages` returns live inbox items. VK key survived file declaration (value-less block, aios-noc precedent). Model scope on `/v1/models`: only `ollama/muse-glimmer:latest`.

**New traps (burned this session):**
- **Mixed-arch k3s cluster** (caelx002/caelx003 = amd64, util-server = arm64): `docker save | k3s ctr import` is NODE-LOCAL. Studio builds are arm64; the pod schedules on amd64 workers → ErrImageNeverPull until an amd64 buildx build is imported on the pod's node (`ssh cengel@<node>` + `sudo -n k3s ctr -n k8s.io images import -`; passwordless sudo works on all three).
- **Lab repo merge ≠ apply**: lab manifests are applied by hand (`cat .../kustomization.yaml | ssh util-server kubectl apply -f -` — the file is raw YAML despite its name, NOT a kustomization). Bifrost subPath config also needs `rollout restart`. PR #11 existed 30+ min before its config reached the cluster.
- **Login must carry the daemon's flags**: plain `--login` caches a FULL-scope MSAL grant; the `--read-only` daemon then fails silent refresh (~1h, at access-token expiry) with "Failed to acquire token". Scope match or death; verify via a real tool call through Bifrost, never a second `--verify-login` process (extra RT consumer). Symptom class documented upstream (#522). README step 4 amended.
- **File-declared Bifrost MCP clients are in-memory only** (never persisted to `config_mcp_clients`): DB-side VK grants cannot reference them — grants must live in the file governance section (aios-noc pattern). PR #12 fixes the grant path PR #11 assumed was dashboard-side.
- **Bifrost namespaces MCP tools `<client>.<tool>`** over raw MCP: callers (digest job) must use `graph-list-mail-messages` style names.
- `kubectl kustomize` rejects the lab bifrost "kustomization.yaml" (raw multi-doc YAML) — apply with `-f`, not `-k`.

**Watch item added:** MSAL silent-refresh health — expect steady refresh now that scopes match; if the ~1h death pattern returns, check token-cache mtimes + `--verify-login --read-only` differential before assuming upstream regression.

**Open:** digest CronJob (next — needs VK already in Infisical ✓, issue thread, muse-glimmer reasoning_effort none); `--enabled-tools` R0 mail subset tightening; aios-manager apply checklist (universal-auth Secret now EXISTS in ns `ai` ✓).
