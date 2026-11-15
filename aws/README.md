# AWS kit

Everything here is written to be read and **dry-run first**: every script takes `--dry-run`, prints each call it would make
(`+ aws ...`) and changes nothing, so it also runs with no account and no credentials. Claude Code writes these scripts and
never runs them for real; Claude Science (or the owner) runs them with the `tmkg-deployer` key, after reading the dry run.
Fill `aws/config.env` from `aws/config.example.env` first (git-ignored; never commit keys). One region (`AWS_REGION`),
every resource tagged `project=trialmindkg`, so `aws/teardown.sh` can find and remove all of it.
Cost plan and the cap: `docs/AWS_COST_ESTIMATE_2026-11-04.md`; stack decision: `docs/MASTER_PLAN.md` section 4.

## Steps, in this order

| # | Command | What it does |
|---|---|---|
| 1 | `bash aws/budget_alarms.sh` | **Before anything else.** SNS topic + e-mail, CloudWatch `EstimatedCharges` alarms at `ALARM_USD` (default 25 / 75 / 150 USD), a monthly budget of `BUDGET_USD`. Billing alerts must be switched on once in the console. If the account denies SNS in us-east-1, the script says so, skips the topic and alarms, and creates only the budget (which mails by itself). |
| 2 | `bash aws/iam_setup.sh` | Attaches **two** managed policies to the user `tmkg-deployer`: `tmkg-deployer-policy` (`aws/iam/deployer_policy.json`: S3 bucket, EC2, CloudFront, Neptune, SSM `/trialmindkg/*`, Scheduler group, CloudWatch, Lambda `tmkg-*`, `tmkg-*` roles; explicit Deny on users, keys, organizations) and `tmkg-deployer-policy-2` (`aws/iam/deployer_policy_2.json`, 14 Nov: Run Command on the project instance only, read-only CloudFront / Neptune / metric look-ups, the idle-stop function's policy and logs, reading its own two policies). Two because one managed policy holds at most 6,144 characters; no action is in both. Creates the roles `tmkg-app` (instance; may also read and start, never stop or query, the `tmkg-*` Neptune cluster), `tmkg-neptune-s3-load`, `tmkg-scheduler` (may also invoke the idle-stop function), `tmkg-idle-stop` and the instance profile. |
| 3 | `NVIDIA_API_KEY=... bash aws/nim_key_ssm.sh` | Stores the NIM key as SSM SecureString `/trialmindkg/NVIDIA_API_KEY` through a 0600 temp file (never on a command line). |
| 4 | `bash aws/upload_s3.sh` | Uploads `outputs/` and the frozen snapshot folders to S3, keyed by the snapshot fingerprint. |
| 5 | `bash aws/neptune_create.sh [--io-optimized]` | Neptune Database **Serverless** (1-4 NCU; `MIN_NCU` / `MAX_NCU` change it) in the default VPC, port 8182 open only to the app's security group, loader role attached, S3 gateway endpoint. Deletion protection on. |
| 5b | `bash aws/neptune_idle_stop.sh`, then `bash aws/neptune_idle_stop.sh --status` | **Idle stop** (PK14 A4): function `tmkg-idle-stop` (`aws/lambda/idle_stop.py`), CloudWatch alarm `tmkg-neptune-idle` (no query for 60 minutes; its action is the function) and the sweep schedule `tmkg-neptune-idle-sweep` (every 30 minutes, because an alarm acts only on a state change). `--pause` before a bulk load (a load is not query activity), `--resume` after it, `--remove`. `--status` is read-only and exits 0 only when all of it exists and is enabled. Confirm the two metric names once the cluster exists (`aws cloudwatch list-metrics --namespace AWS/Neptune`; `NEPTUNE_ACTIVITY_METRIC`, `NEPTUNE_UPTIME_METRIC` in `aws/config.env`) and re-run. |
| 6 | `bash aws/neptune_load.sh [--full] [--remote]` | Bulk-loads `outputs/neptune/opencypher/` and **exits 1 unless node and edge counts equal `outputs/run_manifest.json`**. `--full` loads the whole graph of `pipelines/neptune_export.py` (`outputs/neptune/full/opencypher/`, many gzip files, parallelism HIGH) and checks `outputs/neptune/full/manifest.json`. `TMKG_NEPTUNE_S3_URI=s3://bucket/prefix/` with `--full`: two jobs, `nodes/` then `edges/` (the second depends on the first). A job that fails with `LOAD_DATA_DEADLOCK` is resubmitted (mode RESUME) one parallelism step lower, at most 3 times. `--remote`: port 8182 answers only inside the VPC and there is no ssh, so every call runs on the app instance through Run Command (`tmkg-deployer-policy-2`). |
| 7 | `bash aws/deploy_app.sh` | Private bucket, app bundle (`git archive` + card index + predictions) to S3, one EC2 instance (t3.large, 50 GB disk by default: `APP_INSTANCE_TYPE`, `APP_DISK_GB`) running `aws/Dockerfile` (no ssh: Session Manager; port 80 only from CloudFront's prefix list; NIM key read from SSM at start), CloudFront distribution in front. Prints the URL for `loop/state.yaml` (`hosted_url`). |
| 7a | `bash aws/cloudfront_front.sh`, then `bash aws/cloudfront_front.sh --status` | **Amazon CloudFront in front of the Caddy site** (PK14 A1), additive: one distribution (comment `tmkg front`, tag `project=trialmindkg`, fixed caller reference, so never a second one) whose origin is `tmkg.<elastic-ip-with-dashes>.sslip.io` over HTTPS. No caching for the app (cache policy `Managed-CachingDisabled`, every viewer header but Host passed on), `Managed-CachingOptimized` for `/downloads/*`. Prints `https://<distribution>.cloudfront.net/`; the sslip.io link stays the fallback. At boot Caddy trusts `X-Forwarded-For` only from CloudFront's origin-facing ranges (`user_data.sh.tmpl`, A1b), so visitors keep their own address. `--status` is read-only (exit 0 only when Deployed and enabled). |
| 7b | `bash aws/status_alarm.sh`, then `bash aws/status_alarm.sh --status` | **After every launch or relaunch.** SNS topic `tmkg-app-status` and one CloudWatch alarm `tmkg-app-status-check` on the EC2 status checks of the running instance tagged `Name=tmkg-app` (failing for two minutes, or no data because the instance is stopped or gone). The alarm names one instance id, so a relaunch needs a re-run (same alarm, new id). It subscribes nobody: the owner adds her address in the console. `--status` is read-only and exits 0 only when the alarm watches the running instance and the topic has a confirmed subscription. What to do when it goes off: `docs/RUNBOOK_JUDGING.md`. |
| 8 | `bash aws/schedule_stop_start.sh [--ec2]`, then `bash aws/schedule_stop_start.sh --status` | EventBridge Scheduler (group `tmkg`) stops Neptune (and the app instance with `--ec2`) every evening at `STOP_CRON` (04:00 UTC = 20:00 US Pacific, 23:00 US Eastern); start schedules are created disabled (the page wakes Neptune on demand). Neptune has no idle shutdown of its own: step 5b adds one. `--status` reads the four schedules by name (`get-schedule`; `list-schedules` is denied for the deployer) and exits 0 when `tmkg-neptune-stop` is ENABLED. |
| 9 | `bash aws/teardown.sh --list`, then `bash aws/teardown.sh [--include-bucket] [--snapshot]` | Lists everything tagged `project=trialmindkg` and removes it in dependency order. The bucket (the deliverables) is kept unless `--include-bucket`. |

`bash aws/monitor_task.sh` is the monthly change monitor (AACT check -> freeze -> rebuild -> diff -> tests -> upload); run it as a
container task, see the end of this file.

## Files

- `lib.sh` - shared helpers (`--dry-run`, `run`, tags, rendering of the policy templates). `iam/*.json` - policies and trust documents
  with `BUCKET_NAME`, `ACCOUNT_ID`, `REGION`, `TAG_VALUE` placeholders filled at run time.
- `Dockerfile`, `Dockerfile.dockerignore`, `user_data.sh.tmpl` - the app image and the instance's first-boot script.
- `tests/test_aws_kit.py` checks all of it without AWS: dry runs print every call and make none, resources are tagged, policies parse,
  the key never reaches a command line, `neptune_load.sh` fails on a count mismatch (stubbed endpoint), `shellcheck` is clean when installed.

## Checks before the first real run
1. `bash aws/<script>.sh --dry-run` for the step, read it.
2. `uv tool run --from shellcheck-py shellcheck -x -P SCRIPTDIR aws/*.sh` (clean with shellcheck 0.11.0 from any folder; without `-P SCRIPTDIR` the `source=` hints resolve against the current folder, which gave SC2218/SC1091 findings; checked by `tests/test_aws_kit.py` wherever shellcheck is installed).
3. Real runs one step at a time; the cost estimate says what each step can cost. Stop and ask if a step would pass 50% of the cap.

## Alarms on the project's account (S09, 12 Nov)

- `bash aws/budget_alarms.sh` on that account: the organization denies the notification service (SNS) in us-east-1 with a
  service control policy, and billing metrics exist only in that region, so the script skips the topic and the alarms and
  creates only the budget.
  Spending is watched by two AWS Budgets that mail the owner directly; see `docs/RUNBOOK_JUDGING.md`, section 6.
- SNS and CloudWatch in the app's region are allowed: `bash aws/status_alarm.sh` works (step 7b). Record of the run:
  the S09 run record under `outputs/review/` (private repository; not in the public copy).

## Caddy front door: the certificate survives a relaunch (CC-42, 9 Nov)

Let's Encrypt gives at most 5 certificates per hostname per 7 days. Before CC-42 the certificate lived only in
`/srv/caddy` on the instance, so every relaunch asked for a new one (the bare `<ip>.sslip.io` name hit the limit on
9 Nov). Now, with `TMKG_TLS=caddy`, `user_data.sh.tmpl`:
- before Caddy starts, restores `s3://$TMKG_BUCKET/app/caddy/` into `/srv/caddy` (nothing there = first boot = a new
  certificate; a failed restore prints a WARNING and the boot goes on);
- saves `/srv/caddy` back to that prefix 2 minutes after boot, every 10 minutes after that (systemd timer
  `tmkg-caddy-sync-periodic.timer`) and at shutdown (`tmkg-caddy-sync.service`, ExecStop). Lock files are not saved.
- The prefix holds the certificate's private key and the ACME account key: the bucket must stay private.
- Optional `TMKG_ACME_EMAIL` in `aws/config.env` writes `{ email ... }` at the top of the Caddyfile, so Caddy can fall
  back to ZeroSSL if Let's Encrypt refuses.

Owner steps before redeploy #3 (once):
1. `bash aws/iam_setup.sh --roles-only` - re-puts the `tmkg-app` inline policy, which now may write (PutObject only)
   `app/caddy/*`. The running instance gets the permission at once.
2. Save the certificate the running instance already has, so redeploy #3 uses no new one: in a Session Manager shell
   on it, `sudo aws s3 sync /srv/caddy/ s3://<bucket>/app/caddy/ --region <region> --exclude "caddy/locks/*"`, then
   `aws s3 ls s3://<bucket>/app/caddy/ --recursive` from the Mac should list `.crt` and `.key` files for the hostname.
3. Redeploy as before. In the new instance's boot log (`/var/log/cloud-init-output.log`) there is no WARNING line
   after the restore, and `docker logs caddy 2>&1 | grep -i obtain` shows no new "certificate obtained" line (it reused the saved one).

### Roles: `--roles-only`, `--with-neptune`, `--verify` (CC-44, CC-45; 9 Nov)

- `bash aws/iam_setup.sh --roles-only` only **updates roles that exist**. It looks each one up first (`aws iam get-role`);
  a role that is not there is not created and its inline policy is not put. It prints
  `absent: NAME, not created (run without --roles-only or pass --with-neptune)`. A role you deleted stays deleted (on 9 Nov
  a `--roles-only` run recreated the deleted `tmkg-neptune-s3-load`). `--dry-run --roles-only` makes no call: it prints
  the `get-role` look-ups and says that any absent role would be skipped.
- `bash aws/iam_setup.sh --roles-only --with-neptune` creates `tmkg-neptune-s3-load` even when it is absent (only when
  Neptune is in use). A full run without `--roles-only` (the first setup) creates all three roles, as before.
- `bash aws/iam_setup.sh --verify` is read-only: for each `tmkg-*` role it lists the inline policies
  (`list-role-policies`) and prints each statement's Sid, with the Resource for S3 statements (`get-role-policy`). An
  absent role prints `absent: NAME`.
- `--verify` needs `iam:GetRolePolicy` and `iam:ListRolePolicies`, added on 9 Nov to the statement `OnlyTheProjectRoles`
  of `aws/iam/deployer_policy.json` (same `tmkg-*` resources). Until the new version is applied, both answer AccessDenied.
  **An admin login must apply it** (the deployer may not change its own policy): in the console, signed in as an admin,
  IAM → Policies → tmkg-deployer-policy → Edit → JSON → Save as default version (paste the file with `ACCOUNT_ID` replaced
  by the account number). Then `bash aws/iam_setup.sh --verify` as the deployer.

## Independent check on an edge: outbound hosts (`pipelines/routes_audit.py`, 12 Nov)

`GET /api/pharos/audit/{edge_id}` answers from a stored record when there is one and makes **no outbound call** for it.
For any other edge the app instance itself calls public sources and one hosted model, all over HTTPS (port 443), under a
total budget of 10 seconds per check. These are the only hosts the check contacts:

| Host | Used for | Kind of edge |
|---|---|---|
| `www.ebi.ac.uk` | IntAct (PSICQUIC), Complex Portal, Europe PMC, QuickGO, ChEMBL, IMPC | protein-protein, gene-disease, Gene Ontology, compound-protein, phenotype |
| `reactome.org` | Reactome ContentService | protein-protein, pathway |
| `api.platform.opentargets.org` | Open Targets GraphQL (association scores, text-mined sentences) | gene-disease |
| `rest.uniprot.org` | UniProt entry (names, gene cross-references) | protein-protein, gene-disease, gene-protein, pathway, phenotype |
| `www.wikipathways.org` | WikiPathways participant tables | pathway |
| `string-db.org` | STRING channel scores (shown as context only) | protein-protein |
| the model endpoint (`base_url` in `config/nim_models.yaml`, `integrate.api.nvidia.com` unless `NIM_BASE_URL` is set) | one judge call | every kind except gene-protein |

- **Nothing has to be opened today:** `aws/deploy_app.sh` creates the security group `tmkg-app` with inbound rules only, so
  outbound traffic is unrestricted (the AWS default). If outbound traffic is ever restricted (an egress rule, a proxy, a
  network firewall), allow these hosts on 443; a blocked host does not break the page, the check for that edge answers
  "independent check unavailable right now" or lists the source as not answered.
- **What leaves the instance:** public identifiers and names of the two entities of the edge (UniProt accession, gene
  symbol, disease name, GO / pathway / compound id) and, to the model endpoint, the evidence those sources returned. The
  pruner's verdict, confidence and reasons are never sent. No visitor data is sent.
- **Stored records:** `data/audit/edges/<edge id>.json` (written on the Mac by `scripts/audit_spotcheck.py publish`; `data/`
  is not in git). Upload the folder with the stored answers so that it lands under the data root, on the instance
  `/srv/data/data/audit/edges/` (the container sees it at `/data/data/audit/edges/`, read-only). The app writes nothing there.
- **Cost guards:** a new check counts as one question of the visitor (`TMKG_ASK_LIMIT_PER_HOUR`) and of the day's total for
  all visitors (`TMKG_ASK_LIMIT_PER_DAY`), both shared with the ask
  endpoints; stored and already-made checks are never counted); at most `TMKG_AUDIT_PER_MINUTE` new checks start per minute
  for all visitors together (default 8; 0 = no cap), because the NVIDIA key allows roughly 15 to 20 calls a minute and the
  ask endpoints share it. One check = at most one model call, never retried. At most 4 checks run at once; a check that gave no
  judgement is not repeated for that edge for a minute.
- **Judge model:** `TMKG_AUDIT_MODEL` if set, else `spotcheck_raters.judge_b` of `config/nim_models.yaml` (the faster of the
  two pre-registered NVIDIA judges). Without `NVIDIA_API_KEY` only gene-protein edges can be checked on demand (they need
  no model); every other edge without a stored record answers "unavailable".
- **Check after a deploy:** `curl -s https://<host>/api/pharos/audit/<an edge id from data/audit/edges/index.json>` must show
  `"origin": "stored"`; the same call for an edge id of any card must show `"origin": "on_demand"` or the unavailable
  sentence within about 10 seconds. The reason of an unavailable check is in `docker logs tmkg-app` (lines starting
  `[independent check]`), never in the answer.

## NIM on EC2 (self-hosted, optional)
The default is the hosted build.nvidia.com endpoint. To self-host: launch a GPU instance with the NVIDIA AMI (size per model card;
model ids in `config/nim_models.yaml`), `docker login nvcr.io` with the NGC key (on the instance only), run the NIM container
(`docker run --gpus all -e NGC_API_KEY -p 8000:8000 nvcr.io/nim/nvidia/<model>:latest`) and set `NIM_BASE_URL=http://<private-ip>:8000/v1`.
Changing the endpoint does not change the frozen request keys unless the model id changes.

## Schedule the monitor (EventBridge -> Batch/ECS)
`aws events put-rule --name tmkg-monthly --schedule-expression "cron(0 6 5 * ? *)"` and target a Batch job / ECS task that runs
`aws/monitor_task.sh` in this repository's container. A Lambda function is too small for the rebuild (15-minute limit, multi-GB AACT
files), so the schedule starts a container instead.


