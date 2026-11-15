#!/usr/bin/env bash
# AFTER EVERY LAUNCH of the hosted app (aws/deploy_app.sh): the status alarm. One SNS topic (tmkg-app-status) in the app's
# region and one CloudWatch alarm (tmkg-app-status-check) on the EC2 status checks of the running instance tagged
# Name=tmkg-app. The alarm goes off when a status check (system or instance) fails for two minutes in a row, or when the
# instance stops reporting (stopped or terminated: missing data counts as failing), and reports again when it is back to OK.
# The alarm names ONE instance id and every relaunch makes a new instance, so run this again after each relaunch: the same
# alarm name is overwritten with the new instance id (nothing is duplicated). A relaunch therefore sends one ALARM message
# (old instance gone) and one OK message (new instance watched and passing).
# This script never subscribes anyone and never reads ALERT_EMAIL: the owner adds her address herself (console: SNS ->
# Topics -> tmkg-app-status -> Create subscription -> Email, then the link in the confirmation mail).
# What it does NOT see: the app or the front door failing on a healthy instance (container down, certificate, full disk).
# That is the job of the external monitor on GET /api/pharos/status; see docs/RUNBOOK_JUDGING.md.
# Usage: bash aws/status_alarm.sh [--dry-run]             create the topic, create or re-point the alarm
#        bash aws/status_alarm.sh --status [--dry-run]    read-only: exits 0 only if the alarm exists, watches the running
#                                                         instance and the topic has a confirmed subscription
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
TOPIC_NAME="tmkg-app-status"
ALARM_NAME="tmkg-app-status-check"
STATUS=0
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  case "$a" in
    --status) STATUS=1 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

# the one instance that is up (a relaunch leaves the old one in shutting-down / terminated, which this filter leaves out)
IDS="$(awsx_cap i-0example ec2 describe-instances \
  --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" Name=tag:Name,Values=tmkg-app Name=instance-state-name,Values=pending,running \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
read -r -a INSTANCES <<< "$IDS"
if [ "${#INSTANCES[@]}" -eq 0 ] || [ "${INSTANCES[0]}" = None ]; then
  echo "no pending or running instance tagged Name=tmkg-app in ${AWS_REGION}: launch the app first (aws/deploy_app.sh)" >&2
  exit 1
fi
if [ "${#INSTANCES[@]}" -gt 1 ]; then
  echo "more than one instance tagged Name=tmkg-app is up (${INSTANCES[*]}): terminate the old one first, then run this again" >&2
  exit 1
fi
INSTANCE_ID="${INSTANCES[0]}"
TOPIC_ARN="arn:aws:sns:${AWS_REGION}:${ACCOUNT_ID}:${TOPIC_NAME}"

if [ "$STATUS" = 1 ]; then
  read -r WATCHED STATE <<< "$(awsx_cap "i-0example OK" cloudwatch describe-alarms --alarm-names "$ALARM_NAME" \
    --query 'MetricAlarms[0].[Dimensions[0].Value,StateValue]' --output text)"
  read -r CONFIRMED PENDING <<< "$(awsx_cap "1 0" sns get-topic-attributes --topic-arn "$TOPIC_ARN" \
    --query 'Attributes.[SubscriptionsConfirmed,SubscriptionsPending]' --output text)"
  BAD=0
  echo "running instance:        ${INSTANCE_ID}"
  if [ -z "${WATCHED:-}" ] || [ "$WATCHED" = None ]; then
    echo "alarm ${ALARM_NAME}: MISSING (run: bash aws/status_alarm.sh)"
    BAD=1
  elif [ "$WATCHED" != "$INSTANCE_ID" ]; then
    echo "alarm ${ALARM_NAME}: STALE, it watches ${WATCHED} (run: bash aws/status_alarm.sh)"
    BAD=1
  else
    echo "alarm ${ALARM_NAME}: watches the running instance, state ${STATE:-unknown}"
  fi
  echo "topic ${TOPIC_NAME}: ${CONFIRMED:-0} confirmed subscription(s), ${PENDING:-0} waiting for confirmation"
  if [ "${CONFIRMED:-0}" = 0 ] || [ "${CONFIRMED:-0}" = None ]; then
    echo "nobody is told when the alarm goes off: subscribe an address in the console (SNS -> Topics -> ${TOPIC_NAME}) and confirm it"
    BAD=1
  fi
  exit "$BAD"
fi

# create-topic returns the existing topic when the name is taken (same tags), so a second run changes nothing here
TOPIC_ARN="$(awsx_cap "$TOPIC_ARN" sns create-topic --name "$TOPIC_NAME" \
  --tags "Key=${TAG_KEY},Value=${TAG_VALUE}" --query TopicArn --output text)"
# StatusCheckFailed = 1 when the system check or the instance check fails; EC2 reports it every minute at no charge
awsx cloudwatch put-metric-alarm --alarm-name "$ALARM_NAME" \
  --alarm-description "TrialMindKG hosted app: EC2 status check failing, or no data, on ${INSTANCE_ID}. What to do: docs/RUNBOOK_JUDGING.md, case A" \
  --namespace AWS/EC2 --metric-name StatusCheckFailed --dimensions "Name=InstanceId,Value=${INSTANCE_ID}" \
  --statistic Maximum --period 60 --evaluation-periods 2 --datapoints-to-alarm 2 --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data breaching \
  --alarm-actions "$TOPIC_ARN" --ok-actions "$TOPIC_ARN"
echo "Done: alarm ${ALARM_NAME} watches ${INSTANCE_ID} and reports to the topic ${TOPIC_NAME} (${AWS_REGION})."
echo "The owner subscribes her address in the console (SNS -> Topics -> ${TOPIC_NAME} -> Create subscription -> Email) and confirms it."
echo "Check any time, read-only: bash aws/status_alarm.sh --status"
