#!/usr/bin/env bash
# Bulk-load the uploaded graph version into Neptune DATABASE (the Serverless cluster of aws/neptune_create.sh; the /loader API
# does not exist on Neptune Analytics) as openCypher CSV, then CHECK the counts against outputs/run_manifest.json and exit 1
# when they differ. Run from a machine inside the cluster's VPC (the app instance), after aws/upload_s3.sh, or from anywhere
# with --remote (below).
# The loader reads every file in one S3 folder (nodes.csv + edges.csv, or the many nodes_*.csv.gz / edges_*.csv.gz that
# pipelines/neptune_export.py writes); failOnError makes a bad row stop the load.
# Usage: bash aws/neptune_load.sh [--dry-run] [--full] [--remote]   (TMKG_S3_VERSION=<fingerprint> overrides outputs/last_s3_version.txt)
#   --full    the WHOLE graph of pipelines/neptune_export.py: folder outputs/neptune/full/opencypher/, counts checked against
#             outputs/neptune/full/manifest.json, parallelism HIGH (AWS documents a deadlock risk for openCypher loads at HIGH
#             and OVERSUBSCRIBE: a job that fails with LOAD_DATA_DEADLOCK is resubmitted with mode RESUME one parallelism step
#             lower, at most 3 times). Without it: MEDIUM, outputs/neptune/opencypher/, outputs/run_manifest.json.
#   NEPTUNE_PARALLELISM=LOW|MEDIUM|HIGH|OVERSUBSCRIBE overrides either default.
#   TMKG_NEPTUNE_S3_URI=s3://bucket/prefix/  load from this folder instead of the fingerprint path. With --full the folder
#             must hold nodes/ and edges/: two loader jobs, <uri>nodes/ first and <uri>edges/ second (dependencies on the
#             first; both openCypher, failOnError, userProvidedEdgeIds, queueRequest). The loader role may read neptune/*.
#   --remote  port 8182 answers only inside the VPC and there is no ssh: every loader and openCypher call runs ON the app
#             instance (the running one tagged Name=tmkg-app) through Run Command (aws ssm send-command, document
#             AWS-RunShellScript) and is read back with get-command-invocation. The manifest is read locally. Same count
#             check, same exit 1 on a mismatch. Needs tmkg-deployer-policy-2 (Run Command on the project instance).
#   Pause the idle stop first (bash aws/neptune_idle_stop.sh --pause): a load is not query activity.
set -euo pipefail

# retry_decision STATUS PARALLELISM ATTEMPTS_SO_FAR [DETAILS]: what to do with a load job that ended in STATUS (pure).
# Prints "done" (LOAD_COMPLETED), "retry <lower parallelism>" (a LOAD_DATA_DEADLOCK in the status or the error details, fewer
# than 3 retries so far) or "fail".
retry_decision() {
  local status="$1" par="$2" attempts="$3" details="${4:-}" lower
  if [ "$status" = LOAD_COMPLETED ]; then echo "done"; return 0; fi
  case "${status} ${details}" in
    *LOAD_DATA_DEADLOCK*) ;;
    *) echo fail; return 0 ;;
  esac
  if [ "$attempts" -ge 3 ]; then echo fail; return 0; fi
  case "$par" in
    OVERSUBSCRIBE) lower=HIGH ;;
    HIGH) lower=MEDIUM ;;
    *) lower=LOW ;;
  esac
  echo "retry ${lower}"
}

# load_body SOURCE PARALLELISM [MODE] [DEPENDENCY_LOAD_ID]: the JSON body of one POST /loader (uses the globals below)
load_body() {
  local src="$1" par="$2" mode="${3:-AUTO}" dep="${4:-}" extra=""
  if [ "$EDGE_IDS" = 1 ]; then extra=',"userProvidedEdgeIds":"TRUE"'; fi
  if [ "$mode" != AUTO ]; then extra="${extra},\"mode\":\"${mode}\""; fi
  if [ -n "$dep" ]; then extra="${extra},\"dependencies\":[\"${dep}\"]"; fi
  printf '{"source":"%s","format":"opencypher","iamRoleArn":"%s","region":"%s","failOnError":"TRUE","parallelism":"%s","queueRequest":"TRUE"%s}' \
    "$src" "$NEPTUNE_LOAD_ROLE_ARN" "$AWS_REGION" "$par" "$extra"
}

