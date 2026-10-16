# aios-mail-mcp — Microsoft Graph MCP server for the helpdesk mail agent (hire #4, R0)
# Wraps @softeria/ms-365-mcp-server (pinned) in a slim non-root container.
# Runs: HTTP streamable transport on :3000, trust-proxy-auth (bearer check off —
# the ONLY inbound path is Bifrost via NetworkPolicy), read-only enforced
# in-server (non-GET Graph calls blocked + tools without readOnlyHint filtered).
#
# Identity model: one-time device-code login by Chad (delegated); the token
# cache persists on the aios-mail-cache PVC. No secrets are injected into the
# image or pod — the cache-encryption key path + auth-cache are container-local
# (config dir on the PVC). Boundary rule: the only agent secret tied to this
# service is the aios-helpdesk VK key consumed by digest/agent callers
# (jarvis-jatg), not by this container.
FROM node:22-slim
# node:22-slim per-arch digests differ; this image is built + imported into k3s
# locally (k3s ctr images import), never pulled from a registry — tag pin only.

ENV NODE_ENV=production
# Softeria default app registration (multi-tenant OAuth for device-code login)
# is used; no BYO CLIENT_ID/SECRET in v1. See roadmap/helpdesk-mail-agent.md.

RUN npm install -g --no-audit --no-fund @softeria/ms-365-mcp-server@0.168.0 \
    && npm cache clean --force

RUN mkdir -p /home/node/.config && chown -R node:node /home/node/.config
USER node
WORKDIR /home/node

EXPOSE 3000
ENTRYPOINT ["ms-365-mcp-server", "--http", ":3000", "--trust-proxy-auth", "--read-only"]
# Post-first-login hardening (T-M2): append --enabled-tools <exact names>
# once readOnly tools are enumerated via tools/list, and optionally
# --expected-username <chad-upn> to pin the cached account.