## The whole graph in Amazon Neptune (packets CC-17 and PK14)

`python pipelines/neptune_export.py` streams every built layer (Pharos, Open Targets, ChEMBL 37, the HPO / GO / MONDO
hierarchies and the trial layer, pruned edges left out) into `outputs/neptune/full/opencypher/nodes_000.csv.gz ...` and
`edges_000.csv.gz ...` (at most 1,000,000 rows per file; Neptune Database reads gzip, one text file per archive) and writes
`outputs/neptune/full/manifest.json` (file rows and sha256, counts, parity questions with the digest of their evidence edge ids).
These files are a deliverable in their own right (`outputs/neptune/full/README.md`, and the downloads of the hosted site): anyone
with a Neptune Database cluster can load them, and nothing in the app needs them.

Whether the graph is live in Amazon Neptune on the project's account, at what capacity, and how long the measured load took is
stated by Claude Science, with the commands it ran and their output, in the run records under `outputs/review/` (this file
states no such number; the records stay in the private repository and are left out of the public copy). The hosted app answers from its local card index by default either way; with `NEPTUNE_ENDPOINT` set
to the project cluster (`tmkg-neptune.cluster-...`) the app also learns the cluster's state from the host (PK14 A2: a timer on
the instance writes `/srv/neptune/status.json` every 20 s and starts a stopped cluster when the app leaves `wake.request`; the
container itself holds no AWS credentials and the host sends no query).