# sourced (tests): only the functions above
if [ "${BASH_SOURCE[0]}" != "$0" ]; then return 0; fi

# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
cd "$ROOT_DIR"
exec 3>&1      # progress lines go to the terminal (fd 3) also from inside $(...)
: "${NEPTUNE_ENDPOINT:?set NEPTUNE_ENDPOINT in aws/config.env}"
: "${NEPTUNE_LOAD_ROLE_ARN:?set NEPTUNE_LOAD_ROLE_ARN in aws/config.env}"
NEPTUNE_IAM_AUTH="${NEPTUNE_IAM_AUTH:-0}"
FULL=0
REMOTE=0
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  case "$a" in
    --full) FULL=1 ;;
    --remote) REMOTE=1 ;;
  esac
done
if [ "$REMOTE" = 1 ] && [ "$NEPTUNE_IAM_AUTH" = 1 ]; then
  echo "--remote sends plain curl from the app instance; a cluster with IAM auth needs awscurl there (not installed)" >&2
  exit 2
fi
# TMKG_NEPTUNE_SRC: the folder under outputs/ that holds nodes.csv + edges.csv (default the full graph; the kinase subgraph of
# pipelines/neptune_subgraph.py is neptune_kinase/opencypher). TMKG_MANIFEST: the JSON whose counts are checked.
if [ "$FULL" = 1 ]; then
  NEPTUNE_SRC="${TMKG_NEPTUNE_SRC:-neptune/full/opencypher}"
  MANIFEST="${TMKG_MANIFEST:-outputs/neptune/full/manifest.json}"
  PARALLELISM="${NEPTUNE_PARALLELISM:-HIGH}"
else
  NEPTUNE_SRC="${TMKG_NEPTUNE_SRC:-neptune/opencypher}"
  MANIFEST="${TMKG_MANIFEST:-outputs/run_manifest.json}"
  PARALLELISM="${NEPTUNE_PARALLELISM:-MEDIUM}"
fi
case "$PARALLELISM" in LOW | MEDIUM | HIGH | OVERSUBSCRIBE) ;; *) echo "NEPTUNE_PARALLELISM must be LOW, MEDIUM, HIGH or OVERSUBSCRIBE" >&2; exit 2 ;; esac
EDGE_IDS=0
TWO_STEP=0
if [ -n "${TMKG_NEPTUNE_S3_URI:-}" ]; then
  if ! [[ "$TMKG_NEPTUNE_S3_URI" =~ ^s3://[a-z0-9.-]+/[A-Za-z0-9._/=-]*$ ]]; then
    echo "TMKG_NEPTUNE_S3_URI must look like s3://bucket/prefix/ (got '$TMKG_NEPTUNE_S3_URI')" >&2
    exit 2
  fi
  SRC="${TMKG_NEPTUNE_S3_URI%/}/"
  if [ "$FULL" = 1 ]; then TWO_STEP=1; EDGE_IDS=1; fi
else
  if [ "$DRY_RUN" = 1 ]; then FP="0123456789ab"; else FP="${TMKG_S3_VERSION:-$(cat outputs/last_s3_version.txt)}"; fi
  SRC="s3://${TMKG_BUCKET}/trialmindkg/${FP}/outputs/${NEPTUNE_SRC#/}/"
fi
URL="https://${NEPTUNE_ENDPOINT}:8182"

# --remote: the one running app instance
INSTANCE_ID=""
if [ "$REMOTE" = 1 ]; then
  IDS="$(awsx_cap i-0example ec2 describe-instances \
    --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" Name=tag:Name,Values=tmkg-app Name=instance-state-name,Values=running \
    --query 'Reservations[].Instances[].InstanceId' --output text)"
  read -r -a FOUND_IDS <<< "$IDS"
  if [ "${#FOUND_IDS[@]}" != 1 ] || [ "${FOUND_IDS[0]}" = None ]; then
    echo "--remote needs exactly one running instance tagged Name=tmkg-app (found: ${IDS:-none})" >&2
    exit 1
  fi
  INSTANCE_ID="${FOUND_IDS[0]}"
fi

# remote_run COMMAND: run one shell command on the app instance (Run Command), wait for it, print its standard output.
# A failed command prints its error output and returns 1.
remote_run() {
  local params cid out status i
  params="$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]]}))' "$1")"
  cid="$(aws ssm send-command --document-name AWS-RunShellScript --instance-ids "$INSTANCE_ID" \
    --comment "tmkg neptune_load.sh" --parameters "$params" --query Command.CommandId --output text --region "$AWS_REGION")"
  for i in $(seq 1 150); do
    sleep 2
    if ! out="$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$INSTANCE_ID" --output json \
        --region "$AWS_REGION" 2>&1)"; then
      case "$out" in *InvocationDoesNotExist*) continue ;; *) printf '%s\n' "$out" >&2; return 1 ;; esac
    fi
    status="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Status"])')"
    case "$status" in
      Pending | InProgress | Delayed) continue ;;
      Success) printf '%s' "$out" | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin)["StandardOutputContent"])'
               return 0 ;;
      *) printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.stderr.write(d.get("StandardErrorContent","") + d.get("StandardOutputContent",""))'
         echo "Run Command ${cid} ended ${status} after ${i} polls" >&2
         return 1 ;;
    esac
  done
  echo "Run Command ${cid} did not finish in 5 minutes" >&2
  return 1
}

