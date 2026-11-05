# Stakpak spike — home trial on aiserver.home (issue #92)

Started 2026-11-04. Goal: run the free Stakpak agent at home, understand how it
works, then decide whether/how it maps to work. Tracked in issue **#92**.

## Verified facts (2026-11-04, from stakpak.dev, stakpak.gitbook.io/docs, github.com/stakpak/agent)

- **Product:** open-source DevOps agent (Rust, Apache-2.0, `stakpak/agent`, ~1.8k stars),
  install via `curl -sSL https://stakpak.dev/install.sh | sh` or `brew tap stakpak/stakpak`.
  Banner on stakpak.dev: **"Stakpak is joining Vercel"** — read the announcement before
  depending on the hosted service; the OSS binary is the durable artifact.
- **No Stakpak API key for the free/OSS tier.** Docs (oss-vs-cloud-vs-enterprise):
  OSS "runs fully in your environment with your own key and requires no connection to
  the Stakpak API (except fetching public Rulebooks from the Stakpak Registry, which is
  unauthenticated)." A Stakpak API key exists only for **Stakpak Cloud** (paid shared
  rulebooks/memory/recovery, LLM-gateway wallet). The `@stakpak/mcp` npm server also
  needs a Stakpak key but only wraps the cloud API — not used in OSS mode.
- **Local LLM (our path):** profile with `provider = "local"`, custom provider
  `api_endpoint = "http://<ollama-host>:11434/v1"`, `api_key` optional for local
  providers; model referenced as `offline/<model>`. On aiserver.home that is
  `http://192.168.30.10:11434/v1` — the same Ollama the mail digest uses (qwen3.8).
- **Config:** `~/.stakpak/config.toml` holds provider credentials in plaintext — keep
  `~/.stakpak/` out of any sync/backup/git.
- **Warden** = deterministic network sandbox / destructive-op guardrails
  (`stakpak warden`; docs `how-it-works/warden-guardrails`). Enable on the lab box.
- **Autopilot** = 24/7 background mode (`~/.stakpak/autopilot.toml`: schedules, channels,
  notification routes). Treat as R1+ — do not enable unattended until guardrails are read.

## Why aiserver.home (not caelx004, not the cluster)

- aiserver.home (192.168.30.10, Mac Studio M3 Ultra) is in VLAN 30 (in scope) and already
  runs Ollama — Stakpak's local-model backend needs nothing new there.
- caelx004 was rejected: no SSH path for agent sessions, off-cluster syslog relay with no
  monitoring (went dark 6 days unnoticed until the NOC flagged it), wrong host for an agent.
- Cluster (ns `ai`) deployment later would follow the proven `aios-noc` pattern (own VK,
  egress NetworkPolicy, InfisicalSecret) — but Stakpak OSS has no Bifrost/VK integration;
  its MCP server is cloud-only. So the first home is the Studio itself.

## Setup checklist (Chad runs on the Studio; agent sessions have no SSH path to it)

- [ ] `curl -sSL https://stakpak.dev/install.sh | sh` (or `brew install stakpak`)
- [ ] `~/.stakpak/config.toml` offline profile → `http://192.168.30.10:11434/v1`,
      model `offline/qwen3.8:latest` to start (compare with `ollama/muse-glimmer`)
- [ ] `~/.stakpak/` private: not in iCloud/Drive/Time-Machine-shared paths, not in any repo
- [ ] Read `docs/how-it-works/warden-guardrails.md`, then start Warden before any real task
- [ ] First tasks R0 read-only: GitHub issue listing via `gh`, Loki queries via curl,
      `kubectl -n ai get` read-only — same posture as the NOC agent
- [ ] Do NOT enable autopilot schedules or cloud login during the home trial

## Findings log

(append here as the trial runs)

## Work-side evaluation gate (separate decision, later)

Nothing client/work-data-bearing goes to Stakpak Cloud without vendor security review
against the M365/Defender/Sentinel posture. Checklist: Enterprise/on-prem deployment,
data residency, SOC 2 / compliance attestations, Warden Cedar custom policies, SSO,
audit logging, what the agent can reach (credential scope), and the Vercel acquisition's
effect on roadmap/support.
