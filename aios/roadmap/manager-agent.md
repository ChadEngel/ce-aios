# Manager agent — design & protocol (added 2026-10-04)

**What it is:** the orchestrator / "IT manager" role from `ai-it-team.md`, formalized. Its job is not to do infrastructure work — it's to **keep the backlog honest and keep work moving**. The specialist agents (NOC, patch, scheduler, …) do the work; the manager grooms, prioritizes, dispatches, and reports.

**Key design decision — GitHub Issues is the manager's state store.** No separate database, no tickets-in-a-doc. Issues + labels + milestones are the durable, auditable, versioned work queue, already living next to this repo. The manager reads and writes that queue.

## Where it sits relative to everything else
```
Open WebUI (front door)
      │
   Bifrost (gateway + routing)
      │
  ┌───┴─────────────────────────────┐
  │ Manager agent (this doc)        │  ← triage / prioritize / dispatch / report
  │   state = GitHub Issues         │
  └───┬─────────────────────────────┘
      │ dispatches to
  ┌───┴────┬─────────┬──────────┬─────────┐
 NOC     Patch   Facilities  Helpdesk  Personal (scheduler/travel/finance)
```
Same platform and guardrails as every other agent: secrets from Infisical only, per-agent identity, R0→R2 tiers, tool calls → Loki.

## Job (what "get them going" means concretely)
1. **Triage** every new issue: assign `area:*`, `prio:*`, `tier:*`, and write a one-line **next action**. An issue with no next action is unfinished triage.
2. **Prioritize against "finish before starting."** The manager's default is to surface the nearly-done P0 before anything new. It should say so out loud.
3. **Keep the "now" lane small.** WIP limit ~2–3 active items; everything else is `status:next` or `status:blocked`. Multi-tasking is the enemy of finishing (see `context.md` §2).
4. **Dependency tracking.** Blocked-by / blocks links; lift `status:blocked` when the blocker closes.
5. **Digest.** A short recurring report: *ready to move* / *waiting on Chad* / *blocked* / *new scope that competes with P0*.
6. **Flag scope creep once** (per the operating rules): if a new issue competes with an open P0, comment once and move on.
7. **Never closes, never executes.** It labels, comments, sets milestones, links. Closing an issue or running a runbook is a human/ specialist action (R2).

## Tiers
| Tier | Allowed |
|---|---|
| R0 (default) | read issues/comments/labels; produce the digest |
| R1 | add labels, set milestone, **comment** proposals (reversible, visible) |
| R2 | **not granted.** No closing, no merging, no editing issue bodies someone else wrote, no infra actions |

## Rollout — two phases (be honest about the dependency)

### Phase 0 — zero-runtime protocol (start here, available now)
The manager is a **protocol any assistant runs in-session** via `gh`, not a service. Trigger = "run the manager." This delivers most of the value with none of the runtime risk, and it does **not** depend on the tool plane.
- Rules + the digest format live in this file.
- Runs from any machine with `gh` authed (`repo` scope) — today that's Chad's.
- Output: labels/milestone set, comments posted, a digest message.

### Phase 1 — autonomous service (depends on the tool plane)
An in-cluster service (`aios-manager`) that polls or receives events and does the same loop unattended.
- **Auth:** a **GitHub App** installed on `ChadEngel/ce-aios`, scoped to Issues (read/write), private key in Infisical. *Not* a personal PAT (finer scoping, independent rotation/revocation). Chad installs it.
- **Trigger:** schedule (cron) + issue events. Webhooks need inbound; if we avoid inbound, **poll** on a short interval (e.g. every 15 min) — acceptable for this workload.
- **LLM:** routed through Bifrost, like every other agent.
- **Tiers:** R0/R1 only, same limits as Phase 0.
- **Observability:** every tool call → Loki.
- **BLOCKED ON:** the tool plane (`open-items.md` T1–T3). Cannot exist before it.

### Considered and rejected — GitHub Actions as the manager runtime
Runs in GitHub's cloud with no home runtime, which is tempting. Rejected because it needs an **LLM API key stored as a GitHub secret**, which violates the Infisical-only hard rule, and Bifrost is LAN-only (not reachable from GitHub's runners). Revisit only if Bifrost is ever exposed via a path-limited Cloudflare Tunnel — not worth the inbound surface for the marginal convenience.

## Labels (the manager's vocabulary)
| Group | Labels |
|---|---|
| Area | `area:secrets` `area:infra` `area:network` `area:ai-team` `area:personal-agents` `area:docs` `area:aios` |
| Priority | `prio:p0` `prio:p1` `prio:p2` `prio:parked` |
| Tier | `tier:r0` `tier:r1` `tier:r2` `human-executed` |
| Status | `status:next` `status:in-progress` `status:blocked` `status:waiting-on-chad` `status:review` |
| Type | `type:runbook` `type:spike` `type:decision` `type:agent-hire` |
| Meta | `manager` |

## Milestones
`Level 2 — Secrets consolidation` · `Tool plane / agent runtime` · `IT team hires` · `Personal agents` · `Docs & hygiene` · `Work management`

## Digest format (Phase 0 output)
```
AIOS digest — <date>
READY TO MOVE : <#issue ref → next action>   (cap ~3)
WAITING ON CHAD: <#issue → the exact question/approval>
BLOCKED       : <#issue → blocker>
COMPETING SCOPE: <new issue vs open P0, flagged once>
NOW LANE      : <the 1–3 things actually in progress>
```

## Open questions
1. GitHub App vs fine-grained PAT for Phase 1? (App recommended.)
2. Milestone per level vs per workstream — current set is per workstream; fine to refine.
3. Should the manager also own the scheduled digest (Phase 1) or just on-demand (Phase 0)?