call() {   # call METHOD PATH [key=value form field]: JSON for /loader, a url-encoded form field for /openCypher
  local method="$1" path="$2" data="${3:-}"
  local -a opts=()
  if [ -n "$data" ]; then
    case "$path" in
      /openCypher) opts=(--data-urlencode "$data") ;;
      *) opts=(-H 'Content-Type: application/json' -d "$data") ;;
    esac
  fi
  if [ "$REMOTE" = 1 ]; then
    local cmd      # POSIX-quoted (shlex): Run Command hands the line to sh
    cmd="$(python3 -c 'import shlex,sys; print(shlex.join(sys.argv[1:]))' \
      curl -sS --fail-with-body --max-time 60 -X "$method" "${URL}${path}" "${opts[@]+"${opts[@]}"}")"
    remote_run "$cmd"
  elif [ "$NEPTUNE_IAM_AUTH" = "1" ]; then
    awscurl --service neptune-db --region "$AWS_REGION" -X "$method" "${URL}${path}" "${opts[@]+"${opts[@]}"}"
  else
    curl -sS --fail-with-body -X "$method" "${URL}${path}" "${opts[@]+"${opts[@]}"}"
  fi
}
jget() { python3 -c "import json,sys;d=json.load(sys.stdin);print($1)"; }

if [ "$DRY_RUN" = 1 ]; then
  VIA=""
  if [ "$REMOTE" = 1 ]; then
    VIA="   (on ${INSTANCE_ID} via aws ssm send-command --document-name AWS-RunShellScript; read back with get-command-invocation)"
    show aws ssm send-command --document-name AWS-RunShellScript --instance-ids "$INSTANCE_ID" --comment "tmkg neptune_load.sh" \
      --parameters '{"commands": ["curl -sS --fail-with-body --max-time 60 -X POST '"${URL}"'/loader ..."]}' --region "$AWS_REGION"
    show aws ssm get-command-invocation --command-id '<CommandId>' --instance-id "$INSTANCE_ID" --output json --region "$AWS_REGION"
  fi
  if [ "$TWO_STEP" = 1 ]; then
    echo "+ POST ${URL}/loader   $(load_body "${SRC}nodes/" "$PARALLELISM")${VIA}"
    echo "+ POST ${URL}/loader   $(load_body "${SRC}edges/" "$PARALLELISM" AUTO '<loadId of the nodes job>')${VIA}"
    echo "# step 1 loads ${SRC}nodes/, step 2 ${SRC}edges/ and starts only after step 1 completed (dependencies)"
  else
    echo "+ POST ${URL}/loader   $(load_body "$SRC" "$PARALLELISM")${VIA}"
    echo "# the prefix holds every nodes_*.csv.gz and edges_*.csv.gz of the manifest (the loader takes all files under it)"
  fi
  echo "+ GET  ${URL}/loader/<loadId>   (every 15 s until LOAD_COMPLETED)${VIA}"
  echo "# on LOAD_DATA_DEADLOCK: the same job again with \"mode\":\"RESUME\" one parallelism step lower ($(retry_decision LOAD_DATA_DEADLOCK "$PARALLELISM" 0)), at most 3 times"
  echo "+ POST ${URL}/openCypher   query=MATCH (n) RETURN count(n) AS nodes${VIA}"
  echo "+ POST ${URL}/openCypher   query=MATCH ()-[r]->() RETURN count(r) AS edges${VIA}"
  echo "# then compare with ${MANIFEST} counts; exit 1 on any difference"
  exit 0
