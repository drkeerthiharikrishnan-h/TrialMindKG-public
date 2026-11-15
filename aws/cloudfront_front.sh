#!/usr/bin/env bash
# Amazon CloudFront in front of the existing site (PK14 A1). ADDITIVE: the Caddy front door and its link
# https://tmkg.<elastic-ip-with-dashes>.sslip.io/ stay exactly as they are and remain the fallback link; CloudFront only adds
# a second address, https://<distribution>.cloudfront.net/, that reaches the same Caddy over HTTPS.
#   ONE distribution for the project, found again by its comment 'tmkg front' and its tag project=trialmindkg; the fixed
#   CallerReference 'tmkg-front' makes AWS refuse a second one. Origin = the Caddy name derived from the project's Elastic IP
#   (as aws/deploy_app.sh does), https-only, TLSv1.2, read timeout 60 s, keep-alive 60 s.
#   Default behaviour: all seven methods, redirect-to-https, cache policy Managed-CachingDisabled (answers stream and depend
#   on the visitor), origin request policy Managed-AllViewerExceptHostHeader (every viewer header, cookie and query string
#   EXCEPT Host: Caddy answers only for its own name), compression on.
#   /downloads/*: GET and HEAD, Managed-CachingOptimized, the same origin request policy.
#   HTTP/2 and HTTP/3, IPv6, all edge locations (price class all).
# The managed policies are looked up BY NAME at run time; the ids read on 14 Nov are only the dry-run stand-ins.
# The old TMKG_TLS=cloudfront mode of aws/deploy_app.sh (its step 4) is not used and not changed.
# Visitors' addresses: user_data.sh.tmpl (PK14 A1b) makes Caddy trust X-Forwarded-For from CloudFront's origin-facing ranges.
# Usage: bash aws/cloudfront_front.sh [--dry-run]            create it once; a re-run reports the existing one
#        bash aws/cloudfront_front.sh --status [--dry-run]   read-only: id, domain, Status, the two policy names; exit 0 only
#                                                            when it is Deployed and enabled
# Removal: aws/teardown.sh (matches the comment 'tmkg front').
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
STATUS=0
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  case "$a" in
    --status) STATUS=1 ;;
    *) echo "unknown argument: $a (usage: bash aws/cloudfront_front.sh [--dry-run] [--status])" >&2; exit 2 ;;
  esac
done
COMMENT="tmkg front"
CALLER_REF="tmkg-front"
# dry-run stand-ins: the managed policy ids read from the account on 14 Nov (the real run reads them again by name)
DRY_CACHING_DISABLED="4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
DRY_CACHING_OPTIMIZED="658327ea-f89d-4fab-a63d-7e88639e58f6"
DRY_ALL_VIEWER_EXCEPT_HOST="b689b0a8-53d0-40ab-baf2-68738e2966ac"

# find_distribution: prints "ID<TAB>DOMAIN<TAB>STATUS" of the project's front distribution (comment AND tag), or nothing;
# stops the script when there is more than one.
find_distribution() {
  local listing placeholder="None" id arn domain status tag found=()
  if [ "$STATUS" = 1 ]; then
    placeholder="E2FRONTEXAMPLE"$'\t'"arn:aws:cloudfront::${ACCOUNT_ID}:distribution/E2FRONTEXAMPLE"$'\t'"d111111abcdef8.cloudfront.net"$'\t'"Deployed"
  fi
  listing="$(awsx_cap "$placeholder" cloudfront list-distributions \
    --query "DistributionList.Items[?Comment=='${COMMENT}'].[Id,ARN,DomainName,Status]" --output text)"
  while IFS=$'\t' read -r id arn domain status; do
    if [ -z "$id" ] || [ "$id" = None ]; then continue; fi
    tag="$(awsx_cap "$TAG_VALUE" cloudfront list-tags-for-resource --resource "$arn" \
      --query "Tags.Items[?Key=='${TAG_KEY}'].Value | [0]" --output text)"
    if [ "$tag" = "$TAG_VALUE" ]; then
      found+=("${id}"$'\t'"${domain}"$'\t'"${status}")
    else
      echo "# distribution ${id} has the comment '${COMMENT}' but not the tag ${TAG_KEY}=${TAG_VALUE}: not ours, left alone" >&2
    fi
  done <<< "$listing"
  if [ "${#found[@]}" -gt 1 ]; then
    echo "more than one distribution '${COMMENT}' tagged ${TAG_KEY}=${TAG_VALUE}: ${found[*]}; remove the extra one first" >&2
    exit 1
  fi
  if [ "${#found[@]}" = 1 ]; then printf '%s\n' "${found[0]}"; fi
}

