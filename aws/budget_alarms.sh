#!/usr/bin/env bash
# STEP 1, before any other resource: billing alarms. An SNS topic that emails ALERT_EMAIL, one CloudWatch
# EstimatedCharges alarm per threshold in ALARM_USD (default "25 75 150", MASTER_PLAN section 5) and one monthly
# AWS Budget of BUDGET_USD (default 150) that mails at 50% and 100% of actual spend.
# Billing metrics exist only in us-east-1, so this script ignores AWS_REGION. Billing alerts must be switched on once in
# the console (Billing -> Billing preferences -> "Receive CloudWatch billing alerts") and the e-mail subscription confirmed.
# If SNS in us-east-1 is denied (organization policy) it creates only the budget and says so.
# Usage: bash aws/budget_alarms.sh [--dry-run]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
: "${ALERT_EMAIL:?set ALERT_EMAIL in aws/config.env}"
ALARM_USD="${ALARM_USD:-25 75 150}"
BUDGET_USD="${BUDGET_USD:-150}"
BILLING_REGION="us-east-1"

# Some accounts (an organization service control policy) deny SNS in us-east-1. Billing metrics live only there and an alarm
# can notify only a topic of its own region, so no billing alarm that sends mail is possible: then the topic, the
# subscription and the alarms are skipped and the budget below (which mails by itself) is still created.
ALARMS_DONE="alarms at ${ALARM_USD} USD and "
if TOPIC_ARN="$(cap "arn:aws:sns:${BILLING_REGION}:${ACCOUNT_ID}:tmkg-billing" aws sns create-topic --name tmkg-billing \
    --tags "Key=${TAG_KEY},Value=${TAG_VALUE}" --query TopicArn --output text --region "$BILLING_REGION")"; then
  run aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol email --notification-endpoint "$ALERT_EMAIL" \
    --region "$BILLING_REGION"

  for usd in $ALARM_USD; do
    run aws cloudwatch put-metric-alarm --alarm-name "tmkg-billing-${usd}usd" \
      --alarm-description "Estimated AWS charges reached ${usd} USD (project ${TAG_VALUE})" \
      --namespace AWS/Billing --metric-name EstimatedCharges --dimensions Name=Currency,Value=USD \
      --statistic Maximum --period 21600 --evaluation-periods 1 --threshold "$usd" \
      --comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching \
      --alarm-actions "$TOPIC_ARN" --region "$BILLING_REGION"
  done
else
  echo "SKIPPED: SNS in ${BILLING_REGION} refused the topic (see the error above), so no billing topic, subscription or alarms." >&2
  echo "         The budget below still mails ${ALERT_EMAIL} by itself." >&2
  ALARMS_DONE=""
fi

BUDGET="$(printf '{"BudgetName":"tmkg-monthly","BudgetLimit":{"Amount":"%s","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' "$BUDGET_USD")"
NOTES="$(printf '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":50,"ThresholdType":"PERCENTAGE"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"%s"}]},{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"%s"}]}]' "$ALERT_EMAIL" "$ALERT_EMAIL")"
run aws budgets create-budget --account-id "$ACCOUNT_ID" --budget "$BUDGET" --notifications-with-subscribers "$NOTES"
echo "Done: ${ALARMS_DONE}a ${BUDGET_USD} USD monthly budget. Confirm the e-mail subscription before going on."
