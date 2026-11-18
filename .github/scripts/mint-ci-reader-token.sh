#!/usr/bin/env bash
# Trusted default-branch helper; fixed reader broker and target.
readonly BROKER=ci-reader
readonly SCOPE=Nofraud/terraform-custom-checks
readonly IDENTITY=terraform-composite-actions
set -euo pipefail
case "$BROKER" in
  gitops|redeploy|ci-reader) ;;
  *) echo "::error::unknown broker '${BROKER}' (allowed: gitops, redeploy, ci-reader)"; exit 1 ;;
esac
if ! [[ "$SCOPE" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "::error::scope must be owner/repo (got '${SCOPE}')"; exit 1
fi
if ! [[ "$IDENTITY" =~ ^[a-z0-9-]+$ ]]; then
  echo "::error::identity must match ^[a-z0-9-]+\$ (got '${IDENTITY}')"; exit 1
fi
if [ -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
  echo "::error::no OIDC token request URL. The job needs permissions: id-token: write"; exit 1
fi

# The broker's custom domain is the OIDC audience.
domain="${BROKER}.sts.nofraud.com"
oidc=$(curl -sSf --retry 3 --connect-timeout 10 --max-time 30 \
  -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${domain}" | jq -r '.value // empty')
if [ -z "$oidc" ]; then echo "::error::GitHub returned no OIDC token"; exit 1; fi
echo "::add-mask::${oidc}"

# curl retries 5xx and timeouts, which covers a Lambda cold start.
resp=$(mktemp)
trap 'rm -f "$resp"' EXIT
code=$(curl -sS --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 30 \
  -o "$resp" -w '%{http_code}' -H "Authorization: Bearer ${oidc}" \
  --get --data-urlencode "scope=${SCOPE}" --data-urlencode "identity=${IDENTITY}" \
  "https://${domain}/sts/exchange") || code="curl-error"
if [ "$code" != 200 ]; then
  # Responses can contain credentials even after failed transfers.
  echo "::error::${domain} returned HTTP ${code} for scope=${SCOPE} identity=${IDENTITY}"
  exit 1
fi
# Validate before command substitution strips trailing newlines.
if ! token=$(jq -ers '
  select(length == 1) | .[0]
  | select(type == "object") | .token
  | select(type == "string") | select(length > 0)
  | select(explode | all(. >= 32 and . <= 126))
' "$resp" 2>/dev/null); then
  echo "::error::${domain} returned an invalid token response for scope=${SCOPE} identity=${IDENTITY}"
  exit 1
fi
if [ -z "$token" ]; then echo "::error::${domain} returned no token"; exit 1; fi
echo "::add-mask::${token}"
echo "token=${token}" >> "$GITHUB_OUTPUT"
echo "Token issued by ${domain} for ${SCOPE} (identity ${IDENTITY})."