policy_name() {   # policy_name cache|origin-request ID: the policy's name (read-only)
  if [ "$1" = cache ]; then
    awsx_cap "Managed-CachingDisabled" cloudfront get-cache-policy --id "$2" --query CachePolicy.CachePolicyConfig.Name --output text
  else
    awsx_cap "Managed-AllViewerExceptHostHeader" cloudfront get-origin-request-policy --id "$2" \
      --query OriginRequestPolicy.OriginRequestPolicyConfig.Name --output text
  fi
}

if [ "$STATUS" = 1 ]; then
  FOUND="$(find_distribution)"
  if [ -z "$FOUND" ]; then
    echo "distribution '${COMMENT}': MISSING (create it: bash aws/cloudfront_front.sh)"
    exit 1
  fi
  IFS=$'\t' read -r ID DOMAIN _ <<< "$FOUND"
  IFS=$'\t' read -r STATE ENABLED ORIGIN_NOW CP ORP <<< "$(awsx_cap "Deployed"$'\t'"True"$'\t'"tmkg.3-25-0-1.sslip.io"$'\t'"${DRY_CACHING_DISABLED}"$'\t'"${DRY_ALL_VIEWER_EXCEPT_HOST}" \
    cloudfront get-distribution --id "$ID" --query "Distribution.[Status,DistributionConfig.Enabled,DistributionConfig.Origins.Items[0].DomainName,DistributionConfig.DefaultCacheBehavior.CachePolicyId,DistributionConfig.DefaultCacheBehavior.OriginRequestPolicyId]" \
    --output text)"
  echo "id:      ${ID}"
  echo "domain:  https://${DOMAIN}/"
  echo "origin:  ${ORIGIN_NOW}"
  echo "status:  ${STATE} (enabled ${ENABLED})"
  echo "default behaviour: cache policy $(policy_name cache "$CP"), origin request policy $(policy_name origin-request "$ORP")"
  if [ "$STATE" = Deployed ] && [ "$ENABLED" = True ]; then exit 0; fi
  echo "not ready: a new or changed distribution takes a few minutes to deploy (Status InProgress)"
  exit 1
fi

# --- the origin: the Caddy name of the project's Elastic IP (aws/deploy_app.sh, caddy front door)
EIP="$(awsx_cap "3.25.0.1" ec2 describe-addresses --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" Name=tag:Name,Values=tmkg-app \
  --query 'Addresses[0].PublicIp' --output text)"
