# Session log

## 2026-10-04 (session 2)
- Re-scanned ce-ai-lab: Influx migration still not cut over; four placeholder credentials still live; Tailscale HA, UniFi DR, syslog receiver shipped. Updated context.md.
- Clarified topology: chad-studio-home = client; aiserver.home = aibeasts-mac-studio (Ollama only, 192.168.30.10). Withdrew k3s-on-Studio idea.
- Evaluated ownjarvisai.com → rejected (SaaS/funnel; wrong trust model).
- Defined vision: AI IT team with orchestrator + agents; sequencing agreed.
- Chad connected home-network-config and home-assistant-config; read all four repos. Found HA at 192.168.250.168 (out of scope → exception pending), prior MCP prototype, unredacted-looking psk/iapp keys in network exports, unconfirmed HA secret rotations, allow-all inter-VLAN firewall.
- Chad: M365 shop (no Google); can create app registrations. Later: tenant is personal `engelmn.com`, M365 Business Premium + Teams + "Azure Plan 2" (TBD).
- Built this knowledge base in `jarvis/aios/` (README, operating-rules, context, environment/*, roadmap/*, decisions, session-log).
- **Where we left off:** waiting on answers to the 4 questions in `roadmap/open-items.md`; recommended next action = S1 (rotate Grafana admin password) then build NOC agent R0.

## 2026-08-16/17 (session 1)
- Level 1 discovery; read jarvis + ce-ai-lab; named Level 2 target; created `aios/context.md` and README; researched kids-AI market and Claude-vs-local models.
