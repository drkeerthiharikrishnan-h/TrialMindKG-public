#!/usr/bin/env bash
# Idle stop for the Amazon Neptune cluster tmkg-neptune (PK14 A4), as the organizers' cost guidance asks: "set a CloudWatch
# alarm on query activity (e.g., no queries for 60 minutes) that triggers a small Lambda function to stop the graph or cluster".
#   function  tmkg-idle-stop (aws/lambda/idle_stop.py, python3.12, role tmkg-idle-stop, 30 s): stops the cluster only when it
#             is available, has run for the whole idle window and saw no query in it (see the module's docstring)
#   alarm     tmkg-neptune-idle: Sum of NEPTUNE_ACTIVITY_METRIC (default TotalRequestsPerSec) <= 0 for 12 periods of 300 s,
#             missing data = breaching, action = the function (allowed by a resource policy scoped to this alarm's ARN)
#   sweep     tmkg-neptune-idle-sweep in the Scheduler group tmkg, rate(30 minutes), invokes the function through the role
#             tmkg-scheduler: an alarm acts only on a state CHANGE, so a cluster woken and never queried would otherwise run on
#             until the nightly stop (aws/schedule_stop_start.sh stays the backstop).
# Before: aws/iam_setup.sh (role tmkg-idle-stop; tmkg-scheduler may invoke the function). Tags project=trialmindkg on the
# function, the alarm and the schedule group (EventBridge schedules themselves take no tags).
# The metric names are NOT verified (no cluster existed when this was written): after the cluster exists, run
#   aws cloudwatch list-metrics --namespace AWS/Neptune --region <region>
# and set NEPTUNE_ACTIVITY_METRIC / NEPTUNE_UPTIME_METRIC in aws/config.env if they differ; re-run this script to apply them.
# Usage: bash aws/neptune_idle_stop.sh [--dry-run]          create, or update in place (re-runs are safe)
#        bash aws/neptune_idle_stop.sh --status [--dry-run] read-only; exit 0 only when the function, the alarm with its action,
#                                                           the permission and the enabled sweep all exist
#        bash aws/neptune_idle_stop.sh --pause [--dry-run]  alarm actions off + sweep DISABLED: run it BEFORE a bulk load (a
#                                                           load is not query activity, the function would stop the cluster)
#        bash aws/neptune_idle_stop.sh --resume [--dry-run] alarm actions on + sweep ENABLED (after the load)
#        bash aws/neptune_idle_stop.sh --remove [--dry-run] delete the sweep, the permission, the alarm, the function, its log group
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
MODE=deploy
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  case "$a" in
    --status) MODE=status ;;
    --pause) MODE=pause ;;
    --resume) MODE=resume ;;
    --remove) MODE=remove ;;
    *) echo "unknown argument: $a (usage: bash aws/neptune_idle_stop.sh [--dry-run] [--status|--pause|--resume|--remove])" >&2; exit 2 ;;
  esac
done
CLUSTER="tmkg-neptune"
FN="tmkg-idle-stop"
ALARM="tmkg-neptune-idle"
SWEEP="tmkg-neptune-idle-sweep"
STATEMENT_ID="tmkg-neptune-idle-alarm"
LOG_GROUP="/aws/lambda/${FN}"
IDLE_MINUTES="${NEPTUNE_IDLE_MINUTES:-60}"
ACTIVITY="${NEPTUNE_ACTIVITY_METRIC:-TotalRequestsPerSec}"
UPTIME="${NEPTUNE_UPTIME_METRIC:-CPUUtilization}"
if ! [[ "$IDLE_MINUTES" =~ ^[0-9]+$ ]] || [ $((IDLE_MINUTES % 5)) != 0 ] || [ "$IDLE_MINUTES" -lt 5 ]; then
  echo "NEPTUNE_IDLE_MINUTES must be a multiple of 5 minutes, not '$IDLE_MINUTES'" >&2
  exit 2
fi
for m in "$ACTIVITY" "$UPTIME"; do
  if ! [[ "$m" =~ ^[A-Za-z0-9]+$ ]]; then echo "a Neptune metric name is letters and digits only, not '$m'" >&2; exit 2; fi
done
PERIODS=$((IDLE_MINUTES / 5))
FN_ARN="arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${FN}"
ALARM_ARN="arn:aws:cloudwatch:${AWS_REGION}:${ACCOUNT_ID}:alarm:${ALARM}"
FN_ROLE="arn:aws:iam::${ACCOUNT_ID}:role/tmkg-idle-stop"
SCHED_ROLE="arn:aws:iam::${ACCOUNT_ID}:role/tmkg-scheduler"
TAGS="Key=${TAG_KEY},Value=${TAG_VALUE}"

