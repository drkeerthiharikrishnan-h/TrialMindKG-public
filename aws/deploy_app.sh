#!/usr/bin/env bash
# STEP 6: the hosted app. One EC2 instance (APP_INSTANCE_TYPE, default t3.large; APP_DISK_GB, default 50) running aws/Dockerfile, HTTPS through a
# CloudFront distribution in front (the instance accepts port 80 only from CloudFront's prefix list), no ssh (Session Manager),
# NIM key from SSM. The big files go to S3 first: the code as a tarball of the tracked files and, under app/data/, the files the
# app reads (card index, predictions, deliverables).
#   S3 layout: s3://$TMKG_BUCKET/app/app-bundle.tgz   s3://$TMKG_BUCKET/app/data/{data/kg/pharos_index/card_index.sqlite,outputs/...}
# Run aws/budget_alarms.sh, aws/iam_setup.sh and aws/nim_key_ssm.sh first. Prints the CloudFront URL for loop/state.yaml.
# Usage: bash aws/deploy_app.sh [--dry-run]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
# t3.large (8 GB) / 50 GB: CC-21 measured one request at 0.54 GB peak memory, but a broad question can take several hundred MB and
# a few at once could exhaust the 4 GB of a t3.medium (owner decision 11 Nov 2026). The card index is 20.3 GB on disk.
# aws/config.env is sourced by init above and sets APP_INSTANCE_TYPE: that line, not this default, decides a normal launch.
INSTANCE_TYPE="${APP_INSTANCE_TYPE:-t3.large}"
DISK_GB="${APP_DISK_GB:-50}"
TAG_SPEC="ResourceType=instance,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}},{Key=Name,Value=tmkg-app}]"
cd "$ROOT_DIR"

# --- 1. bucket (private, tagged) and the bundle
# TMKG_SKIP_S3_STEP=1: the bucket exists and the files are already in S3 (bundle and small files by boto3, the card index as
# zstd parts under app/index/ by the owner's curl upload; user_data.sh.tmpl decompresses them), so go straight to step 2.
if [ "${TMKG_SKIP_S3_STEP:-0}" = 1 ]; then
  echo "# step 1 skipped (TMKG_SKIP_S3_STEP=1): bucket and files already in s3://${TMKG_BUCKET}/app/"
