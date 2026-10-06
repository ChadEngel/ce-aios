# T4/T5 execution state — live handoff (2026-10-06)

**Read this file to resume.** It is the authoritative record of what was executed
against the live cluster this session, what is verified, and what decisions are
pending from Chad. Runbook = `t4-t5-runbook.md`; design = `t4-noc-mcp-design.md`.

## Where execution stands

| Runbook step | Status |
|---|---|
| 1 — apply (Infisical sync → mcp-grafana → bifrost ConfigMap + restart) | **DONE, live** |
| 2 — verify registration (clients + VKs) | **DONE** (via sqlite, not `/api` — see Bug 2) |
| 3 — T5 proof (one read-only tool call end-to-end) | **HALF DONE** — `initialize` + `tools/list` proven; **tool CALL still pending** |
| 4 — record + close #31/#30 | NOT DONE |
| 5 — rollback | not needed; no breakage |

## What is live and verified

- `mcp-grafana` Deployment/Service/NetworkPolicy in ns `ai`, pod Ready, pinned
  `grafana/mcp-grafana:2.0.1`. Startup SECURITY warning (caller auth off on
  :8000) is expected/accepted — NetworkPolicy admits only `app=bifrost`.
- Secret `mcp-grafana-secrets` (ns `ai`) created by operator from Infisical
  `GRAFANA_SERVICE_ACCOUNT_TOKEN` (`mcp-grafana-secrets-sync` InfisicalSecret applied).
- Bifrost: `bifrost-config` ConfigMap → `/app/data/config.json` (subPath). On
  startup: `[Bifrost MCP] Per-user headers verification succeeded for 'grafana_noc':
  discovered 20 tools`.
- **Merge-safety proven:** all three VKs exist in config.db — `ce-key`
  (`sk-bf-1da01b…` unchanged), `ce-pi-macbook` (`sk-bf-1bf8a6…` unchanged),
  `aios-noc` (auto-generated `sk-bf-4ebf05…` — **SECRET, never commit**).
- DB row (verified via sqlite on copied `/tmp/bifrost-data/config.db`):
  `grafana_noc | http | http://mcp-grafana.ai.svc.cluster.local:8000/mcp | none |
  ["*"] | [] | allow_on_all_virtual_keys=0 | endpoint_slug=grafana-noc`.
  VK↔MCP: `aios-noc ↔ grafana_noc, tools ["*"]`. Providers: openrouter+ollama,
  `["*"]`, allow_all_keys=1.
- Bifrost MCP gateway endpoint (NOT under `/api`): `POST /mcp/grafana-noc`
  (slug re-hyphenates the client name) → `initialize` 200, `tools/list` → 20 tools:
  alerting_manage_routing, alerting_rules_read, alerting_silences_read,
  analyze_loki_labels, check_datasources_health, get_dashboard_by_uid,
  get_dashboard_panel_queries, get_dashboard_property, get_datasource,
  list_dashboard_versions, list_datasources, list_loki_label_names,
  list_loki_label_values, query_influxdb, query_loki_logs, query_loki_patterns,
  query_loki_stats, search_dashboards, search_folders — all prefixed
  `grafana_noc-` (client name underscore + `-` separator; hyphens inside tool
  names are handled by Bifrost's tool-name normalization).
- Inference `/v1/*` unaffected throughout (Open WebUI path fine).

## Bug 1 — FIXED: MCP client names cannot contain hyphens

`ValidateMCPClientName` (`core/mcp/utils.go:878-897`): ASCII, no hyphens, no
spaces, can't start with a digit. `grafana-noc` was rejected at startup, which
also made the VK's `mcp_configs` fail to resolve ("failed to resolve MCP client
'grafana-noc': not found"). VK **names** allow hyphens (`ce-key` precedent) —
only client names forbid them.

**Fix applied (working tree of ce-ai-home-lab, not yet committed):** renamed
`grafana-noc` → `grafana_noc` in `bifrost/kustomization.yaml` (ConfigMap),
`mcp-grafana/kustomization.yaml` comment, `mcp-grafana/README.md`,
`scripts/deploy-all.sh`, `scripts/deploy-mcp-grafana.sh`. Grep confirms zero
`grafana-noc` remain in manifests/scripts. Re-applied + restarted → registered.

## Bug 2 — OPEN decision: v2.2.6 "OSS Management API Setup Lock" locks `/api`

Root cause chain: bifrost deploys with `image: maximhq/bifrost:latest` +
`imagePullPolicy: Always` (lab commit `8fe3c26` chose this deliberately); the
rollout restart pulled **v2.2.6, released today 2026-10-06**, which locks every
non-public `/api` route while dashboard auth is inactive (no admin, no
setup_token) — missing token → 401, no token configured → 403. Lock lifts when
an enabled admin is saved; `setup_token` (config.json or `BIFROST_SETUP_TOKEN`)
enables scripted access via `X-Bifrost-Setup-Token` header.

- **Not caused by T4's config.json** — any restart would have hit it. The spike
  (v2.2.3) predates it.
- Blast radius: management `/api/*` only. Inference and `/mcp/*` unaffected.
- Options for Chad:
  - **A (recommended now):** `setup_token` from Infisical → key
    `BIFROST_SETUP_TOKEN`, consumed as `"setup_token": "env.BIFROST_SETUP_TOKEN"`
    in the git-tracked ConfigMap (env injected from a K8s secret via the operator,
    same pattern as Grafana token). Restores scripted `/api` access; keeps auth-off posture.
  - **B:** enable dashboard auth + first admin (needs setup_token anyway to
    bootstrap). This IS T6 (#32) — previously downgraded to optional; upstream
    has now effectively forced it for anyone wanting `/api`.
- Related recommendation: pin the Bifrost image (tag/digest). `latest`+Always
  caused silent v2.2.3→v2.2.6 with a behavior change mid-deploy — contradicts the
  repo's own pinning convention (`8fe3c26` reverted digest-pinning).

## Local probe assets (reusable)

- `/tmp/t5probe.sh` — drives `/mcp/grafana-noc` via `kubectl exec ... wget`
  (busybox quirks: headers as separate `--header='K: V'` args).
- `/tmp/bifrost-data/config.db{,-wal,-shm}` — copied live DB; `sqlite3` is on the
  host, NOT in the container. Tables of interest: `config_mcp_clients`,
  `governance_virtual_keys`, `governance_virtual_key_mcp_configs`,
  `governance_virtual_key_provider_configs`.

## Remaining to finish T4/T5 (in order)

1. **T5 tool call:** call ONE read-only tool end-to-end (suggest
   `grafana_noc-list_loki_label_values` or `list_datasources`) through the
   gateway, show real output. VK-authed path (Bearer the `aios-noc` value) vs
   unauthed gateway — prove the VK path since that's the agent's path.
2. Chad decisions: setup_token (A/B above); image pinning.
3. Comment results on #31, close #31 then #30; flip design doc Finding 3 →
   "applied + proven".
4. Separate explicit yeses still owed: commit ce-ai-home-lab; delete orphan
   `GRAFANA_API_TOKEN` (Q-T4-6).
5. Then pivot to Chad's **unrelated Grafana task** (not yet specified) — he asked
   for it before going further on AIOS.