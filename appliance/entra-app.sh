#!/usr/bin/env bash
# Inbox Console M0 — creates the app registration YOUR tenant needs for the
# per-user mail appliance. Run locally with `az login` already done.
#
# Creates NOTHING privileged:
#   - public client (no client secret, no certificates)
#   - delegated Microsoft Graph scopes ONLY:
#       User.Read, Mail.Read, Mail.ReadWrite, Contacts.Read
#   - NO Mail.Send / SMTP / application-level permissions anywhere — the
#     appliance cannot send mail, by registration-level construction.
set -euo pipefail

APP_NAME="${APP_NAME:-aios-inbox-console}"
GRAPH="00000003-0000-0000-c000-000000000000"   # Microsoft Graph service principal
REDIRECT="http://localhost:8614/callback"

USER_READ="e1fe6dd8-ba31-4d61-89e7-88639da4683d"
MAIL_READ="570282fd-fa5c-430d-a7fd-fc834984d1c4"
MAIL_RW="024d486e-b451-40bb-8fe4-d10ac68d1e83"
CONTACTS_READ="9d761c7c-9a19-46a6-97d5-9e20960e2efb"

if ! az account show >/dev/null 2>&1; then
  echo "az CLI not logged in — run \`az login\` first" >&2
  exit 1
fi

PERMS=$(mktemp)
cat > "$PERMS" <<EOF
[{"resourceAppId":"$GRAPH","resourceAccess":[
  {"id":"$USER_READ","type":"Scope"},
  {"id":"$MAIL_READ","type":"Scope"},
  {"id":"$MAIL_RW","type":"Scope"},
  {"id":"$CONTACTS_READ","type":"Scope"}]}]
EOF

echo "==> app registration '$APP_NAME' (single tenant, public client)"
APP_ID=$(az ad app create \
  --display-name "$APP_NAME" \
  --sign-in-audience AzureADMyOrg \
  --is-fallback-public-client true \
  --required-resource-accesses "$PERMS" \
  --query appId -o tsv)

echo "==> enabling PKCE loopback redirect ($REDIRECT)"
az ad app update --id "$APP_ID" \
  --set "publicClient={\"redirectUris\":[\"$REDIRECT\"]}"

TENANT=$(az account show --query tenantId -o tsv)
echo
echo "================================================================"
echo "  Inbox Console app registration created (M0)"
echo "================================================================"
echo "  TENANT_ID : $TENANT"
echo "  CLIENT_ID : $APP_ID"
echo "  redirect  : $REDIRECT"
echo "  scopes    : User.Read, Mail.Read, Mail.ReadWrite, Contacts.Read"
echo "              (delegated only — Mail.Send NOT requested, ever)"
echo "================================================================"
echo "  Verify consent status with:"
echo "    az ad app permission list --id $APP_ID -o table"
echo "================================================================"