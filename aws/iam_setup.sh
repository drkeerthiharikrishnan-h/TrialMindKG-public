#!/usr/bin/env bash
# STEP 2: the two policies for the deploying user and the four roles the project resources run as (all named tmkg-*):
#   tmkg-deployer-policy    attach to the IAM user tmkg-deployer (aws/iam/deployer_policy.json: S3 bucket, EC2, CloudFront,
#                           Neptune, SSM /trialmindkg/*, EventBridge Scheduler group tmkg, CloudWatch, SNS, Budgets, tmkg-* roles)
#   tmkg-deployer-policy-2  attach to the same user (aws/iam/deployer_policy_2.json, 14 Nov: Run Command on the project instance,
#                           read-only CloudFront / Neptune / metric look-ups, the idle-stop function's policy and logs). Two
#                           policies because one managed policy holds at most 6,144 characters; no action is in both.
#   tmkg-app                EC2 instance role: read the app bundle in S3 and the NIM key in SSM, Session Manager (no ssh),
#                           read and start (never stop, never query) the tmkg-* Neptune cluster for the wake-on-click host timer
#   tmkg-neptune-s3-load    Neptune bulk loader: read the project bucket
#   tmkg-scheduler          EventBridge Scheduler: stop / start the tagged Neptune cluster and the app instance, run the idle sweep
#   tmkg-idle-stop          the idle-stop function (aws/neptune_idle_stop.sh): read the cluster and its metrics, stop it, write its log
# The owner creates the IAM user tmkg-deployer and its access key in the console; this script only attaches the policies.
# Re-runs are safe: a role or instance profile that already exists prints 'exists' and the script continues.
# --roles-only skips create-policy and attach-user-policy for BOTH deployer policies (use it when they already exist and
# are attached: the deployer's own policy denies attach-user-policy, so a full re-run would stop there). It only UPDATES roles that
# exist: each role is looked up first (aws iam get-role) and a role that is not there is NOT created and its inline policy
# is not put ("absent: NAME, not created ..."), so a role the owner deleted stays deleted (CC-44, 9 Nov: a --roles-only run
# recreated the deleted tmkg-neptune-s3-load). --with-neptune creates the two Neptune roles (tmkg-neptune-s3-load and
# tmkg-idle-stop) when they are absent (only when Neptune is in use); without it an absent one stays absent.
# A dry run calls nothing, so it prints the get-role look-ups and says which roles it would skip if absent.
# --verify is read-only (CC-45): for each tmkg-* role, its inline policies (list-role-policies), each statement's Sid and,
# for S3 statements, the Resource (get-role-policy). Needs iam:ListRolePolicies + iam:GetRolePolicy in the deployer policy
# (added 9 Nov; an admin login must apply the new policy version, see aws/README.md).
# Usage: bash aws/iam_setup.sh [--dry-run] [--roles-only [--with-neptune]] | bash aws/iam_setup.sh [--dry-run] --verify
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
IAM="$AWS_DIR/iam"
TAGS="Key=${TAG_KEY},Value=${TAG_VALUE}"
ROLES_ONLY=0 WITH_NEPTUNE=0 VERIFY=0
USAGE="usage: bash aws/iam_setup.sh [--dry-run] [--roles-only [--with-neptune]] | [--dry-run] --verify"
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  case "$a" in
    --roles-only) ROLES_ONLY=1 ;;
    --with-neptune) WITH_NEPTUNE=1 ;;
    --verify) VERIFY=1 ;;
    *) echo "unknown argument: $a ($USAGE)" >&2; exit 2 ;;
  esac
done
if [ "$WITH_NEPTUNE" = 1 ] && [ "$ROLES_ONLY" = 0 ]; then echo "--with-neptune only makes sense with --roles-only ($USAGE)" >&2; exit 2; fi
if [ "$VERIFY" = 1 ] && [ "$ROLES_ONLY" = 1 ]; then echo "--verify changes nothing; do not combine it with --roles-only ($USAGE)" >&2; exit 2; fi
ROLES="tmkg-app tmkg-neptune-s3-load tmkg-scheduler tmkg-idle-stop"
NEPTUNE_ROLES="tmkg-neptune-s3-load tmkg-idle-stop"    # --with-neptune creates these when absent

# role_state NAME: prints present / absent (aws iam get-role; NoSuchEntity = absent); any other error stops the script.
# In a dry run nothing is called: it prints the call and 'unknown'.
role_state() {
  local err
  show aws iam get-role --role-name "$1" >&2            # stderr: stdout is the answer the caller captures
  if [ "$DRY_RUN" = 1 ]; then echo unknown; return 0; fi
  if err="$(aws iam get-role --role-name "$1" 2>&1 >/dev/null)"; then echo present; return 0; fi
  case "$err" in
    *NoSuchEntity*) echo absent ;;
    *) printf '%s\n' "$err" >&2; return 1 ;;
  esac
}