fi

submit() {   # submit SOURCE PARALLELISM MODE DEPENDENCY: start one load job, print its id
  call POST /loader "$(load_body "$1" "$2" "$3" "$4")" | jget "d['payload']['loadId']"
}

# wait_job ID: poll until the job leaves the queue / progress states; print its final status
wait_job() {
  local st
  while true; do
    st="$(call GET "/loader/$1" | jget "d['payload']['overallStatus']['status']")" || return 1
    echo "  status: ${st}   (${1})" >&3
    case "$st" in
      LOAD_IN_PROGRESS | LOAD_NOT_STARTED | LOAD_IN_QUEUE) sleep 15 ;;
      *) echo "$st"; return 0 ;;
    esac
  done
}

# load_step NAME SOURCE DEPENDENCY: run one job to the end with the deadlock retry; print the id of the job that completed.
# Exit 1 when it fails for any other reason or after 3 retries.
load_step() {
  local name="$1" src="$2" dep="${3:-}" par="$PARALLELISM" mode=AUTO attempts=0 id st details decision
  while true; do
    id="$(submit "$src" "$par" "$mode" "$dep")" || { echo "the loader did not accept the ${name} job" >&2; exit 1; }
    if [ -z "$id" ]; then echo "the loader returned no load id for the ${name} job" >&2; exit 1; fi
    echo "Load ${id} (${name}, parallelism ${par}, mode ${mode}) started from ${src}" >&3
    st="$(wait_job "$id")" || { echo "could not read the status of load ${id}" >&2; exit 1; }
    details=""
    if [ "$st" != LOAD_COMPLETED ]; then details="$(call GET "/loader/${id}?details=true&errors=true" || true)"; fi
    decision="$(retry_decision "$st" "$par" "$attempts" "$details")"
    case "$decision" in
      "done") echo "$id"; return 0 ;;
      retry*) attempts=$((attempts + 1)); par="${decision#retry }"; mode=RESUME
              echo "  ${name}: LOAD_DATA_DEADLOCK; retry ${attempts} of 3 with parallelism ${par}, mode RESUME" >&3 ;;
      *) printf '%s\n' "$details" >&2
         echo "load ${id} (${name}) ended ${st}" >&2
         exit 1 ;;
    esac
  done
}

if [ "$TWO_STEP" = 1 ]; then
  # the edges job is queued only once the nodes job has completed (it names that job in dependencies); a nodes job that had
  # to be retried is a new load id, so the edges job always depends on the one that completed
  NODES_ID="$(load_step nodes "${SRC}nodes/")" || exit 1
  load_step edges "${SRC}edges/" "$NODES_ID" > /dev/null
else
  load_step graph "$SRC" > /dev/null
fi

WANT_N=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['counts']['nodes'])" "$MANIFEST")
WANT_E=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['counts']['edges'])" "$MANIFEST")
GOT_N=$(call POST /openCypher "query=MATCH (n) RETURN count(n) AS nodes" | jget "d['results'][0]['nodes']")
GOT_E=$(call POST /openCypher "query=MATCH ()-[r]->() RETURN count(r) AS edges" | jget "d['results'][0]['edges']")
echo "nodes: loaded ${GOT_N}, manifest ${WANT_N}; edges: loaded ${GOT_E}, manifest ${WANT_E}"
if [ "$GOT_N" != "$WANT_N" ] || [ "$GOT_E" != "$WANT_E" ]; then
  echo "COUNT MISMATCH: the graph in Neptune is not the committed graph" >&2
  exit 1
fi
echo "Counts match the manifest."
