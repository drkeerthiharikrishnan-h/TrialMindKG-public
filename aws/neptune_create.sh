#!/usr/bin/env bash
# STEP 5: Neptune Database SERVERLESS (MASTER_PLAN section 4): one cluster tmkg-neptune, capacity MIN_NCU..MAX_NCU
# (default 1..4), in the default VPC, port 8182 open only to the app's security group, the S3 loader role attached and an S3
# gateway endpoint (the bulk loader reaches S3 through it). Neptune has no idle shutdown: run aws/schedule_stop_start.sh too.
# Prints the cluster endpoint for NEPTUNE_ENDPOINT in aws/config.env.
# Options: --io-optimized  Neptune I/O-Optimized storage (--storage-type iopt1): no per-I/O charge, the better choice for the bulk load of
#                          the whole graph (about 30 million edges) and for a demo that reads a lot; standard storage otherwise.
#          --scale         do NOT create anything: only set the serverless capacity of the existing cluster to MIN_NCU..MAX_NCU
#                          (load at a high max, e.g. MIN_NCU=8 MAX_NCU=128, then back to MIN_NCU=1 MAX_NCU=4 for the demo).
#          MIN_NCU / MAX_NCU in the environment set the capacity (default 1..4).
# Usage: bash aws/neptune_create.sh [--dry-run] [--io-optimized] [--scale]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
MIN_NCU="${MIN_NCU:-1}"
MAX_NCU="${MAX_NCU:-4}"
TAGS="Key=${TAG_KEY},Value=${TAG_VALUE}"
IOPT=0
SCALE=0
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  case "$a" in
    --io-optimized) IOPT=1 ;;
    --scale) SCALE=1 ;;
  esac
done
if [ "$SCALE" = 1 ]; then
  awsx neptune modify-db-cluster --db-cluster-identifier tmkg-neptune --apply-immediately \
    --serverless-v2-scaling-configuration "MinCapacity=${MIN_NCU},MaxCapacity=${MAX_NCU}"
  echo "Capacity of tmkg-neptune set to ${MIN_NCU}..${MAX_NCU} NCU."
  exit 0
fi
STORAGE=()
if [ "$IOPT" = 1 ]; then STORAGE=(--storage-type iopt1); fi
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/tmkg-neptune-s3-load"

VPC_ID="$(awsx_cap vpc-0example ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
SUBNETS="$(awsx_cap "subnet-0aaa subnet-0bbb" ec2 describe-subnets --filters "Name=vpc-id,Values=${VPC_ID}" \
  Name=default-for-az,Values=true --query 'Subnets[].SubnetId' --output text)"
# shellcheck disable=SC2086   # SUBNETS is a space-separated list on purpose
awsx neptune create-db-subnet-group --db-subnet-group-name tmkg-neptune --db-subnet-group-description "tmkg Neptune" \
  --subnet-ids $SUBNETS --tags "$TAGS"

SG_ID="$(awsx_cap sg-0neptune ec2 create-security-group --group-name tmkg-neptune --description "tmkg Neptune 8182 from the app" \
  --vpc-id "$VPC_ID" --tag-specifications "ResourceType=security-group,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}}]" \
  --query GroupId --output text)"
APP_SG="$(awsx_cap sg-0app ec2 describe-security-groups --filters Name=group-name,Values=tmkg-app "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId' --output text)"
awsx ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 8182 --source-group "$APP_SG"
awsx ec2 create-vpc-endpoint --vpc-id "$VPC_ID" --vpc-endpoint-type Gateway --service-name "com.amazonaws.${AWS_REGION}.s3" \
  --tag-specifications "ResourceType=vpc-endpoint,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}}]"

awsx neptune create-db-cluster --db-cluster-identifier tmkg-neptune --engine neptune --storage-encrypted \
  --serverless-v2-scaling-configuration "MinCapacity=${MIN_NCU},MaxCapacity=${MAX_NCU}" \
  --db-subnet-group-name tmkg-neptune --vpc-security-group-ids "$SG_ID" --deletion-protection --tags "$TAGS" \
  "${STORAGE[@]+"${STORAGE[@]}"}"
awsx neptune create-db-instance --db-instance-identifier tmkg-neptune-1 --db-cluster-identifier tmkg-neptune \
  --engine neptune --db-instance-class db.serverless --tags "$TAGS"
awsx neptune add-role-to-db-cluster --db-cluster-identifier tmkg-neptune --role-arn "$ROLE_ARN"
awsx neptune wait db-instance-available --db-instance-identifier tmkg-neptune-1
ENDPOINT="$(awsx_cap "tmkg-neptune.cluster-example.${AWS_REGION}.neptune.amazonaws.com" neptune describe-db-clusters \
  --db-cluster-identifier tmkg-neptune --query 'DBClusters[0].Endpoint' --output text)"
echo "NEPTUNE_ENDPOINT=${ENDPOINT}   <- put this in aws/config.env, then: bash aws/neptune_load.sh (from the app instance)"
echo "The cluster has deletion protection: aws/teardown.sh switches it off before deleting."
