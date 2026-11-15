#!/usr/bin/env bash
# LAST STEP, or any time: list and remove everything the project created. Everything is found by the tag project=trialmindkg
# (resourcegroupstaggingapi), then deleted in dependency order: CloudFront (disabled first), schedules, the Neptune idle stop
# (alarm, function, log group), EC2 and its security
# groups, Neptune (deletion protection off, no final snapshot unless --snapshot), SSM parameter, roles and instance profile,
# alarms/budget/topic, and the S3 bucket ONLY with --include-bucket (the deliverables live there: download them first).
# Usage: bash aws/teardown.sh --list | [--include-bucket] [--snapshot] [--yes] [--dry-run]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
LIST=0
BUCKET=0
SNAP=0
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  case "$a" in
    --list) LIST=1 ;;
    --include-bucket) BUCKET=1 ;;
    --snapshot) SNAP=1 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

echo "== resources tagged ${TAG_KEY}=${TAG_VALUE} in ${AWS_REGION}"
TAGGED="$(awsx_cap "arn:aws:ec2:${AWS_REGION}:${ACCOUNT_ID}:instance/i-0example" resourcegroupstaggingapi get-resources \
  --tag-filters "Key=${TAG_KEY},Values=${TAG_VALUE}" --query 'ResourceTagMappingList[].ResourceARN' --output text)"
echo "$TAGGED" | tr '\t' '\n'
if [ "$LIST" = 1 ]; then exit 0; fi
confirm "Delete all of the above (and the untagged billing alarms and tmkg-* roles)?" || { echo "aborted"; exit 1; }

# CloudFront: disable, wait until deployed, delete (a distribution must be disabled before it can be deleted)
disable_distribution() {   # disable_distribution ID BASE -> BASE.config (the config with Enabled=false), BASE.etag
  local id="$1" base="$2"
  show aws cloudfront get-distribution-config --id "$id"
  if [ "$DRY_RUN" = 1 ]; then
    echo '<etag>' > "${base}.etag"
    echo '{}' > "${base}.config"
    return 0
  fi
  aws cloudfront get-distribution-config --id "$id" --output json | python3 -c "
import json, sys
d = json.load(sys.stdin)
c = d['DistributionConfig']
c['Enabled'] = False
open(sys.argv[1] + '.config', 'w').write(json.dumps(c))
open(sys.argv[1] + '.etag', 'w').write(d['ETag'])" "$base"
}
# 'tmkg app' = the old deploy_app.sh step 4; 'tmkg front' = aws/cloudfront_front.sh (PK14 A1, in front of the Caddy site)
for id in $(awsx_cap "E1EXAMPLE E2FRONTEXAMPLE" cloudfront list-distributions \
    --query "DistributionList.Items[?Comment=='tmkg app' || Comment=='tmkg front'].Id" --output text); do
  if [ "$id" = None ]; then continue; fi        # no distribution at all: the CLI prints None
  BASE="$(mktemp)"
  disable_distribution "$id" "$BASE"
  run aws cloudfront update-distribution --id "$id" --if-match "$(cat "${BASE}.etag")" --distribution-config "file://${BASE}.config"
  run aws cloudfront wait distribution-deployed --id "$id"
  run aws cloudfront delete-distribution --id "$id" --if-match "$(awsx_cap "<etag>" cloudfront get-distribution --id "$id" --query ETag --output text)"
  rm -f "$BASE" "${BASE}.config" "${BASE}.etag"
done

# schedules (tmkg-neptune-idle-sweep: aws/neptune_idle_stop.sh)
for name in tmkg-neptune-stop tmkg-neptune-start tmkg-app-stop tmkg-app-start tmkg-neptune-idle-sweep; do
  awsx scheduler delete-schedule --group-name tmkg --name "$name" || true
done
awsx scheduler delete-schedule-group --name tmkg || true

# the idle stop (aws/neptune_idle_stop.sh): its alarm, the function and the function's log group (the role goes below)
awsx cloudwatch delete-alarms --alarm-names tmkg-neptune-idle || true
awsx lambda delete-function --function-name tmkg-idle-stop || true
awsx logs delete-log-group --log-group-name /aws/lambda/tmkg-idle-stop || true

# EC2
IDS="$(awsx_cap "i-0example" ec2 describe-instances --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" \
  Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)"
if [ -n "$IDS" ]; then
  # shellcheck disable=SC2086   # IDS is a space-separated list on purpose
  awsx ec2 terminate-instances --instance-ids $IDS
  # shellcheck disable=SC2086
  awsx ec2 wait instance-terminated --instance-ids $IDS
