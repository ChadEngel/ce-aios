# AIOS knowledge base (Chad Engel)

Portable, model-agnostic memory for AIOS ("Jarvis" = Chad's own self-hosted AI operating system — not the third-party ownjarvisai.com SaaS, which was evaluated and rejected, see `decisions.md`).
Plain markdown. Load into a Claude Project, or an Open WebUI system prompt / Knowledge collection for any model routed through Bifrost.

**Contains no secret values.** Only names/locations of secrets (all live in Infisical). Do not paste credentials into these files.

## Load order
1. `operating-rules.md` — hard rules + agent guardrails (the system-prompt core). Load FIRST, always.
2. `context.md` — the single-file summary of who Chad is, the lab, active threads, current target. If you can only load one file, load this + operating-rules.
3. `environment/` — facts the agents need:
   - `network-and-hosts.md` — VLANs, hosts, IPs, services, URLs, Infisical layout
   - `home-assistant.md` — HA setup, deploy pipeline, prior MCP prototype, scope exception
   - `repo-map-and-sources.md` — which repo owns what, visibility, where to read the source of truth
4. `roadmap/` — what to do next:
   - `ai-it-team.md` — roles, build order, permission tiers, per-agent access
   - `personal-agents.md` — non-IT agents (scheduler, travel, financial); same platform/guardrails
   - `manager-agent.md` — the work-management agent + protocol (state = GitHub Issues)
   - `open-items.md` — prioritized tracker (secrets, Influx, network, docs, parked ideas) + pending questions

**Live backlog:** the actionable queue now lives in **GitHub Issues** on `ChadEngel/ce-aios` (labels `area:*`/`prio:*`/`tier:*`/`status:*`/`type:*`; milestones per workstream). `open-items.md` is the human-readable summary; issues are the source of truth for status.
5. `decisions.md` — dated why-we-chose-X log (read before re-litigating anything)
6. `session-log.md` — what happened each session / where we left off

## Update protocol (keeps this from going stale again)
- A "where are we / what's left" question = re-check the live repos first, then update `open-items.md` and `session-log.md`.
- **Work tracking:** GitHub Issues is the queue (see `roadmap/manager-agent.md`). New work = new issue with `area:*`/`prio:*`/`status:*` + a one-line next action. Keep the now-lane small.
- Any new decision → one entry in `decisions.md`. Any state change → `open-items.md`. New fact about hosts/network → `environment/`.
- `context.md` is the summary; detail lives in the topical files. When they disagree, the topical file + live repo win; fix `context.md`.
- Never write private-repo detail (`home-network-config`, `home-assistant-config`) into the PUBLIC `ce-ai-lab` repo.

Last full refresh: 2026-10-04.
