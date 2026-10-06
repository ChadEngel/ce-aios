# AIOS — Chad Engel Context File

Last updated: 2026-10-04 (rev 4: tenant confirmed personal engelmn.com (M365 Business Premium))
Purpose: portable memory for any assistant (Claude, or a local model through Bifrost) working with Chad. Plain markdown, no vendor-specific syntax — paste into Claude Project instructions/knowledge, or an Open WebUI system prompt / knowledge base.

---

## 1. Operating rules (hard constraints — never violate)

- **Secrets:** everything routes through Infisical. No exceptions, no hardcoded values, no "just for now" plaintext creds anywhere — scripts, env files, kubectl secrets, nothing.
- **Network scope:** all infrastructure work stays inside `192.168.30.0/24`. If a target (e.g. Home Assistant) turns out to live on a different subnet, stop and ask before touching it — don't assume reachability just because the VLAN can route out.
- **Voice:** direct, low-fluff, high technical competence assumed. Don't explain fundamentals (VLANs, SNMP, APIs, PostgreSQL, AWS regions, WireGuard, Grafana, LLMs) unless asked. One strong recommendation beats five options. State the recommendation, why, tradeoffs, implementation, failure modes — in that order.
- **Honesty over completeness:** where something is unconfirmed, say "unconfirmed." Never present planning assumptions as facts, and never fabricate business/financial detail that hasn't actually been established.

## 2. Who Chad is

**Professional:** Head of Infrastructure at a ~200-person hedge fund (offices/users in Minnesota, New York, Switzerland, Singapore). Microsoft/Azure-based environment (M365, Intune, Defender, Sentinel), Windows 11 endpoints, Oracle + PostgreSQL workloads. Evaluating a move off an MSP-operated model toward AWS, prioritizing resiliency and global presence. Investment strategy / AUM / fee structure: unconfirmed, not established in any conversation — do not invent.

**Family:** wife (new to AI, potential interest in a gamified "basic use" on-ramp; may eventually help with marketing/hospitality on the Maui venture) and one daughter, **Lauren, age 8**.

**Time reality:** full-time demanding job + an active kid in sports + family that rightly comes first. Home lab and side projects get worked "when I can" — unscheduled, not a fixed weekly block. This is the actual constraint everything else has to work around.

**The core pattern (self-identified, important):** Chad doesn't lack ideas — he has several strong ones running at once. What's missing is finishing. Projects stall half-done, not from lack of skill but from lack of a follow-through mechanism plus limited, unscheduled time. Any plan built for Chad should default to *finish the nearly-done thing* over *start the exciting new thing*, and should flag when a new idea is at risk of becoming another unfinished pile.

## 3. Home lab — `ce-ai-lab` (live infrastructure, not hypothetical)

Real, working, GitOps-managed self-hosted AI platform:

- Single-node k3s cluster, host `util-server` (192.168.30.217), on its own VLAN (`192.168.30.0/24`).
- Stack: Open WebUI (`ai.caehomelab.com`) as the chat front end, the **Bifrost** AI gateway (`llm.caehomelab.com`, OpenAI-compatible) routing to both local Ollama models (external host `aiserver.home:11434`) and cloud providers, SearXNG for private search, Infisical (`secrets.caehomelab.com`) for centralized secrets, cert-manager + Cloudflare DNS-01 for TLS, Traefik ingress.
- Monitoring: Grafana + InfluxDB v2 + Loki (UDM syslog, 15-day retention) + unpoller (UniFi network metrics). Domain: `caehomelab.com`.
- History note: a Cloudflare token, an InfluxDB token, and TLS keys were leaked in old git history and have been rotated; git history was rewritten.

