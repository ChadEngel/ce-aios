# NOC tech (hire #1) — PROMOTED to "hired" (R0 gate passed) 2026-11-04

Chad's decision: **promote.** This documents the gate review per
`ai-it-team.md` ("Definition of hired") against 8 days / 21 sweeps of live data.

## Gate checklist

| Gate | Evidence | Status |
|---|---|---|
| 1. Own Infisical identity, least privilege | VK `BIFROST_VK_AIOS_NOC` in `jarvis-jatg`/prod, active, rotated 2026-10-27; live probe: `/mcp/grafana-noc` **200** (20 read-only tools), `/mcp/graph` **403** | ✅ |
| 2. Firewall allow-list | NetworkPolicy `aios-noc-netpol` (egress: DNS + 443 + pushover-bridge:8080), 8d stable | ✅ |
| 3. R0 for 2 weeks with logged calls reviewed | 21/21 sweeps completed Oct 27 → Nov 4, zero failures/dupes; all reports on #17 reviewed in #89 | ✅ (window ran 8 of 14 days at decision; Chad promoted early on the evidence) |
| 4. Failure-mode list | `aios/noc/README.md` lists 10, each with a mitigation | ✅ |
| 5. R1/R2 one action type at a time | **Not granted by this promotion.** Agent stays R0 read-only; any R1 capability is a separate future decision | ⬜ deferred by design |

## What the window proved

- Caught the `udm-syslog` relay dark for ~6 days while the datasource reported
  healthy — exactly the stale-vs-unhealthy distinction it was built for — and
  confirmed recovery (Oct 30 23:15Z, fresh 0.0h).
- 0 false all-clears; the one rendering bug (✅ next to "stale") was caught by
  its own selftest before deploy, never shipped.
- Alert delivery (#88, merged) closed the "check nobody sees" gap: warn-or-worse
  pushes to the phone via the existing `pushover-bridge`, all-clear and log-noise
  never push. Bridge confirmed relaying: `pushover sent … NOC: 2 findings`.
- Device-offline detection (#87, merged) covers the UniFi gap the log sweep
  could not see.

## Standing posture after promotion

- **Still R0 read-only.** "Hired" ≠ write access. The VK has no mutating tools
  and `/mcp/graph` is 403. Promotion means the hire is trusted to keep running
  unattended and its findings to be acted on — not that it may act.
- Schedule unchanged: `15 6,18 * * *` America/Chicago, reports → #17.
- 50/50 selftests green on merged main.

## Residual risks (accepted)

1. Loki has a single `job` label value today (`udm-syslog`) — the sweep is a
   real but thin check. Widen with `NOC_EXPECTED_JOBS` as sources are added.
2. A sub-20-minute device outage between sweeps is invisible to the schedule
   (mitigated: window-max check reports *that* it happened).
3. A failed pushover push is silent to the phone (loud in logs); the path is
   exercised live.