The order, when a cluster is used:

```bash
bash aws/upload_s3.sh                                   # outputs/ -> S3, including outputs/neptune/full/
MIN_NCU=8 MAX_NCU=128 bash aws/neptune_create.sh --io-optimized   # one cluster, I/O-Optimized storage, high ceiling for the load
bash aws/neptune_idle_stop.sh && bash aws/neptune_idle_stop.sh --pause   # idle stop installed, paused for the load
TMKG_NEPTUNE_S3_URI=s3://<bucket>/neptune/full/ bash aws/neptune_load.sh --full --remote   # nodes/ then edges/; exits 1 unless the counts equal the manifest
MIN_NCU=1 MAX_NCU=8 bash aws/neptune_create.sh --scale  # back down for serving (Serverless bills the capacity it uses)
bash aws/neptune_idle_stop.sh --resume && bash aws/neptune_idle_stop.sh --status
bash aws/schedule_stop_start.sh && bash aws/schedule_stop_start.sh --status
```

The loader role may read `s3://<bucket>/neptune/*` (and `trialmindkg/*`), so the files are copied under a `neptune/` prefix
with `nodes/` and `edges/` folders first. Load at a high maximum capacity (a Serverless cluster scales up to it while the loader
runs) and serve at a low one. I/O-Optimized storage can only be chosen when the cluster is created or changed with
`modify-db-cluster`; it is dearer per GB-month and has no per-I/O charge, which suits the load. Check the current prices in the
AWS console before you create anything (docs/AWS_COST_ESTIMATE_2026-11-04.md has the ones used so far).

