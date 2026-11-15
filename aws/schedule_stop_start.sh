#!/usr/bin/env bash
# STEP 7: nothing runs overnight by accident. EventBridge Scheduler (no Lambda needed) stops the Neptune cluster every evening
# and, with --ec2, the app instance; start schedules exist but are created disabled, for the judging window enable them or start
# by hand. Neptune restarts itself after 7 days when stopped: the stop schedule runs daily, so it is stopped again.
# The schedules live in the group "tmkg" (tagged). Times are UTC: STOP_CRON / START_CRON in aws/config.env.
# --status (read-only, PK14 A6): reads the four schedules BY NAME with get-schedule (list-schedules is denied for the deployer:
# the call is checked against schedule/*/*), prints name, state and expression; exits 0 when tmkg-neptune-stop is ENABLED.
# Usage: bash aws/schedule_stop_start.sh [--ec2] [--dry-run] | bash aws/schedule_stop_start.sh --status [--dry-run]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
WITH_EC2=0
STATUS=0
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  case "$a" in
    --ec2) WITH_EC2=1 ;;
    --status) STATUS=1 ;;
  esac
done
if [ "$STATUS" = 1 ]; then
  BAD=1
  for name in tmkg-neptune-stop tmkg-neptune-start tmkg-app-stop tmkg-app-start; do
    show aws scheduler get-schedule --group-name tmkg --name "$name" --query '[State,ScheduleExpression]' --output text \
      --region "$AWS_REGION"
    if [ "$DRY_RUN" = 1 ]; then
      case "$name" in *-stop) line="ENABLED"$'\t'"${STOP_CRON:-cron(0 18 * * ? *)}" ;; *) line="DISABLED"$'\t'"${START_CRON:-cron(0 6 * * ? *)}" ;; esac
    elif ! line="$(aws scheduler get-schedule --group-name tmkg --name "$name" --query '[State,ScheduleExpression]' \
        --output text --region "$AWS_REGION" 2>&1)"; then
      case "$line" in *ResourceNotFound*) line="absent" ;; *) printf '%s\n' "$line" >&2; exit 1 ;; esac
    fi
    IFS=$'\t' read -r state expr <<< "$line"
    printf '%-20s %-9s %s\n' "$name" "$state" "${expr:-}"
    if [ "$name" = tmkg-neptune-stop ] && [ "$state" = ENABLED ]; then BAD=0; fi
  done
  if [ "$BAD" = 1 ]; then echo "tmkg-neptune-stop is not ENABLED: nothing stops Amazon Neptune overnight (run: bash aws/schedule_stop_start.sh)"; fi
  exit "$BAD"
fi
STOP_CRON="${STOP_CRON:-cron(0 18 * * ? *)}"
START_CRON="${START_CRON:-cron(0 6 * * ? *)}"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/tmkg-scheduler"

awsx scheduler create-schedule-group --name tmkg --tags "Key=${TAG_KEY},Value=${TAG_VALUE}"

schedule() {   # schedule NAME CRON STATE SDK-TARGET INPUT
  run aws scheduler create-schedule --group-name tmkg --name "$1" --schedule-expression "$2" \
    --schedule-expression-timezone UTC --flexible-time-window Mode=OFF --state "$3" \
    --target "{\"Arn\":\"arn:aws:scheduler:::aws-sdk:$4\",\"RoleArn\":\"${ROLE_ARN}\",\"Input\":$(printf '%s' "$5" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}" \
    --region "$AWS_REGION"
}
schedule tmkg-neptune-stop "$STOP_CRON" ENABLED neptune:stopDBCluster '{"DbClusterIdentifier":"tmkg-neptune"}'
schedule tmkg-neptune-start "$START_CRON" DISABLED neptune:startDBCluster '{"DbClusterIdentifier":"tmkg-neptune"}'
if [ "$WITH_EC2" = 1 ]; then
  INSTANCE_ID="$(awsx_cap i-0example ec2 describe-instances --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" Name=tag:Name,Values=tmkg-app \
    Name=instance-state-name,Values=running,stopped --query 'Reservations[0].Instances[0].InstanceId' --output text)"
  schedule tmkg-app-stop "$STOP_CRON" ENABLED ec2:stopInstances "{\"InstanceIds\":[\"${INSTANCE_ID}\"]}"
  schedule tmkg-app-start "$START_CRON" DISABLED ec2:startInstances "{\"InstanceIds\":[\"${INSTANCE_ID}\"]}"
fi
echo "Stop schedules are on (${STOP_CRON} UTC); start schedules are created disabled. Enable one with:"
echo "  aws scheduler update-schedule --group-name tmkg --name tmkg-neptune-start ... --state ENABLED"