fi
# the app's Elastic IP (deploy_app.sh, caddy front door): an address left allocated keeps costing ~$3.6/month
for alloc in $(awsx_cap "eipalloc-0example" ec2 describe-addresses --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" \
    --query 'Addresses[].AllocationId' --output text); do
  awsx ec2 release-address --allocation-id "$alloc" || true
done

# Neptune
awsx neptune modify-db-cluster --db-cluster-identifier tmkg-neptune --no-deletion-protection --apply-immediately || true
awsx neptune delete-db-instance --db-instance-identifier tmkg-neptune-1 || true
if [ "$SNAP" = 1 ]; then
  awsx neptune delete-db-cluster --db-cluster-identifier tmkg-neptune --final-db-snapshot-identifier tmkg-neptune-final || true
else
  awsx neptune delete-db-cluster --db-cluster-identifier tmkg-neptune --skip-final-snapshot || true
fi
awsx neptune delete-db-subnet-group --db-subnet-group-name tmkg-neptune || true

# security groups and the S3 endpoint (after the instances and the cluster are gone)
for sg in $(awsx_cap "sg-0app sg-0neptune" ec2 describe-security-groups --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" \
    --query 'SecurityGroups[].GroupId' --output text); do
  awsx ec2 delete-security-group --group-id "$sg" || true
done
for ep in $(awsx_cap "vpce-0example" ec2 describe-vpc-endpoints --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" \
    --query 'VpcEndpoints[].VpcEndpointId' --output text); do
  awsx ec2 delete-vpc-endpoints --vpc-endpoint-ids "$ep"
done

# parameter, roles, profile
awsx ssm delete-parameter --name /trialmindkg/NVIDIA_API_KEY || true
run aws iam remove-role-from-instance-profile --instance-profile-name tmkg-app --role-name tmkg-app || true
run aws iam delete-instance-profile --instance-profile-name tmkg-app || true
for role in tmkg-app tmkg-neptune-s3-load tmkg-scheduler tmkg-idle-stop; do
  run aws iam delete-role-policy --role-name "$role" --policy-name "${role}-inline" || true
  run aws iam delete-role --role-name "$role" || true
done
for pol in tmkg-deployer-policy tmkg-deployer-policy-2; do
  run aws iam detach-user-policy --user-name tmkg-deployer --policy-arn "arn:aws:iam::${ACCOUNT_ID}:policy/${pol}" || true
  run aws iam delete-policy --policy-arn "arn:aws:iam::${ACCOUNT_ID}:policy/${pol}" || true
done

# the app's status alarm and its topic (aws/status_alarm.sh): they watch an instance that no longer exists
awsx cloudwatch delete-alarms --alarm-names tmkg-app-status-check || true
awsx sns delete-topic --topic-arn "arn:aws:sns:${AWS_REGION}:${ACCOUNT_ID}:tmkg-app-status" || true

# billing alarms stay unless you really want them gone: they cost nothing; remove with --yes and the lines below if needed.
# Names follow ALARM_USD exactly as budget_alarms.sh builds them (default "25 75 150"); on an account where SNS in us-east-1
# is denied the script made none, and the delete is a harmless no-op.
BILLING_ALARMS=()
for usd in ${ALARM_USD:-25 75 150}; do BILLING_ALARMS+=("tmkg-billing-${usd}usd"); done
run aws cloudwatch delete-alarms --alarm-names "${BILLING_ALARMS[@]}" --region us-east-1 || true
run aws budgets delete-budget --account-id "$ACCOUNT_ID" --budget-name tmkg-monthly || true
run aws sns delete-topic --topic-arn "arn:aws:sns:us-east-1:${ACCOUNT_ID}:tmkg-billing" --region us-east-1 || true

if [ "$BUCKET" = 1 ]; then
  echo "== bucket ${TMKG_BUCKET} (deliverables!)"
  confirm "Empty and delete s3://${TMKG_BUCKET}?" || { echo "bucket kept"; exit 0; }
  run aws s3 rb "s3://${TMKG_BUCKET}" --force --region "$AWS_REGION"
else
  echo "S3 bucket ${TMKG_BUCKET} kept (use --include-bucket to delete it)."
fi
echo "Run bash aws/teardown.sh --list to check that nothing tagged ${TAG_KEY}=${TAG_VALUE} is left."