# probe PATTERN CMD...: run a read (shown on stderr). Prints its output; prints '@absent' when the error contains PATTERN
# (the resource is not there); any other error stops the script. In a dry run nothing is called and it prints '@dry'.
probe() {
  local pattern="$1" out
  shift
  show "$@" >&2
  if [ "$DRY_RUN" = 1 ]; then echo "@dry"; return 0; fi
  if out="$("$@" 2>&1)"; then printf '%s\n' "$out"; return 0; fi
  case "$out" in
    *"$pattern"*) echo "@absent" ;;
    *) printf '%s\n' "$out" >&2; exit 1 ;;
  esac
}

# soft PATTERN NOTE CMD...: run CMD (shown; skipped in a dry run); an error containing PATTERN prints NOTE and the script goes
# on (an exists / not-found answer on a re-run); any other error stops the script.
soft() {
  local pattern="$1" note="$2" err
  shift 2
  show "$@"
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  if err="$("$@" 2>&1 >/dev/null)"; then return 0; fi
  case "$err" in
    *"$pattern"*) echo "$note" ;;
    *) printf '%s\n' "$err" >&2; exit 1 ;;
  esac
}

# put_alarm on|off: create or overwrite the idle alarm (the same definition every time; only whether its action runs changes).
# Tagging an alarm needs cloudwatch:TagResource on top of PutMetricAlarm: if the account refuses only that, the alarm is
# written without the tag and one line says so (teardown deletes it by name).
put_alarm() {
  local enabled=--actions-enabled err
  if [ "$1" = off ]; then enabled=--no-actions-enabled; fi
  local -a args=(aws cloudwatch put-metric-alarm --alarm-name "$ALARM"
    --alarm-description "TrialMindKG: no Amazon Neptune query for ${IDLE_MINUTES} minutes -> ${FN} stops ${CLUSTER} (aws/neptune_idle_stop.sh)"
    --namespace AWS/Neptune --metric-name "$ACTIVITY" --dimensions "Name=DBClusterIdentifier,Value=${CLUSTER}"
    --statistic Sum --period 300 --evaluation-periods "$PERIODS" --datapoints-to-alarm "$PERIODS" --threshold 0
    --comparison-operator LessThanOrEqualToThreshold --treat-missing-data breaching --alarm-actions "$FN_ARN" "$enabled"
    --region "$AWS_REGION")
  show "${args[@]}" --tags "$TAGS"
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  if err="$("${args[@]}" --tags "$TAGS" 2>&1 >/dev/null)"; then return 0; fi
  case "$err" in
    *TagResource*)
      echo "# the account refuses cloudwatch:TagResource: alarm ${ALARM} written without the project tag"
      run "${args[@]}" ;;
    *) printf '%s\n' "$err" >&2; exit 1 ;;
  esac
}

# put_sweep ENABLED|DISABLED: create the sweep schedule, or update it in place when it exists
put_sweep() {
  local state="$1" verb=create-schedule found
  local target
  target="$(printf '{"Arn":"%s","RoleArn":"%s","Input":"{\\"source\\": \\"%s\\"}","RetryPolicy":{"MaximumRetryAttempts":0}}' \
    "$FN_ARN" "$SCHED_ROLE" "$SWEEP")"
  found="$(probe ResourceNotFound aws scheduler get-schedule --group-name tmkg --name "$SWEEP" --query State --output text \
    --region "$AWS_REGION")"
  case "$found" in
    @absent) verb=create-schedule ;;
    @dry) echo "# create-schedule when ${SWEEP} is absent, update-schedule (same arguments) when it exists" ;;
    *) verb=update-schedule ;;
  esac
  run aws scheduler "$verb" --group-name tmkg --name "$SWEEP" --schedule-expression "rate(30 minutes)" \
    --flexible-time-window Mode=OFF --state "$state" \
    --description "TrialMindKG: let ${FN} check every 30 minutes whether ${CLUSTER} has been idle for ${IDLE_MINUTES} minutes" \
    --target "$target" --region "$AWS_REGION"
}