# --verify: read-only listing of each project role's inline policies, Sids and S3 resources
if [ "$VERIFY" = 1 ]; then
  echo "# --verify: read-only (list-role-policies, get-role-policy); nothing is created or changed"
  for r in $ROLES; do
    show aws iam list-role-policies --role-name "$r" --output json
    if [ "$DRY_RUN" = 1 ]; then echo "# would list the inline policies of $r and print each statement's Sid (S3 statements with their Resource)"; continue; fi
    if ! pols="$(aws iam list-role-policies --role-name "$r" --output json 2>&1)"; then
      case "$pols" in *NoSuchEntity*) echo "absent: $r"; continue ;; *) printf '%s\n' "$pols" >&2; exit 1 ;; esac
    fi
    names="$(printf '%s' "$pols" | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin).get("PolicyNames", [])))')"
    if [ -z "$names" ]; then echo "role $r: no inline policy"; continue; fi
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      show aws iam get-role-policy --role-name "$r" --policy-name "$p" --output json
      doc="$(aws iam get-role-policy --role-name "$r" --policy-name "$p" --output json)"
      printf '%s' "$doc" | python3 -c '
import json, sys, urllib.parse
role, pol = sys.argv[1], sys.argv[2]
d = json.load(sys.stdin).get("PolicyDocument", {})
d = json.loads(urllib.parse.unquote(d)) if isinstance(d, str) else d
sts = d.get("Statement", [])
sts = [sts] if isinstance(sts, dict) else sts
print(f"role {role} / policy {pol}: {len(sts)} statements")
for s in sts:
    acts = s.get("Action", [])
    acts = [acts] if isinstance(acts, str) else acts
    res = s.get("Resource", [])
    res = [res] if isinstance(res, str) else res
    line = "  Sid " + str(s.get("Sid", "(no Sid)"))
    if any(a.lower().startswith("s3:") for a in acts):
        line += "  Resource " + ", ".join(res)
    print(line)
' "$r" "$p"
    done <<< "$names"
  done
  exit 0
fi

# run_or_exists NAME CMD...: run CMD (shown first, skipped in a dry run); when AWS answers that NAME already exists
# (EntityAlreadyExists, or LimitExceeded for a role already in the instance profile) print 'exists: NAME' and go on;
# any other error stops the script with AWS's message.
run_or_exists() {
  local name="$1" err
  shift
  show "$@"
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  if err="$("$@" 2>&1 >/dev/null)"; then return 0; fi
  case "$err" in
    *EntityAlreadyExists* | *LimitExceeded*) echo "exists: $name" ;;
    *) printf '%s\n' "$err" >&2; return 1 ;;
  esac
}

deployer_policy() {   # deployer_policy NAME FILE: create the managed policy and attach it to tmkg-deployer (admin run only)
  local file arn
  file="$(render_tmp "$IAM/$2")"
  arn="$(cap "arn:aws:iam::${ACCOUNT_ID}:policy/$1" aws iam create-policy \
    --policy-name "$1" --policy-document "file://${file}" --tags "$TAGS" \
    --query Policy.Arn --output text)"
  run aws iam attach-user-policy --user-name tmkg-deployer --policy-arn "$arn"
  rm -f "$file"
}
if [ "$ROLES_ONLY" = 1 ]; then
  echo "# --roles-only: tmkg-deployer-policy and tmkg-deployer-policy-2 are neither created nor attached"
else
  deployer_policy tmkg-deployer-policy deployer_policy.json
  deployer_policy tmkg-deployer-policy-2 deployer_policy_2.json
fi

make_role() {   # make_role NAME TRUST_FILE POLICY_FILE
  local trust inline state
  if [ "$ROLES_ONLY" = 1 ] && ! { [ "$WITH_NEPTUNE" = 1 ] && case " $NEPTUNE_ROLES " in *" $1 "*) true ;; *) false ;; esac; }; then
    state="$(role_state "$1")"
    if [ "$state" = absent ]; then
      echo "absent: $1, not created (run without --roles-only or pass --with-neptune)"
      return 0
    fi
    if [ "$state" = unknown ]; then
      echo "# --roles-only: $1 is updated only if get-role finds it; if it is absent it is skipped (absent: $1, not created)"
    fi
  fi
  trust="$(render_tmp "$IAM/$2")"
  inline="$(render_tmp "$IAM/$3")"
  run_or_exists "role $1" aws iam create-role --role-name "$1" --assume-role-policy-document "file://${trust}" --tags "$TAGS"
  run aws iam put-role-policy --role-name "$1" --policy-name "$1-inline" --policy-document "file://${inline}"
  rm -f "$trust" "$inline"
  MADE="$MADE $1"
}
MADE=""
make_role tmkg-app trust_ec2.json app_instance_policy.json
make_role tmkg-neptune-s3-load trust_rds.json neptune_s3_read.json
make_role tmkg-scheduler trust_scheduler.json scheduler_policy.json
make_role tmkg-idle-stop trust_lambda.json idle_stop_policy.json

case " $MADE " in
  *" tmkg-app "*)
    run_or_exists "instance profile tmkg-app" aws iam create-instance-profile --instance-profile-name tmkg-app --tags "$TAGS"
    run_or_exists "role tmkg-app in instance profile tmkg-app" aws iam add-role-to-instance-profile \
      --instance-profile-name tmkg-app --role-name tmkg-app ;;
  *) echo "# tmkg-app is absent: the instance profile is left alone" ;;
esac
echo "Done. Role ARNs: arn:aws:iam::${ACCOUNT_ID}:role/tmkg-neptune-s3-load (put it into NEPTUNE_LOAD_ROLE_ARN in aws/config.env);"
echo "arn:aws:iam::${ACCOUNT_ID}:role/tmkg-idle-stop (used by aws/neptune_idle_stop.sh)"
