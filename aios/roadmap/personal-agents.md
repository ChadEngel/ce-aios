# Personal agents — design & build order (added 2026-10-04)

**Why this file exists:** `ai-it-team.md` covers the lab. These are the *personal* roster — life admin, not infrastructure. Same platform and same guardrails (Open WebUI front door → Bifrost → Ollama/MCP, secrets only from Infisical, R0→R2 tiers, per-agent identity `aios-<role>`, one Entra app per agent for anything Microsoft). The difference: two of these touch real money and real bookings, so the tier discipline matters more, not less.

## Runtime prerequisite (shared with the IT team — build once, not per agent)
You do **not** need per-agent hardware. You need the **tool/execution plane** that currently doesn't exist:
- **Have:** Open WebUI (front door), Bifrost (gateway, already has MCP config in `client_config`), Ollama (local models), Infisical/Grafana/Loki/InfluxDB/k3s.
- **Missing:** a tool layer that lets a model actually call things. `mcpo` is documented but README-only — upstream image `ghcr.io/open-webui/mcpo` is not published, so that path is blocked. **First move: test Bifrost's native MCP support** (already deployed); fallbacks = Open WebUI native tool servers (OpenAPI) or self-built mcpo image.
- **Also missing:** MCP servers per data source (Graph, calendar, Loki/Grafana/Influx, k8s), per-agent Infisical identities (Level 2), and Entra app registrations (Chad creates).
- **Background agents need a home** — Open WebUI is interactive-only and can't run a "watch the calendar" loop. Runtime = Compose on the Mac Studio (leaning in-cluster next to Bifrost/Infisical for one secret/observability/firewall path). See `open-items.md` A1.

## Shared prerequisites (identical to the IT team)
- **Level 2 complete** — placeholders rotated, secrets in Infisical, per-agent identities. Every personal agent is gated on this, same as every IT hire.
- **Per-agent firewall allow-list**; default deny once the VLAN segmentation lands.
- **Microsoft Graph** (scheduler, and mail/calendar parts) = same per-agent Entra app registration pattern as `ai-it-team.md`: single-tenant, single mailbox/calendar via Exchange RBAC for Applications, certificate or short-expiry secret stored only in Infisical, no `Mail.Send`, no delete. Chad creates/consents the registrations, not the AI.
- **Voice/logging:** same as the rest — tool calls → Loki, explicit failure modes, one recommendation not a menu.

## Roster
| # | Role | Job | Data / tools | Tier | Status |
|---|---|---|---|---|---|
| 1 | **Scheduler** | Own the calendar: protect family/priority blocks, flag overcommitment, propose where a task/trip/project actually fits, surface conflicts | M365 Calendar via Graph (`aios-scheduler`) | R0 → R1 (propose; R2 only for explicitly confirmed moves) | NEW — highest leverage |
| 2 | **Travel agent** | Itinerary & award-travel planning: search, position, compare, draft an itinerary; never books without confirmation | Amex MR balance/points tools, flight/award search (Amadeus etc.), prior trip history | R1 (book = R2, human-confirmed per action) | NEW — episodic |
| 3 | **Financial planner** | Budget, account aggregation, projections, "what does this cost over N years" | UNCONFIRMED source (see below) | R0 only until proven | NEW — highest risk, build last |

## Per-agent notes

### 1. Scheduler (build first of the three)
- **Why first:** it directly attacks the self-identified core problem — unscheduled, fragmented free time and stalling projects. A scheduler that protects family time and forces "finish the nearly-done thing" into the calendar is the follow-through mechanism the rest of this system assumes but doesn't yet have.
- **Cheapest to build:** reuses the exact Graph/app-registration infrastructure the helpdesk agent (`aios-helpdesk-mail`) already needs — do them together. The `aios-calendar` registration is already scoped as optional in `ai-it-team.md`; this promotes it to real.
- **Scope:** read calendar R0; propose blocks/changes R1; move/create events R2 only with per-action confirmation. Never auto-accept invites, never auto-decline.
- **Family-first rule:** the agent should know the pattern (full-time job, kid's sports, family first) and be biased toward *protecting* family/priority blocks, not double-booking them.
- **Unconfirmed:** which calendar (personal `engelmn.com` assumed — same tenant as the helpdesk plan), shared/family calendar involvement, and whether it should read the employer calendar (employer tenant stays off-limits without policy clearance).

### 2. Travel agent (episodic — spin up when a trip exists)
- **Known from `assistant-briefing.md`:** planning MSP→OGG for **March 2027**; prefers premium cabins, minimal stops, avoids ATL where practical; uses Amex Membership Rewards (Platinum/Gold points strategy); has considered positioning through SEA, SLC, LAX, SFO. Treat as trip-specific, not permanent law.
- **Tools, in order:** award/points search (point.me-equivalent or Amadeus Self-Service API free tier) → itinerary drafting → cost/points comparison. Store **no payment credentials**; the agent drafts, Chad books.
- **Tier:** R1 (propose itineraries, don't transact). Any actual booking/purchase is R2, human-confirmed per action, and ideally done by Chad in the airline UI — the agent's job is the research and the draft.
- **First real job exists:** the March 2027 MSP→OGG trip is a concrete pilot, which keeps this from being speculative.

### 3. Financial planner (highest risk — build last, design carefully)
- **Hard truth:** least-known, highest-stakes. Nothing about personal finances (accounts, budget, goals, debt, brokerage) is established in any prior conversation — all **unconfirmed**. Do not invent account types, balances, or goals.
- **Data-source decision is the whole project** and is unresolved: manual CSV/export, a spreadsheet Chad maintains, an aggregator (Plaid/SimpleFIN — adds a vendor + credential surface + cost), or read-only brokerage APIs. Each has real security/trust tradeoffs; pick before building.
- **Tier:** R0 (read/compute/report) indefinitely. No money movement, no trades, no payments — ever, by an agent, without becoming a different conversation entirely.
- **Failure modes to write down early:** stale balances presented as current, currency/FX errors, tax assumptions stated as fact, and the classic planning-project trap (see the Maui business). Keep it a *tool*, not a new empire.

## Recommended sequence & the honest flag
- **Order:** Scheduler (bundle with the helpdesk/Graph work) → Travel (only when a trip is live, e.g. MSP→OGG) → Financial (last, after a data-source decision).
- **Flag (once, per operating rules):** three new agents is three new threads. Level 2 (secrets + Influx) is still open and is the literal prerequisite for all of them — and `ai-it-team.md` hires #1–#3 are already queued ahead. Recommendation: add these to the tracker, but let the **scheduler ride along with the existing helpdesk Graph work** (near-zero marginal cost) and keep travel/finance parked until Level 2 closes. Do not let these become the next well-planned, unbuilt pile.

## Open questions for Chad
1. Scheduler: which calendar(s), and are family/shared calendars in scope?
2. Scheduler: should it be allowed to *propose* changes (R1) from day one, or stay read-only (R0) for a trial?
3. Travel: is the March 2027 MSP→OGG trip the intended first real job?
4. Finance: what's the intended data source (manual/spreadsheet vs aggregator vs brokerage API)? This gates the whole agent.
5. Should the scheduler and travel agents share one "personal ops" orchestrator, or stay separate specialists under the existing front door?
