# Home Assistant (outside AIOS default scope — see exception below)

- **Host:** Raspberry Pi, HA OS, Core 2026.9.3, `homeassistant.local` = **192.168.250.168** (VLAN 1 Default). API :8123. An MCP/OpenAPI bridge was probed at :8300 (`/home-assistant`, tool `GetLiveContext`) in April 2026.
- **Placement is an open decision:** Default vs Family (see vlan plan).
- **Repos (private):** `home-assistant-config` outer repo = deploy tooling; inner `home-assistant/` repo = config (github.com/ChadEngel/home-assistant). `scripts/deploy.sh` rsyncs Mac→Pi over SSH and calls `reload_core_config`; `.githooks/post-merge` auto-deploys on pull; secrets pulled from Infisical project `HomeAssistant` via `lib/infisical-agent.sh` (identity `k3s-identity`). Secrets used: HA_ACCESS_TOKEN, HA_DEPLOY_KEY, HA_SSH_USER/HOST/PORT, HA_USER/PASS/SYSTEM_URL (reference).
- **Hygiene:** verified nothing sensitive tracked (secrets.yaml, Lutron private key, Nabu Casa `.conf` are gitignored and excluded from rsync).
- **Integrations (custom_components):** bambu_lab (3D printer), frigate, google_home, hacs, samsungtv_smart, webrtc; core: wemo, Lutron Caseta. Automations today: living room lights, Lauren's night light, outside lights, Christmas — small; the "re-design" is largely greenfield.
- **Prior AI work:** Feb 2026 design doc planned an Open WebUI + MCPO + ComfyUI stack (multi-user chat, MCP tools, laser-engraving image generation, HA dashboard embed). Existing `CLAUDE.md` in the inner repo is generic HA boilerplate.
- **Pending security follow-ups:** confirm rotation of the Infisical client secret, HA long-lived token, and SSH deploy key that INSTRUCTIONS.md says were exposed in chat.

## Scope exception (PENDING Chad's approval — do not assume granted)
Proposed: the Facilities agent (running on VLAN 30) may reach **only** 192.168.250.168:8123 (and :8300 if the MCP bridge is used), via a named firewall allow rule, read-only token first. Must be written into the Lab→Default rules when the segmentation plan reaches Phase 5, otherwise that deny will break it (and unpoller/udm-thermal, which already reach the UDM).