if [ "$MODE" = status ]; then
  BAD=0
  FN_STATE="$(probe ResourceNotFound aws lambda get-function --function-name "$FN" --query 'Configuration.[State,Runtime]' \
    --output text --region "$AWS_REGION")"
  POLICY="$(probe ResourceNotFound aws lambda get-policy --function-name "$FN" --query Policy --output text --region "$AWS_REGION")"
  ALARM_STATE="$(probe "@never" aws cloudwatch describe-alarms --alarm-names "$ALARM" \
    --query "MetricAlarms[0].[ActionsEnabled,StateValue,MetricName,join(',',AlarmActions)]" --output text --region "$AWS_REGION")"
  SWEEP_STATE="$(probe ResourceNotFound aws scheduler get-schedule --group-name tmkg --name "$SWEEP" \
    --query '[State,ScheduleExpression,Target.Arn]' --output text --region "$AWS_REGION")"
  if [ "$DRY_RUN" = 1 ]; then
    FN_STATE=$'Active\tpython3.12'
    POLICY="{\"Statement\":[{\"Sid\":\"${STATEMENT_ID}\",\"Condition\":{\"ArnLike\":{\"AWS:SourceArn\":\"${ALARM_ARN}\"}}}]}"
    ALARM_STATE="True"$'\t'"OK"$'\t'"${ACTIVITY}"$'\t'"${FN_ARN}"
    SWEEP_STATE="ENABLED"$'\t'"rate(30 minutes)"$'\t'"${FN_ARN}"
  fi
  # the reads are tab-separated (--output text); a schedule expression has a space in it
  IFS=$'\t' read -r S_STATE S_RUNTIME <<< "$FN_STATE"
  if [ "$S_STATE" = Active ]; then echo "function ${FN}: Active (${S_RUNTIME:-})"; else echo "function ${FN}: MISSING or not active (${FN_STATE})"; BAD=1; fi
  case "$POLICY" in
    *"$STATEMENT_ID"*"$ALARM_ARN"* | *"$ALARM_ARN"*"$STATEMENT_ID"*) echo "permission: the alarm ${ALARM} may invoke ${FN}" ;;
    *) echo "permission: MISSING (the alarm cannot invoke ${FN})"; BAD=1 ;;
  esac
  IFS=$'\t' read -r A_ENABLED A_STATE A_METRIC A_ACTIONS <<< "$ALARM_STATE"
  if [ "${A_ENABLED:-None}" = None ]; then
    echo "alarm ${ALARM}: MISSING"; BAD=1
  else
    echo "alarm ${ALARM}: state ${A_STATE}, metric ${A_METRIC}, actions enabled ${A_ENABLED}"
    if [ "$A_ENABLED" != True ]; then echo "  its action is OFF (paused? bash aws/neptune_idle_stop.sh --resume)"; BAD=1; fi
    case ",${A_ACTIONS}," in *",${FN_ARN},"*) ;; *) echo "  its action is not ${FN_ARN}"; BAD=1 ;; esac
  fi
  IFS=$'\t' read -r W_STATE W_EXPR W_TARGET <<< "$SWEEP_STATE"
  if [ "$W_STATE" = @absent ]; then
    echo "sweep ${SWEEP}: MISSING"; BAD=1
  else
    echo "sweep ${SWEEP}: ${W_STATE} ${W_EXPR:-}"
    if [ "$W_STATE" != ENABLED ]; then echo "  the sweep is not enabled (paused? bash aws/neptune_idle_stop.sh --resume)"; BAD=1; fi
    if [ "${W_TARGET:-}" != "$FN_ARN" ]; then echo "  its target is not ${FN_ARN}"; BAD=1; fi
  fi
  exit "$BAD"
fi

if [ "$MODE" = pause ] || [ "$MODE" = resume ]; then
  if [ "$MODE" = pause ]; then
    put_alarm off
    put_sweep DISABLED
    echo "Idle stop PAUSED: nothing stops ${CLUSTER} for idleness until: bash aws/neptune_idle_stop.sh --resume"
    echo "(the nightly stop schedule tmkg-neptune-stop still runs)"
  else
    put_alarm on
    put_sweep ENABLED
    echo "Idle stop resumed. Check: bash aws/neptune_idle_stop.sh --status"
  fi
  exit 0
fi

