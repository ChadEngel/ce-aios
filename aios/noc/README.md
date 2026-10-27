# AIOS NOC agent — hire #1 (R0)

The first AI IT-team hire, per `aios/roadmap/ai-it-team.md`. Job:
**"what's broken / what changed?"** — triage alerts, query Loki/Influx, summarize.

Issue: **#17** (output thread during the R0 window).

## Status

**R0 scaffolding built and verified against the live tool plane.** Not yet
scheduled — see *Rollout* below for what is still human-executed.

## What it does

A deterministic read-only sweep, twice daily. **No model is involved** — every
line in the report is a measured value, so the two-week R0 review window is
reviewing observations rather than prose.

| Check | Tool | What it catches |
|---|---|---|
| Datasource health | `check_datasources_health` | Loki/InfluxDB unreachable |
| Log freshness | `query_loki_logs` (backward, limit 1) | a source that stopped shipping |
| Error volume | `query_loki_stats` + `query_loki_logs` | error-ish lines in the window |
| InfluxDB | `query_influxdb` (`buckets()`) | bucket set changed / query broken |

Output: a markdown comment on the NOC issue thread, with an explicit
`All values measured at <UTC>` line and a one-line verdict. Timestamps are
mandatory — a stale report must never read as current state.

## Access

- **Virtual Key:** `aios-noc` — grants the `grafana_noc` MCP client,
  `tools_to_execute: ["*"]`, **`tools_to_auto_execute: []`**.
- **Tier R0.** Nothing auto-executes. There is no write tool on the VK to call:
  `mcp-grafana` runs with `--disable-write` and the tools are read-only.
- **Egress:** `443` anywhere (Bifrost is RFC1918 at 192.168.30.217; GitHub is
  public) + kube-dns. Egress-only.

## Traps pinned by the selftests

These were all found against the live tool plane, and each is pinned so it
cannot silently regress:

1. **`query_loki_stats` accepts ONLY label matchers.** Both aggregations
   (`sum by (job) (...)`) and line filters (`|~ "error"`) are rejected with
   `400 only label matchers are supported`. So the unfiltered count uses
   `stats`, and a **filtered** count must use `query_loki_logs` and count rows.
2. **Filtered counts are capped** by `NOC_FILTER_LIMIT` (500) and are reported
   as "at least N" when the cap is hit — never as an exact total.
3. **Several tools fail soft**: they return HTTP 200 with a plain-text sentence
   instead of JSON (`unknown arguments ...`, `no log entries ...`). `mcp()`
   surfaces these as `{"_raw": ...}` rather than raising.
4. **Zero total is not health.** A source with 0 entries renders `⚪` (neutral),
   never a green tick — a green tick next to "stale" is a contradiction.
5. **MCP needs `initialize` once** before any `tools/call`.

## Config

| Env | Default | Meaning |
|---|---|---|
| `BIFROST_VK_AIOS_NOC` | — | required; from Infisical |
| `NOC_ISSUE_NUMBER` | — | required; the output thread |
| `NOC_WINDOW_HOURS` | `24` | error-volume window |
| `NOC_STALE_HOURS` | `6` | silence that counts as stale |
| `NOC_FILTER_LIMIT` | `500` | cap on filtered rows counted |
| `NOC_EXPECTED_JOBS` | *(empty)* | explicit job list; empty = discover from Loki |
| `NOC_LOKI_UID` / `NOC_INFLUX_UID` | `loki` / `dfdkew37wk1dse` | datasource UIDs |
| `NOC_DRY_RUN` | — | `1` = print the report, post nothing |

## Rollout

| Step | Who | State |
|---|---|---|
| Tool plane (`mcp-grafana` read-only, 20 tools) | — | ✅ done (#30/#31) |
| `aios-noc` VK + `grafana_noc` client | — | ✅ live |
| Agent + manifests | assistant | ✅ this PR |
| **`BIFROST_VK_AIOS_NOC` into Infisical** | **human** | ⬜ **required before apply** |
| Image import to k3s + apply | assistant | ⬜ |
| R0 window (2 weeks of reports) → "hired" decision | agent → Chad | ⬜ |

**The VK value currently lives only in Bifrost's `config.db`**, which does not
survive a PVC rebuild. Putting it in Infisical (step 4) is what makes this hire
declarative and recoverable — and the sync manifest will not render a non-empty
Secret without it.

## Promotion gate (from `ai-it-team.md`)

1. Own Infisical identity, least privilege — ✅ `aios-noc`
2. Firewall allow-list in place — ✅ netpol (VLAN 30 scope is the tool plane's)
3. **R0 for 2 weeks with logged calls reviewed** — ⬜ this is the current gate
4. Failure-mode list written — see below
5. Only then R1/R2, one action type at a time

## Failure modes

1. **Silent source.** A job stops shipping and nothing else notices. Detected
   by the freshness check, but only for jobs present in Loki's `job` label —
   a source that vanishes *entirely* needs `NOC_EXPECTED_JOBS` set explicitly.
2. **Stale report read as current.** Mitigated by the mandatory
   `All values measured at` line.
3. **`udm-syslog` is the only source.** Today Loki has one job. The sweep is
   therefore thin — it is a real check but not yet broad coverage.
4. **Datasource UID drift.** UIDs are pinned by env; a datasource rebuild
   changes them and the sweep reports "unavailable" until updated.
5. **Tool-plane auth drift.** The sweep trusts the VK. If the VK is revoked or
   the client assignment changes, every check fails loudly (not silently).
6. **Issue-thread noise.** Twice-daily reports accumulate; the R0 window is
   ~28 comments. Fine for review, not a long-term home — see #17.

## Test

```sh
python3 aios/noc/selftest_noc.py     # 18 checks, no network
```