else
LOCATION=()
if [ "$AWS_REGION" != "us-east-1" ]; then LOCATION=(--create-bucket-configuration "LocationConstraint=${AWS_REGION}"); fi
run aws s3api create-bucket --bucket "$TMKG_BUCKET" --region "$AWS_REGION" "${LOCATION[@]+"${LOCATION[@]}"}"
run aws s3api put-public-access-block --bucket "$TMKG_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
run aws s3api put-bucket-tagging --bucket "$TMKG_BUCKET" --tagging "TagSet=[{Key=${TAG_KEY},Value=${TAG_VALUE}}]"
BUNDLE="$(mktemp -d)/app-bundle.tgz"
run git archive --format=tar.gz --output="$BUNDLE" HEAD
run aws s3 cp "$BUNDLE" "s3://${TMKG_BUCKET}/app/app-bundle.tgz" --region "$AWS_REGION"
for f in data/kg/pharos_index/card_index.sqlite outputs/linkpred/predictions.tsv.gz outputs/linkpred/predictions_metapath.tsv.gz \
         outputs/linkpred/*.json outputs/linkpred/*.md outputs/pharos/prune_calibration.json; do   # CS-1: the Numbers page reads /data
  if [ -f "$f" ] || [ "$DRY_RUN" = 1 ]; then
    run aws s3 cp "$f" "s3://${TMKG_BUCKET}/app/data/${f}" --region "$AWS_REGION"
  else
    echo "# skipped (not built): $f"
  fi
done
fi

# --- 2. network: the instance takes port 80 only from CloudFront (default), or 80/443 from anywhere with TMKG_TLS=caddy
# (Caddy on the instance terminates HTTPS for <public-ip>.sslip.io; used while CloudFront needs AWS account verification).
TLS="${TMKG_TLS:-cloudfront}"
case "$TLS" in cloudfront | caddy) ;; *) echo "TMKG_TLS must be cloudfront or caddy, not '$TLS'" >&2; exit 2 ;; esac
export TMKG_TLS="$TLS"
# TMKG_ACME_EMAIL (optional, caddy only): rendered into the Caddyfile so ZeroSSL can stand in for Let's Encrypt.
if [ -n "${TMKG_ACME_EMAIL:-}" ] && ! [[ "$TMKG_ACME_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+$ ]]; then
  echo "TMKG_ACME_EMAIL is not a plain e-mail address: '$TMKG_ACME_EMAIL'" >&2
  exit 2
fi
VPC_ID="$(awsx_cap vpc-0example ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
SG_ID="$(awsx_cap None ec2 describe-security-groups --filters Name=group-name,Values=tmkg-app "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [ -z "$SG_ID" ] || [ "$SG_ID" = None ]; then
  SG_ID="$(awsx_cap sg-0app ec2 create-security-group --group-name tmkg-app --description "tmkg app front door" \
    --vpc-id "$VPC_ID" --tag-specifications "ResourceType=security-group,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}}]" \
    --query GroupId --output text)"
else
  echo "# reusing security group tmkg-app ($SG_ID)"
fi
ingress() {   # add one rule; a rule that already exists is fine (re-run), any other error stops the script
  local err
  if [ "$DRY_RUN" = 1 ]; then awsx ec2 authorize-security-group-ingress --group-id "$SG_ID" --ip-permissions "$1"; return; fi
  show aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --ip-permissions "$1" --region "$AWS_REGION"
  if ! err="$(aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --ip-permissions "$1" --region "$AWS_REGION" 2>&1)"; then
    case "$err" in *InvalidPermission.Duplicate*) echo "# rule exists: $1" ;; *) echo "$err" >&2; return 1 ;; esac
  fi
}
if [ "$TLS" = caddy ]; then
  ingress "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0,Description=caddy-acme-and-redirect}]"
  ingress "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0,Description=caddy-https}]"
else
  PL_ID="$(awsx_cap pl-0cloudfront ec2 describe-managed-prefix-lists \
    --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing --query 'PrefixLists[0].PrefixListId' --output text)"
  ingress "IpProtocol=tcp,FromPort=80,ToPort=80,PrefixListIds=[{PrefixListId=${PL_ID}}]"
fi

# --- 3. the instance
AMI="$(awsx_cap ami-0example ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)"
# caddy front door: one Elastic IP tagged for the project (allocated once, reused on every relaunch), so the link
# https://<eip>.sslip.io/ stays the same; its name is rendered into the user data before the instance boots.
EIP_ALLOC=""
if [ "$TLS" = caddy ]; then
  read -r EIP_ALLOC EIP_IP <<< "$(awsx_cap "None None" ec2 describe-addresses \
    --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" Name=tag:Name,Values=tmkg-app \
    --query 'Addresses[0].[AllocationId,PublicIp]' --output text)"
  if [ -z "$EIP_ALLOC" ] || [ "$EIP_ALLOC" = None ]; then
    read -r EIP_ALLOC EIP_IP <<< "$(awsx_cap "eipalloc-0example 3.25.0.1" ec2 allocate-address --domain vpc \
      --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}},{Key=Name,Value=tmkg-app}]" \
      --query '[AllocationId,PublicIp]' --output text)"
  else
    echo "# reusing Elastic IP ${EIP_IP} (${EIP_ALLOC})"
  fi
  export TMKG_PUBLIC_HOST="tmkg.${EIP_IP//./-}.sslip.io"   # 9 Nov: "tmkg." prefix = a new name for Let's Encrypt after 5 certs/week on the bare one
fi
# PK14 A2: render() turns these two into NEPTUNE_HOST / NEPTUNE_WAKE_MIN (empty endpoint = the boot script of before)
export NEPTUNE_ENDPOINT="${NEPTUNE_ENDPOINT:-}" NEPTUNE_WAKE_MINUTES="${NEPTUNE_WAKE_MINUTES:-}"
USER_DATA="$(render_tmp "$AWS_DIR/user_data.sh.tmpl")"
INSTANCE_ID="$(awsx_cap i-0example ec2 run-instances --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
  --iam-instance-profile Name=tmkg-app --security-group-ids "$SG_ID" --user-data "file://${USER_DATA}" \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=${DISK_GB},VolumeType=gp3,Encrypted=true}" \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=1 --tag-specifications "$TAG_SPEC" \
  "ResourceType=volume,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}}]" --query 'Instances[0].InstanceId' --output text)"
rm -f "$USER_DATA"
awsx ec2 wait instance-running --instance-ids "$INSTANCE_ID"
ORIGIN="$(awsx_cap ec2-0-0-0-0.compute-1.amazonaws.com ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicDnsName' --output text)"

if [ "$TLS" = caddy ]; then
  awsx ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC" --allow-reassociation
  echo "Hosted app: https://${TMKG_PUBLIC_HOST}/   (Caddy + Let's Encrypt on the instance; first boot ~5-10 min). Write it into loop/state.yaml (hosted_url)."
  echo "The link stays the same across relaunches (Elastic IP ${EIP_IP}); downloads: https://${TMKG_PUBLIC_HOST}/downloads/"
  exit 0
fi

# --- 4. CloudFront in front: HTTPS for viewers, HTTP to the instance, no caching (the answers stream)
CFG="$(mktemp)"
cat > "$CFG" <<JSON
{"CallerReference": "tmkg-app-${INSTANCE_ID}", "Comment": "tmkg app", "Enabled": true,
 "Origins": {"Quantity": 1, "Items": [{"Id": "app", "DomainName": "${ORIGIN}",
   "CustomOriginConfig": {"HTTPPort": 80, "HTTPSPort": 443, "OriginProtocolPolicy": "http-only",
                          "OriginReadTimeout": 60}}]},
 "DefaultCacheBehavior": {"TargetOriginId": "app", "ViewerProtocolPolicy": "redirect-to-https",
   "AllowedMethods": {"Quantity": 7, "Items": ["GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE"],
                      "CachedMethods": {"Quantity": 2, "Items": ["GET","HEAD"]}},
   "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
   "OriginRequestPolicyId": "216adef6-5c7f-47e4-b989-5492eafa07d3"}}
JSON
DIST="$(awsx_cap "d111111abcdef8.cloudfront.net" cloudfront create-distribution-with-tags \
  --distribution-config-with-tags "{\"DistributionConfig\": $(tr -d '\n' < "$CFG"), \"Tags\": {\"Items\": [{\"Key\": \"${TAG_KEY}\", \"Value\": \"${TAG_VALUE}\"}]}}" \
  --query Distribution.DomainName --output text)"
rm -f "$CFG"
echo "Hosted app: https://${DIST}/   (first boot takes a few minutes: docker build). Write it into loop/state.yaml (hosted_url)."
echo "Stop it between sessions with aws/schedule_stop_start.sh; remove everything with aws/teardown.sh."
