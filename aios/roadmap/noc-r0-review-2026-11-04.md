# NOC tech (hire #1) — R0 review, day 8 of 14

Deployed 2026-10-27 20:54Z (PR #82). This is the mid-window review; the promotion
decision gate is **~2026-11-10**. 21 sweeps have posted to #17.

## Verdict trend (all 21 reports)

```
Oct 27 20:54   🟠 udm-syslog stale 117.8h     ← first finding, flagged by hand days earlier
Oct 27–29      🟠 worsening: 120h → 156h      ← silent source, correctly tracked
Oct 29 23:15   🔴 no entries in 7 days        ← crossed the "absent" line
Oct 30 11:15   🔴 still absent
Oct 30 16:50   🔴 (last) — then pushed to phone via #88's alert path
Oct 30 23:15   ✅ fresh 0.0h — recovered on its own
Oct 31 → Nov 4 ✅ fresh every fire; only ⚠️ 0.1% error-ish (174k lines, normal)
```

**What it caught:** the `udm-syslog` relay (caelx004 → Loki) went dark for **~6
days** while the datasource itself reported healthy. The sweep rendered exactly
the distinction it was built for — 🟠 *stale* (nothing arriving) vs ❌
*unhealthy* (datasource down) — and watched it recover.

## Reliability

- **21/21 sweeps completed.** Zero failed jobs, zero missing reports, zero
  duplicate posts. CronJob `15 6,18 * * *` America/Chicago held the whole window.
- **0 false "all clear"** — every report with a finding carried the right icon;
  the one rendering bug (green ✅ next to "stale") was caught by its own selftest
  **before deploy**, never reached production.
- **Read-only confirmed:** the VK grants `grafana_noc:["*"]` only; `/mcp/graph`
  → 403 verified live. No write tool exists on the credential. (The stray VK
  that briefly carried a `graph` grant was caught and fixed pre-deploy — #83.)

## Gaps found and fixed during the window

| Gap | Fix | PR |
|---|---|---|
| Watched logs/datasources but not UniFi device state | `check_devices()` via unpoller→InfluxDB `network_metrics` | #87 (merged, live 0.1.2) |
| Findings only posted to a GitHub thread nobody reads | Push to phone via existing `pushover-bridge` (no new secrets; severity→priority) | #88 (open, deployed, see below) |

## The Pushover question (why you didn't get notified)

**You should have, and the plumbing is now in place but unmerged.** PR #88
(`feat/noc-alert-delivery`) adds alert delivery and was **applied live on
Oct 30** (image 0.1.2, both workers) but never merged to main — so main's
`noc.py` has no pushover code and the branch sits open with no review.

Evidence it works once running: the bridge logged
`16:50:10 pushover sent [firing/warning]: ℹ️ NOC: 2 findings — aios-noc`
on Oct 30. **But** the bridge's log shows only **5 NOC pushes total**, all from
that one day — the 10 warn-only sweeps since then (error-ish volume) correctly
did **not** push (alert-fatigue guard: never buzz for log noise). So the
behavior since Oct 30 is by design, but the Oct 27–30 🔴 finding never reached
your phone because #88 wasn't live yet then.

## Recommendation

**Merge #88.** It closes the actual gap (a check nobody sees is not a check),
holds no new secrets, has 50/50 selftests passing (incl. "failed push never
blocks the report"), and its alert-floor design means you only get buzzed for
warn-or-worse, never all-clear. Then the promotion decision on ~Nov 10 has a
complete picture: 14 days of reliable detection + delivery.

## Stats

- 21 sweeps · 11 with a severe finding (all the same udm-syslog outage) · 10 warn-only
- 272 tests green at deploy; 50 on the #88 branch
- R0 posture held: 0 writes, 0 mutations, credential scoped and revocable
