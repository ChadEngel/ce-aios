# Repo map — what owns what, and where truth lives

| Repo / folder | Visibility | Owns | Read first |
|---|---|---|---|
| `ce-ai-lab` (~/dev/ce-ai-lab) | **PUBLIC** (github.com/ChadEngel/ce-ai-home-lab) | k3s manifests, AI apps, deploy scripts, lab docs | README.md, DEPLOYMENT_STATUS.md, MONITORING.md, INFLUX_MIGRATION_STATUS.md, docs/ |
| `home-network-config` (~/dev) | private (ChadEngel/home-network-config), 1 commit | UDM-Pro, switches/APs, syslog receiver (duplicate of ce-ai-lab `infra/syslog-receiver`), alerts/dashboards, runbooks, decision log, VLAN plan | topology.md, docs/vlan-segmentation-plan.md, docs/decision-log.md |
| `home-assistant-config` (~/Source) | private (outer) + ChadEngel/home-assistant (inner) | HA config + Mac→Pi deploy pipeline | INSTRUCTIONS.md |
| `jarvis` (~/Source/jarvis) | local only, not a git repo | AIOS knowledge base (this folder), assistant-briefing.md, brand-context.md | aios/README.md |
| `ce-documentation`, `ce-pi-mac` | unknown | referenced, not yet shared | ask |

Rules: never copy private-repo content into `ce-ai-lab`. `ce-ai-lab/exports/` and `udm-config-*` are local-only (gitignored) and contain TLS keys, kubeconfig and secrets caches — never read them into chat or commit them.

Source-of-truth conflicts to resolve: (1) `syslog-receiver` exists in BOTH ce-ai-lab and home-network-config — choose one owner; (2) `DEPLOYMENT_STATUS.md` header says "last verified 2026-07-18" though content is newer; (3) ce-ai-lab README references a nonexistent `UDM_REPLACEMENT.md`; (4) Ollama host is called both `aiserver.home` and AIbeast in docs (same machine); (5) firewall group `k3s_nodes` lists a stale `.60`; (6) Infisical project names inconsistent.

Access reality: AI sessions read these via the device bridge from chad-studio-home; they have NO live network path into the lab. Live state (kubectl, Grafana, Loki) must be run/pasted by Chad until the NOC agent exists in-cluster.
