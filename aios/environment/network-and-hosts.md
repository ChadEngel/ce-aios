# Network & hosts (no secrets)  — verified from repos 2026-10-04

## Edge
UDM-Pro 192.168.250.1 (UniFi OS 5.1.33 — decided NOT to roll back, see decisions). Dual-WAN: Comcast primary, Verizon failover. WireGuard remote access (`engelvpn` 10.20.1.0/24, port 51820) + "One-Click VPN" 192.168.2.0/24. Switches CAESW001 (.250.42) and CAESW002 (.250.63), both USW-Lite-16-PoE; plus one non-managed Netgear (legacy 192.168.4.0/27 camera path, undocumented). APs: Basement .250.105, Garage .250.171, Upstairs .250.173. SSIDs: TwinsWin (VLAN1), TwinsWin_2.4 (VLAN2 legacy), TwinsWin_Music (VLAN40), TwinsWin_Secure (VLAN50, to be renamed Family).

## VLANs
| VLAN | Subnet | Purpose | Notes |
|---|---|---|---|
| 1 Default | 192.168.250.0/24 | network hardware + Home Assistant | HA = 192.168.250.168. Droplet-7B40 VM at .250.157 (planned move to VLAN 30). NAS eth0 (SMB) here today |
| 2 IP_Camera (legacy) | 192.168.4.0/27 | old DVR 192.168.4.26 | to retire |
| 30 Lab | 192.168.30.0/24 | k3s, NFS, syslog, Proxmox, AI inference | **AIOS scope** |
| 40 "Music" | 192.168.40.0/24 | Sonos + all Reolink cameras | misnamed; rename pending |
| 50 Secure→"Family" | 192.168.50.0/24 | Macs, iPhones, iPads, Apple TVs, Rokus | |
| 60 IoT | planned | Tablo, Lutron, Wemo, MyQ, Rachio, Roomba, Kasa, Echo, etc. | not created |
| 99 temp | 192.168.99.0/24 | throwaway testing | |

**Inter-VLAN firewall: effectively ALLOW_ALL** (3 rules total). Segmentation plan exists (`home-network-config/docs/vlan-segmentation-plan.md`, 6 phases, Phase 0 not started).

## VLAN 30 hosts
| Host | IP | Role |
|---|---|---|
| util-server | 192.168.30.217 | k3s control-plane + Traefik LB (Mac mini, VMware Fusion Ubuntu VM, bridged). Also Tailscale subnet router |
| caelx002 | .30.252 (assumed) | k3s worker; Tailscale HA peer; pins Loki/Open WebUI/(InfluxDB target) |
| caelx003 | 192.168.30.251 | k3s worker; UniFi DR standby (hostNetwork), DNS unifi.caehomelab.com |
| caelx004 | 192.168.30.189 | syslog-ng receiver (Proxmox VM 104) UDM→Loki, off-cluster by design |
| caevmhost01 | 192.168.30.204 | Proxmox VE 9.2 host (Telegraf → host_metrics) |
| NAS (eth1) | 192.168.30.121 | NFS `/data/pod_data` for k3s PVCs (eth0 on Default for SMB) |
| aiserver.home = aibeasts-mac-studio | 192.168.30.10 | Mac Studio M3 Ultra 96GB. Ollama :11434 (native macOS, keep native for Metal GPU). Also old InfluxDB :8086 (migration pending). Only workload today: Ollama |
| chad-studio-home | (client) | Chad's daily-driver Mac — work, dev, 3D printing. NOT an AI host |

## Services (all behind Traefik at 192.168.30.217, TLS via cert-manager + Cloudflare DNS-01; reachable off-LAN via Tailscale subnet route)
ai.caehomelab.com (Open WebUI) · llm.caehomelab.com (Bifrost gateway) · search.caehomelab.com (SearXNG) · secrets.caehomelab.com (Infisical) · grafana.caehomelab.com · loki.caehomelab.com · influxdb.caehomelab.com (new in-cluster instance, not cut over) · unifi.caehomelab.com:8443 (DR standby). Other app folders present but not in the status table: postgres, redis (Infisical backing), pushover-bridge, synthetic-monitor, udm-thermal, unpoller, mcpo (README only).

## Secrets layout (names only)
Infisical (self-hosted, `secrets.caehomelab.com`): project `secret-management` env `prod` path `/` (a.k.a. `caehomelab-v1q6`) for the lab; separate project `HomeAssistant` for HA tooling. Machine identities: `homelab-k8s-operator` (Viewer; in-cluster sync), `homelab-agent` (admin scripts), `k3s-identity` (HA project). Key names: CLOUDFLARE_API_TOKEN, INFLUXDB_TOKEN (write), INFLUXDB_READ_TOKEN (read; Grafana), UDM_API_KEY (read-only), UDM_ROOT_PASSWORD/UDM_SSH_PASS, LINUX_USER/LINUX_PVT_KEY, K3S_NODE_TOKEN, HA_ACCESS_TOKEN/HA_DEPLOY_KEY/HA_SSH_*. Naming across docs is inconsistent (`secret-management` / `caehomelab-v1q6` / `homelab`) — canonicalize.

## Monitoring that already exists
Telegraf → InfluxDB buckets host_metrics, kube_metrics, mac_metrics, network_metrics, proxmox_metrics; unpoller (UniFi); Loki (UDM syslog, 15d); Grafana dashboards (k8s, pod resources, linux host, udm-syslog, unifi-network) and 9 alert rules in `home-network-config/monitoring/alerts` (udm-offline, udm-rebooted, udm-soc-high, syslog-ingest-stalled, link-flap, auth-failures, warn/error spike, infra-service-down/recovering, infra-monitor-silent). Push notifications via `pushover-bridge`.

## Known network incident pattern
2026-10-04: UDM uplink port bouncing traced via Loki to Tablo t4g flapping on CAESW002 port 9 → STP TCN. Fix: lock port 9 to 100M FDX. Method (LogQL on `{job="udm-syslog"}` + switch kernel logs) is the template for the NOC agent.