if ! [[ "$EIP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "no Elastic IP tagged ${TAG_KEY}=${TAG_VALUE}, Name=tmkg-app: launch the Caddy site first (TMKG_TLS=caddy bash aws/deploy_app.sh)" >&2
  exit 1
fi
ORIGIN="tmkg.${EIP//./-}.sslip.io"
echo "# origin: ${ORIGIN} (the Caddy front door; it stays the fallback link https://${ORIGIN}/)"

FOUND="$(find_distribution)"
if [ -n "$FOUND" ]; then
  IFS=$'\t' read -r ID DOMAIN STATE <<< "$FOUND"
  ORIGIN_NOW="$(awsx_cap "$ORIGIN" cloudfront get-distribution-config --id "$ID" \
    --query 'DistributionConfig.Origins.Items[0].DomainName' --output text)"
  if [ "$ORIGIN_NOW" != "$ORIGIN" ]; then
    echo "distribution ${ID} exists but its origin is ${ORIGIN_NOW}, not ${ORIGIN}: change it in the console or remove it (aws/teardown.sh) first" >&2
    exit 1
  fi
  echo "exists: distribution ${ID} (${STATE}); nothing created"
  echo "Amazon CloudFront: https://${DOMAIN}/   (fallback: https://${ORIGIN}/). Check: bash aws/cloudfront_front.sh --status"
  exit 0
fi

# --- the managed policies, by name
lookup_cache() {
  awsx_cap "$2" cloudfront list-cache-policies --type managed \
    --query "CachePolicyList.Items[?CachePolicy.CachePolicyConfig.Name=='$1'].CachePolicy.Id | [0]" --output text
}
CACHING_DISABLED="$(lookup_cache Managed-CachingDisabled "$DRY_CACHING_DISABLED")"
CACHING_OPTIMIZED="$(lookup_cache Managed-CachingOptimized "$DRY_CACHING_OPTIMIZED")"
ALL_VIEWER_EXCEPT_HOST="$(awsx_cap "$DRY_ALL_VIEWER_EXCEPT_HOST" cloudfront list-origin-request-policies --type managed \
  --query "OriginRequestPolicyList.Items[?OriginRequestPolicy.OriginRequestPolicyConfig.Name=='Managed-AllViewerExceptHostHeader'].OriginRequestPolicy.Id | [0]" \
  --output text)"
for v in "$CACHING_DISABLED" "$CACHING_OPTIMIZED" "$ALL_VIEWER_EXCEPT_HOST"; do
  if ! [[ "$v" =~ ^[0-9a-f-]{36}$ ]]; then echo "a managed CloudFront policy was not found by name (got '$v')" >&2; exit 1; fi
done

# --- the distribution
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
CFG="${WORK}/distribution.json"
python3 - "$CFG" "$ORIGIN" "$CACHING_DISABLED" "$CACHING_OPTIMIZED" "$ALL_VIEWER_EXCEPT_HOST" "$TAG_KEY" "$TAG_VALUE" \
  "$CALLER_REF" "$COMMENT" <<'PY'
import json, sys
out, origin, disabled, optimized, all_viewer, tag_key, tag_value, ref, comment = sys.argv[1:]
methods = lambda items: {"Quantity": len(items), "Items": items,
                         "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}}
behaviour = lambda cache, allowed: {"TargetOriginId": "tmkg-caddy", "ViewerProtocolPolicy": "redirect-to-https",
                                    "AllowedMethods": methods(allowed), "CachePolicyId": cache,
                                    "OriginRequestPolicyId": all_viewer, "Compress": True}
downloads = dict(behaviour(optimized, ["GET", "HEAD"]), PathPattern="/downloads/*")
config = {
    "CallerReference": ref, "Comment": comment, "Enabled": True,
    "PriceClass": "PriceClass_All", "HttpVersion": "http2and3", "IsIPV6Enabled": True,
    "Origins": {"Quantity": 1, "Items": [{"Id": "tmkg-caddy", "DomainName": origin, "CustomOriginConfig": {
        "HTTPPort": 80, "HTTPSPort": 443, "OriginProtocolPolicy": "https-only",
        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
        "OriginReadTimeout": 60, "OriginKeepaliveTimeout": 60}}]},
    "DefaultCacheBehavior": behaviour(disabled, ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]),
    "CacheBehaviors": {"Quantity": 1, "Items": [downloads]},
    "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
}
tags = {"Items": [{"Key": tag_key, "Value": tag_value}, {"Key": "Name", "Value": "tmkg-front"}]}
with open(out, "w") as f:
    f.write(json.dumps({"DistributionConfig": config, "Tags": tags}, indent=1, sort_keys=True) + "\n")
PY
if [ "$DRY_RUN" = 1 ]; then
  echo "# distribution config (file://${CFG}):"
  sed 's/^/#   /' "$CFG"
fi
CREATED="$(awsx_cap "E2FRONTEXAMPLE"$'\t'"d111111abcdef8.cloudfront.net" cloudfront create-distribution-with-tags \
  --distribution-config-with-tags "file://${CFG}" --query 'Distribution.[Id,DomainName]' --output text)" || {
  echo "create-distribution-with-tags failed. DistributionAlreadyExists = a distribution with CallerReference ${CALLER_REF} exists" \
       "but lost its comment or tag: look in the console, do not create another one." >&2
  exit 1
}
IFS=$'\t' read -r ID DOMAIN <<< "$CREATED"
echo "created: distribution ${ID} (deploying; about 5 to 15 minutes)."
echo "Amazon CloudFront: https://${DOMAIN}/   (fallback: https://${ORIGIN}/). Check: bash aws/cloudfront_front.sh --status"