## Cost control (the organizers' guidance, as implemented)

The organizers' guidance for a graph service that does not shut itself down, point by point, and where each point lives:

- **Minimum capacity as low as it goes:** the cluster is Serverless with a floor of 1 NCU (`MIN_NCU=1`, the default of
  `aws/neptune_create.sh`); the ceiling is raised only for the bulk load and set back with `--scale` (8 NCU when serving).
- **Idle-based stop:** `aws/neptune_idle_stop.sh` - a CloudWatch alarm on query activity (no query for 60 minutes) whose
  action is the small function `tmkg-idle-stop`, which stops the cluster; plus a sweep every 30 minutes for a cluster that was
  woken and never queried (an alarm acts only on a state change). The function stops only a cluster that is available, has
  run for the whole 60 minutes and saw no query in them. It is paused for a bulk load and resumed after it.
- **Schedule-based stop:** `aws/schedule_stop_start.sh` - `tmkg-neptune-stop` every night at 04:00 UTC (20:00 US Pacific,
  23:00 US Eastern), as the backstop; the start schedule stays disabled. A stopped cluster restarts by itself after 7 days,
  and the nightly stop stops it again.
- **No snapshots or test clusters kept:** one cluster (`tmkg-neptune`), the first load goes into it, no manual snapshots;
  `aws/teardown.sh` deletes the cluster without a final snapshot unless `--snapshot` is given, and the deployer may delete
  `tmkg-*` snapshots that exist.

Amazon CloudFront adds almost nothing to the bill at this traffic. Spending is watched by the budgets of step 1
(`docs/RUNBOOK_JUDGING.md`, section 6).