**Active/half-finished work (checked 2026-10-04 against the live repo — still open):**
- **InfluxDB migration** off `aiserver.home` into the k3s cluster — **NOT cut over; deferred.** Confirmed by Chad 2026-10-04: it's working as-is and is **not a blocker** — do not treat it as an open priority or re-open it. (In-cluster `influxdb` manifests exist and a status doc references a 2026-07-28 migration, but the live cutover has not happened.)
- **Secrets consolidation** — worse than "not 100%": an August audit found **four live placeholder credentials** in production that were never rotated — Grafana `admin`/`admin`, and three Infisical-internal secrets (`AUTH_SECRET`, `ENCRYPTION_KEY`, the Postgres password). A full rotation runbook exists (`docs/rotate-placeholder-credentials.md`) but none of the four steps have been executed. The Cloudflare API token leaked in old git history is also still unrotated at the provider (repo history was cleaned, but the live token itself wasn't swapped).

**New since August (shipped, not part of the original Level 2 target):**
- Tailscale HA subnet-router pair (`util-server` + `caelx002`) advertising `192.168.30.0/24` — done, checked off in `SETUP.md`.
- UniFi controller deployed as a cold DR standby (idle, no devices adopted) for UDM Pro failover — done, working as intended.
- A standalone syslog-ng relay VM (`caelx004`, `192.168.30.189`) catching UDM Pro syslog and forwarding to Loki — **live and in use as of 2026-10-04** (the home-network-config decision log shows Loki DHCP/STP queries used to root-cause the Tablo/CAESW002 port-flap). Correction to the earlier "not confirmed" note; the old "point UDM at Promtail NodePort" outstanding item is superseded.
- A UDM Pro config backup/replacement effort — a full UDM export snapshot exists locally (gitignored, correctly never committed) dated 2026-09-26, and the README now references a `UDM_REPLACEMENT.md` runbook — but that file doesn't actually exist in the repo yet. Loose end to close or clarify.
- A few more app folders exist (`postgres`, `redis` — Infisical's own backing store; `pushover-bridge`, `synthetic-monitor`, `udm-thermal`) that aren't listed in the Services status table — unclear if they're actually deployed or just scaffolded.

**Pattern check:** this is the same shape Level 1 surfaced in August — new, interesting builds (Tailscale HA, UniFi DR, the syslog-ng relay) got finished, while the explicitly-named priority (secrets consolidation) sat untouched and even grew a bit worse (the placeholder-credential audit). Worth naming plainly rather than letting it slide again.

## 4. Other active threads

**West Maui whale-watch / dinner-cruise business** — planned with his wife (Chad: captain + tech/ticketing; wife: marketing + hospitality). Fully modeled on paper: ~100-passenger vessel, ~$956K startup capital, ~$605K annual opex, ~$2.08M projected annual revenue, modeled pricing ($75–$139/trip). **All of this is planning, not execution** — no vessel, no captain's license yet, no bookings, business name unconfirmed. Treat as a live parallel to the "ideas vs. finishing" pattern: extensive planning, zero build.

**Kids' AI-literacy workspace** — idea phase, not started. Concept: a scoped, filtered, local AI environment for Lauren (age 8) built on the existing Open WebUI/Ollama stack, using the UDM Pro's web filtering to bound what search results reach her, with a gamified curriculum layer on top. Possibly extends to a simpler "basic AI use" on-ramp for his wife.
  - Market check (2026-08-17): **not an empty space.** Learning.com sells a turnkey K-8 AI literacy curriculum, MIT's "Day of AI" and Common Sense Education have free curricula, several states have active 2026 K-12 AI policy legislation, and hands-on kid tools already exist (Google's Teachable Machine, "Machine Learning for Kids"). What's genuinely different about Chad's angle: a kid running a **real local model on family-owned hardware**, with **parent-controlled network-level filtering** instead of a vendor's cloud policy — that's not what the existing players offer.
  - **Explicit flag: do not scope this as a business before it's built and used for real, for one kid.** The Maui venture is the cautionary example — a full business plan with nothing shipped. Build it for Lauren first. Revisit the business question only with real evidence afterward.

## 5. Level 2 target (current focus)

**Close every remaining secrets gap into Infisical.** (InfluxDB is explicitly out of scope here — deferred, working as-is, not a blocker.)

Why this, not the Lauren project or the boat:
1. It's nearly done, not a new start — the lowest-risk way to actually prove the finishing muscle works, which matters more than any new feature.
2. Chad named it himself, unprompted, as "the first step to get things going."
3. It's the literal prerequisite for an assistant (Claude or otherwise) to safely operate on this infrastructure — access has to be fully centralized and scoped through Infisical + the VLAN boundary first. Finishing it unblocks the Lauren project too (which will need its own model-access/filtering credentials handled the same clean way).

## 6. Model routing note — Claude vs. self-hosted via Bifrost

As of 2026, checked against current sources (not stale training-data assumptions): closed frontier models (Claude Opus/Sonnet, GPT-5.x) still lead specifically on **long-horizon, ambiguous, multi-tool orchestration** — open-weight models (Qwen 3.6, GLM 5.1, DeepSeek V4, Kimi K2.x) have closed the gap hard on structured coding and high-volume narrow tasks, but still show "loss of coherence in longer agent loops" and gaps on "abstract reasoning or novel problem structures." Self-hosting only pays for itself economically above roughly 5–10M tokens/month of sustained use — below that, a frontier API is usually cheaper and simpler than running the MLOps to support local inference well.

**Recommendation:** keep the AIOS strategy/coaching layer (this kind of work — interviews, planning, cross-domain reasoning) on a frontier model. Route Lauren's workspace and repeatable, well-scoped homelab scripting tasks through Bifrost to local/open models — better fit anyway: private by default (matters more for a kid), free, works offline, no reason to pay frontier prices for narrow, repeatable work.

## 7. Level status

- **Level 1 (Discovery):** complete.
- **Level 2 (The Big Win):** target named Aug 17. Remaining scope = full Infisical secrets consolidation (the four live placeholder credentials). InfluxDB migration is **deferred, not a blocker** (working as-is). Still open as of the Oct 4 check-in — not advanced, while other new infra shipped instead (see §3).
- **Level 3 (Personal Claude / persistent context):** this file. Note: it sat unopened/unedited for the ~7 weeks between Aug 17 and Oct 4 — real infra work happened in that window that this file didn't capture until the Oct 4 check-in. If this file isn't the thing being updated as work happens, it will drift stale again; treat a "what's left / where are we" question as a trigger to re-check the live repo, not just re-read this file.
- **Level 4 (First Automation):** not started.
- **Level 5 (AIOS Foundation):** not started.

## 8. Network, Home Assistant, and the AI IT team (added 2026-10-04 after reading all four repos)

**Repo map (visibility matters — never copy private-repo detail into the public one):**
- `ce-ai-lab` — **PUBLIC** on GitHub. k3s cluster + AI apps. Keep it free of anything sensitive; `exports/` is correctly gitignored.
- `home-network-config` — private. UDM-Pro, switches/APs, syslog receiver, Grafana alert/dashboard JSON, runbooks, decision log, VLAN segmentation plan. Single initial commit, pushed.
- `home-assistant-config` — private. Outer repo = deploy tooling (rsync to the Pi + HA reload, secrets pulled from Infisical project `HomeAssistant`); inner repo = the HA config. Clean hygiene: no secrets.yaml / Lutron key / Nabu Casa token tracked.
- `jarvis` — this folder (personal context). `ce-documentation` and `ce-pi-mac` are referenced but not yet shared.

**Network facts (from `topology.md`):** UDM-Pro 192.168.250.1, dual-WAN. VLAN 1 Default 192.168.250.0/24 (network hardware + **Home Assistant, a Raspberry Pi on HA OS, 192.168.250.168, Core 2026.9.3**); VLAN 30 Lab 192.168.30.0/24 (k3s, NFS, syslog, and the Mac Studio inference host `aiserver.home`=`aibeasts-mac-studio` M3 Ultra 96GB at 192.168.30.10); VLAN 40 "Music" (really cameras + Sonos); VLAN 50 Secure/Family; VLAN 60 IoT planned. **Inter-VLAN firewall is currently ALLOW_ALL** (only 3 rules exist) — the segmentation plan (6 phases, nothing started) is the real access-control layer for any AI agent.

**Scope rule consequence:** Home Assistant is on 192.168.250.0/24 — OUTSIDE the 192.168.30.0/24 scope. Per Chad's rule, ask before any HA access; any HA agent needs an explicit, narrow, documented firewall allow (Lab → 192.168.250.168:8123 and the MCP bridge on :8300), and the planned "Lab → Default deny" rule would break it (and unpoller/udm-thermal) unless exceptions are written in.

**HA agent was already prototyped:** `.claude/settings.local.json` in the HA repo shows an April 2026 session probing an MCP/OpenAPI bridge at 192.168.250.168:8300 (`/home-assistant`, `GetLiveContext`). A Feb 2026 design doc (`AI_Chat_Dashboard_Setup_Guide.docx`) planned Open WebUI + MCPO + ComfyUI (laser-engraving image gen) as **Docker containers on the Mac Studio**, Ollama reachable over Tailscale. So "run on the AI server without k3s" is Docker/Compose on the Studio, not a Linux VM + k3s node.

**Open secrets items found in these repos (verify, don't assume):**
- `home-network-config/udm-pro/exports/` is tracked in git and still contains unredacted-looking values: a 32-char `psk` and `x_iapp_key` (x4) across `rest_setting.json` / `rest_wlanconf.json`. The sanitize script + pre-commit hook in `docs/rotating-leaked-secrets.md` are unchecked action items. Rotate/redact, then scrub history.
- HA `INSTRUCTIONS.md` says the Infisical client secret, HA long-lived token, and SSH deploy key were "visible in chat during setup" and should be rotated — rotation not confirmed.
- UDM SSH is root + password auth, and a k8s sidecar (`udm-thermal`) uses it. **No AI agent ever gets those creds** — read-only UDM API key only.
- Infisical project names are inconsistent across docs (`secret-management`, `caehomelab-v1q6`, `homelab`, `HomeAssistant`). Pick the canonical set.

**The AI IT team — roles and build order (decided 2026-10-04):**
1. IT Manager / orchestrator (Bifrost-routed model; test multi-step tool reliability before trusting a local model here).
2. NOC tech — read-only Loki/Grafana/InfluxDB via MCP. **Hire #1.** Precedent: the 10-04 Loki-driven diagnosis was exactly this job done by hand.
3. Patch tech — report-only image/firmware currency (digest-pinned images → "tell me, don't apply").
4. Facilities tech — Home Assistant (needs scope exception + firewall allow).
5. Helpdesk — email triage + Teams/text (last; tenant confirmed: personal `engelmn.com`, M365 Business Premium + Teams; waits for hires #1–#3; open: what "Azure Plan 2" is, which mailbox). Outbound push already exists via `pushover-bridge`.
Prerequisite for every hire: Level 2 (secrets in Infisical, placeholders rotated) + per-agent scoped credentials + firewall allow-lists. Read-only first; any write/remediate action needs human confirmation.
- **Update 2026-10-04 (later):** Chad confirmed the shop is **Microsoft 365, not Google** and he can create Entra app registrations → Helpdesk agent uses Microsoft Graph. Personal-vs-employer tenant is still unanswered (employer tenant off-limits without policy clearance). Detail now lives in `roadmap/ai-it-team.md`; this folder (`aios/`) is now a multi-file knowledge base — see `README.md` for load order.
- Update 2026-10-04 (tenant): `engelmn.com` is a personal M365 Business Premium tenant with Teams and "Azure Plan 2" (TBD). Employer tenant is not used.