if [ "$MODE" = remove ]; then
  soft ResourceNotFound "# no sweep schedule ${SWEEP}" aws scheduler delete-schedule --group-name tmkg --name "$SWEEP" --region "$AWS_REGION"
  soft ResourceNotFound "# no permission ${STATEMENT_ID}" aws lambda remove-permission --function-name "$FN" \
    --statement-id "$STATEMENT_ID" --region "$AWS_REGION"
  awsx cloudwatch delete-alarms --alarm-names "$ALARM"
  soft ResourceNotFound "# no function ${FN}" aws lambda delete-function --function-name "$FN" --region "$AWS_REGION"
  soft ResourceNotFound "# no log group ${LOG_GROUP}" aws logs delete-log-group --log-group-name "$LOG_GROUP" --region "$AWS_REGION"
  echo "Idle stop removed (the role tmkg-idle-stop stays; aws/teardown.sh deletes it). The nightly stop still runs."
  exit 0
fi

# --- deploy: 1. the function
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ZIP="${WORK}/idle_stop.zip"
run python3 -m zipfile -c "$ZIP" "$AWS_DIR/lambda/idle_stop.py"
ENV_JSON="$(printf '{"Variables":{"CLUSTER_ID":"%s","IDLE_MINUTES":"%s","ACTIVITY_METRIC":"%s","UPTIME_METRIC":"%s"}}' \
  "$CLUSTER" "$IDLE_MINUTES" "$ACTIVITY" "$UPTIME")"
CONFIG_ARGS=(--runtime python3.12 --role "$FN_ROLE" --handler idle_stop.handler --timeout 30 --memory-size 128
  --description "TrialMindKG: stop ${CLUSTER} after ${IDLE_MINUTES} minutes without a query" --environment "$ENV_JSON")
FOUND="$(probe ResourceNotFound aws lambda get-function --function-name "$FN" --query Configuration.FunctionArn --output text \
  --region "$AWS_REGION")"
if [ "$FOUND" = @absent ] || [ "$FOUND" = @dry ]; then
  if [ "$FOUND" = @dry ]; then echo "# create-function when ${FN} is absent; update-function-code + update-function-configuration when it exists"; fi
  # a role created a few seconds ago may not be assumable yet: AWS answers "cannot be assumed"; try again for up to a minute
  for attempt in 1 2 3 4 5 6; do
    show aws lambda create-function --function-name "$FN" "${CONFIG_ARGS[@]}" --zip-file "fileb://${ZIP}" \
      --tags "${TAG_KEY}=${TAG_VALUE}" --region "$AWS_REGION"
    if [ "$DRY_RUN" = 1 ]; then break; fi
    if err="$(aws lambda create-function --function-name "$FN" "${CONFIG_ARGS[@]}" --zip-file "fileb://${ZIP}" \
        --tags "${TAG_KEY}=${TAG_VALUE}" --region "$AWS_REGION" 2>&1 >/dev/null)"; then break; fi
    case "$err" in
      *"cannot be assumed"*) if [ "$attempt" = 6 ]; then printf '%s\n' "$err" >&2; exit 1; fi
                             echo "# role tmkg-idle-stop not assumable yet (attempt ${attempt}); waiting 10 s"; sleep 10 ;;
      *) printf '%s\n' "$err" >&2; exit 1 ;;
    esac
  done
  awsx lambda wait function-active-v2 --function-name "$FN"
else
  awsx lambda update-function-code --function-name "$FN" --zip-file "fileb://${ZIP}"
  awsx lambda wait function-updated-v2 --function-name "$FN"
  awsx lambda update-function-configuration --function-name "$FN" "${CONFIG_ARGS[@]}"
  awsx lambda wait function-updated-v2 --function-name "$FN"
fi

# --- 2. the alarm, and the permission for exactly this alarm to invoke the function
put_alarm on
soft ResourceConflict "# exists: permission ${STATEMENT_ID}" aws lambda add-permission --function-name "$FN" \
  --statement-id "$STATEMENT_ID" --action lambda:InvokeFunction --principal lambda.alarms.cloudwatch.amazonaws.com \
  --source-arn "$ALARM_ARN" --source-account "$ACCOUNT_ID" --region "$AWS_REGION"

# --- 3. the sweep (the schedule group is the one aws/schedule_stop_start.sh uses; created here when absent)
soft ConflictException "# exists: schedule group tmkg" aws scheduler create-schedule-group --name tmkg --tags "$TAGS" --region "$AWS_REGION"
put_sweep ENABLED

echo "Idle stop is on: ${CLUSTER} stops after ${IDLE_MINUTES} minutes without a query (metric ${ACTIVITY}; uptime from ${UPTIME})."
echo "Check, read-only: bash aws/neptune_idle_stop.sh --status. Before a bulk load: --pause; after it: --resume."
