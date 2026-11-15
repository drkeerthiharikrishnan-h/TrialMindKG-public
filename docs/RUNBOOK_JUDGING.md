# Runbook for the judging period

For the owner and for any Claude Science session called in when something is wrong with the hosted site,
https://tmkg.16-178-197-241.sslip.io/ . Written Thu 12 Nov 2026 (task S09). It says how a fault is noticed, who does what,
and how long recovery takes. Numbers marked *measured* come from the dated run records under `outputs/review/` (S09 alarms and model-down
rehearsal) or the hand-off board (`docs/handoff/HANDOFF_2026-11-11.md`, section 3); both stay in the private repository and
are left out of the public copy. Numbers marked *by design* are settings in the code that have not been rehearsed.

The site is one server with no spare. The submission does not rest on it alone: the repository runs a small demo on a
laptop (`HOW_TO_RUN.md`) and the video shows the site working.

## 1. Who does what

| Who | Can do | Does |
|---|---|---|
| Owner | AWS console (browser or phone), a browser on the site, NVIDIA account, Terminal on the Mac | Receives the alarm and monitor mails; decides; reboots the server from the console; starts a Claude Science session with the prompt in section 8; rotates the NVIDIA key; merges and pushes |
| Claude Science session | AWS calls with the deployer key, the Mac's data, the hosted site | Diagnoses with the checks below; relaunches the site (hand-off section 2, REDEPLOY); runs the post-deploy checklist; re-points the status alarm. Shows every AWS call before it runs |
| Claude Code session | Code on a branch | Only when the fault is a bug in the code: a fix on a branch that the owner merges; then a relaunch by Claude Science |

Nobody types commands on the server from memory. The two routes that are practised are a reboot from the console and a
relaunch by the documented procedure.

## 2. How a fault is noticed

| Signal | Set up | Sees | Does not see | Delay |
|---|---|---|---|---|
| Status alarm `tmkg-app-status-check` (mail from the topic `tmkg-app-status`) | `bash aws/status_alarm.sh`, 12 Nov; the owner's address subscribed in the console | The server failing its AWS status checks, or stopped, or terminated | The app or the front door failing on a healthy server; the model | 2 minutes of failed checks (*by design*), then the mail |
| External monitor on `GET /api/pharos/status` | Owner, section 7 | Everything a visitor needs to load the page: name resolution, certificate, front door, app, the 20 GB index | The model; the downloads | The monitor's interval (5 minutes on the free plan of the service named in section 7) |
| Budget mails | AWS Budgets, section 6 | Spending | Everything else | AWS updates budgets a few times a day |
| A person | Daily check, section 7 | The model not answering; a broken download | | Up to a day |

The model is the gap: nothing automatic reports that the model stopped answering (*measured*: with the model endpoint
dead, `/api/pharos/status` and 39 other addresses still answer 200 and make no model call).

## 3. First two minutes, whatever the signal

1. Open https://tmkg.16-178-197-241.sslip.io/api/pharos/status in a browser. Normal: a page of JSON that contains
   `"edges"` followed by a number (29680679 on 12 Nov), in about 1.4 s (*measured* 12 Nov from the Mac).
2. If that works, open the site, click one tested question, then type one question. Tested question fine and typed
   question says "The hosted model did not answer": go to case C. Downloads broken: case D.
3. If step 1 fails, look for the alarm mail. Alarm mail present: case A. No alarm mail: case B.
4. From the Mac, without any question being asked on the site: `make hosted-check` (GET requests only).

## 4. The cases

### Case A. The server is down (status alarm mail, site unreachable)

- What it is: AWS reports the virtual server unhealthy, or it was stopped or terminated.
- Owner, at once: AWS console, region Asia Pacific (Sydney), EC2, Instances, the instance named `tmkg-app`.
  - State "running" with a failed status check: Instance state, Reboot. The address, the disk with the index and the
    certificate stay; both containers start again by themselves. Wait for the "OK" mail from the alarm, then repeat
    section 3. Time: not measured (a reboot has not been rehearsed); no data is downloaded again, so expect minutes.
  - State "stopped": Instance state, Start.
  - No instance, or "terminated", or the reboot did not help within 15 minutes: relaunch (next line).
- Relaunch, by a Claude Science session (prompt in section 8): the REDEPLOY procedure of the hand-off, section 2, then
  the POST-DEPLOY CHECKLIST, then `bash aws/status_alarm.sh` and `bash aws/status_alarm.sh --status`.
  Time: 6 min 3 s from the launch call to the first answer (*measured* Wed 11 Nov, board line S02: launched 20:45:49,
  first answer 20:51:52 IST; the server downloads and unpacks the index in that time). The steps before the launch
  (starting the session, approvals, uploading the bundle) were not timed: plan for about half an hour from the alarm
  mail to a checked site. The link does not change. The first relaunch that carries the Wednesday security changes
  (the image built from the lock file on pinned bases, the swap file, the heal timer, capped container logs) was task
  S06: 6 min 23 s from the launch call to the first answer (*measured* Thu 13 Nov, board line S06: launch 10:27:31,
  first answer 10:33:54 IST; the on-boot image build is inside that time), so the PK1 image does not slow a relaunch
  beyond the earlier 6 min 3 s by more than about 20 s.
- A relaunch sends one ALARM mail and one OK mail by itself (the old server disappears, the alarm is re-pointed).

### Case B. The server is healthy but the site does not answer (monitor mail, no alarm mail)

Work down this list; stop at the first that explains it.

1. Does the name resolve? On the Mac: `dig +short tmkg.16-178-197-241.sslip.io` must print `16.178.197.241`. The name
   is served by the public sslip.io service. If it prints nothing, that service is down: nothing on our side is
   broken and nothing on our side fixes it. Wait; do not change the link during judging (a new name needs a new
   certificate and every document quotes the old one).
2. Certificate: `curl -sv https://tmkg.16-178-197-241.sslip.io/ 2>&1 | grep 'expire date'`. *Measured* 12 Nov: valid
   from 9 Nov 2026 to 7 Feb 2027, so expiry is not expected in the judging period.
3. The app stopped answering while the server is fine. Servers launched from 13 Nov on heal this themselves: the
   container is checked every 30 s, marked unhealthy after 3 failures, and a timer restarts an unhealthy container
   once a minute (*by design*; about 2 to 3 minutes; not rehearsed). Direct confirmation that the timer is active
   needs instance access (the deployer key may not send SSM commands); the boot proceeded past the line that enables
   it, since every later boot step ran. To confirm: Session Manager, `systemctl is-active tmkg-heal.timer`.
4. Owner: reboot from the console as in case A. If the site is not back 15 minutes later: relaunch as in case A.
5. For the Claude Science session, to find the cause before or after the relaunch: EC2 console, the instance,
   Actions, Monitor and troubleshoot, Get system log; or a Session Manager shell (`sudo docker ps`,
   `sudo docker logs --tail 100 tmkg-app`, `sudo docker logs --tail 50 caddy`, `df -h /`). Read only; fix by relaunch.

### Case C. The model does not answer (site up, typed questions fail)

What a visitor gets (*measured* in the local rehearsal, seven kinds of failure, 28 of 28 answered 200):

| Visitor action | Result while the model is down |
|---|---|
| Tested questions, stored answers, cards, graph, searches, predictions, downloads list | Unchanged; no model is called (40 of 40 requests) |
| A tested question typed into the ask box | Answered with rows as usual (5 of 5) |
| Any other typed question about the graph | "The hosted model did not answer, so this question was not run on the graph. Try again in a minute, or use one of the tested questions." At once for a rejected key or a rate limit, after 6 s when the endpoint is unreachable or answers 500 or 503, after 75 s when the model accepts the question and never replies |
| A typed question about a trial | Answered by fixed rules, with a label saying so (up to 34 s; 75 s when the model never replies) |
| Own table, draft the mapping | The fixed rules draft instead of the model, and the answer says so |
| Independent check on a link (from the next relaunch) | A link checked ahead of time: unchanged. Any other link: "independent check unavailable right now" |

Find the cause. On the Mac: `bin/agent-cli nim-check` (it uses the key in the Mac's `.env`, which must be the same key
as the server's).

| `nim-check` says | Cause | Who, what | Recovery time |
|---|---|---|---|
| All models OK on the Mac, site still fails | The server holds another key than the Mac (a rotation without a relaunch) | Owner stores the current key (`aws/nim_key_ssm.sh`, as in `aws/README.md` step 3); Claude Science relaunches | As a relaunch, case A |
| 401 or 403 on every model | The key was rotated, revoked or has expired | Owner makes a new key at build.nvidia.com, stores it (step above), puts it into the Mac's `.env`, runs `nim-check`; Claude Science relaunches | New key plus a relaunch |
| 429 | Rate limit of the key, or its credits are used up | Stop every batch job that uses the same key (the audit ratings measured the key at about 15 to 20 calls a minute). Check the credit balance at build.nvidia.com. No credits: a key from an account that has some, then as the row above | Minutes for a rate limit; a relaunch for a new key |
| Timeouts, 500, 503, or an error for one model only | NVIDIA's side | Nobody: the site keeps working as in the table above. Check again in 30 minutes. If one model id was withdrawn: Claude Code changes the id in `config/nim_models.yaml`, then a relaunch | Not in our hands |

Rule for a planned key rotation: new key, then `aws/nim_key_ssm.sh`, then the Mac's `.env`, then `nim-check`, then the
relaunch, in that order and in one sitting. The running server keeps the key it started with, so between the rotation
and the relaunch every typed question on the site fails.

### Case D. Downloads

The four folders (`kgx/`, `neptune/`, `pyg/`, `verdicts/`) are served by the front door from the server's disk, copied
from the project's storage bucket at launch. Each has a `SHA256SUMS` file; the sums are printed in `HOW_TO_RUN.md`.

| Report | Check | Fix | Who |
|---|---|---|---|
| A large download broke off | Nothing to check: the server supports resuming (*measured*: a range request answers 206) | Tell the person to resume: `curl -C - -O <link>` | Owner |
| Checksum does not match | `curl -s https://tmkg.16-178-197-241.sslip.io/downloads/kgx/SHA256SUMS` (and the other three) against `HOW_TO_RUN.md` | Same sums: the person's copy is incomplete, resume or repeat. Different sums: the server's copy differs from the published one; relaunch, and the post-deploy checklist compares the server's sums with the local export files | Owner, then Claude Science |
| The downloads section is missing from the page, or `/downloads/` is empty or 404 | `curl -s https://tmkg.16-178-197-241.sslip.io/api/downloads` shows `"available": false` | The copy at launch failed. Relaunch; the checklist compares all sums | Claude Science |
| The whole site is down and a judge needs a file now | | Time-limited direct links to the files in the storage bucket (valid at most 7 days), made by a Claude Science session and sent by the owner | Claude Science, owner |

### Case E. Outside sources are down

- The server running on 12 Nov calls one outside service while answering: the model (case C). Links on the page to
  the sources (ClinicalTrials.gov, CTD and the others) open in the visitor's browser; a source that is down breaks its
  link, not the site.
- From the next relaunch the independent check on a link also calls outside sources (the hosts are listed in
  `aws/README.md`). *Measured* in the local rehearsal (merged 13 Nov, not yet deployed): a link checked ahead of time
  is answered from its stored record with no outside call (3 of 3); for any other link every failure, of a source or
  of the model, gives HTTP 200 and the one sentence "independent check unavailable right now" (5 of 5), and a second
  request for the same link within a minute makes no call. One live check has 10 s in total (*by design*). So a
  source outage needs no action; the check works again when the source does. The stored records must be on the
  server for this (task S08 uploads them).
- Open point for S08: with the sources down and the model up, the judge is still asked (rehearsal record, section 6,
  item 5). Until S08 has checked what it then answers, treat a surprising "unsupported" on a link that was not checked
  ahead of time, during a source outage, as unconfirmed.
- To tell "their side" from "our side": from the Mac, `curl -s -o /dev/null -w '%{http_code}\n' https://www.ebi.ac.uk/`
  (and the other hosts). They answer from the Mac but the site reports them unavailable for more than an hour: treat
  as case B, item 5.
- The front door itself depends on two outside services: sslip.io for the name (case B, item 1) and Let's Encrypt for
  the certificate (renewal is not due before January 2027).

### Case F. Amazon Neptune asleep or unavailable

Amazon Neptune is an addition (owner decision D1): every answer comes from the server's own graph first, and nothing on the
site depends on Amazon Neptune. The server reads the cluster's state every 20 s into `/srv/neptune/status.json` and starts
a stopped cluster when the page leaves a wake request there; the app itself holds no AWS credentials. Numbers marked
*measured* come from the S16 and S17 run records under `outputs/review/` (private repository; not in the public copy) and
`outputs/neptune/live_parity.json`.

| Report | Check | Fix | Who |
|---|---|---|---|
| The answer says "Amazon Neptune is asleep to save cost" | `curl -s https://tmkg.16-178-197-241.sslip.io/api/neptune/status` shows `"state":"asleep"` | Nothing is wrong: it was stopped on purpose (idle stop, nightly stop, or by hand). Wake it from the page, or `curl -s -X POST .../api/neptune/wake`; it is available about 11 minutes later (*measured*: 616 s from the start call, 648 s from the click). Each wake costs money: wake it for a judge or a planned check, not to test | Anyone |
| Right after a wake the status shows "asleep" again for one poll | Known (*measured* once, S17): the server took the request and started the cluster, but Amazon Neptune still reported `stopped` for a few seconds | None: the next poll shows "waking". Do not click "Wake it" again | Anyone |
| "unavailable" for more than 10 minutes | `aws neptune describe-db-clusters --db-cluster-identifier tmkg-neptune --region ap-southeast-2 --query 'DBClusters[0].Status'`; on the server (Run Command) `cat /srv/neptune/status.json; tail /var/log/tmkg-neptune.log` | `stopping` or `starting`: wait (*measured*: a stop takes about 6.5 minutes, 387 s). `unknown` in status.json while the cluster is fine: the server cannot read the state (its role or the network); a relaunch restores the server side. A wake log line `start-db-cluster FAILED`: start it by hand with `aws neptune start-db-cluster --db-cluster-identifier tmkg-neptune --region ap-southeast-2` (show the call first) | Claude Science |
| A question on Amazon Neptune is slow or falls back | The answer's line says the answer came from the server's graph | By design: after 20 s the server's graph answers. A cold cache can take longer than that (*measured*: one ready question 76.2 s on its first call, 5.1 s on the repeat) | None |

Before any reload of the graph into Amazon Neptune (a bulk load is not query activity, so the idle stop would stop the
cluster in the middle of it): `bash aws/neptune_idle_stop.sh --pause` (environment `aws`, credential `AWS`; show the dry
run first), load, then `bash aws/neptune_idle_stop.sh --resume` and `--status`, which must exit 0. The pause leaves the
nightly stop `tmkg-neptune-stop` (23:00 IST) in place: do not start a load that can run past it without the owner's word.

## 5. Recovery times at a glance

| Fault | Recovery | Basis |
|---|---|---|
| Server unhealthy, reboot helps | Minutes | Not measured |
| Server gone or reboot does not help | 6 min 23 s from launch to first answer with the current image (6 min 3 s with the old one); about half an hour from the mail to a checked site | Launch times *measured* 11 and 13 Nov; the rest an estimate |
| App hung on a healthy server | 2 to 3 minutes, by itself, on servers launched from 13 Nov; before, a reboot | *By design*, not rehearsed; timer active not yet confirmed on the server (case B, item 3) |
| Model outage at NVIDIA | Not in our hands; the site degrades as in case C | Rehearsed locally |
| Key rejected | A new key plus a relaunch | Relaunch time as above |
| Download fault | A resume, or a relaunch | |
| Outside source down | None needed | As specified; not yet merged |
| Amazon Neptune asleep | About 11 minutes after a wake from the page | *Measured* 616 s (S16) and 648 s from the click (S17) |
| Amazon Neptune unavailable or slow | None needed: the server's own graph answers, with a note | *Measured* on the hosted site 14 Nov (S17); page journey in the harness |

## 6. Spending

- Two monthly budgets exist and mail the owner (*measured* 12 Nov): `tmkg-monthly`, 150 USD, at 33%, 67% and 93% of
  actual spend and at 100% forecast; "My Monthly Cost Budget", 50 USD, at 86% and 100% actual and 100% forecast.
- `aws/budget_alarms.sh` cannot run on this account: the account's organization forbids the notification service in
  the one region where billing figures exist (*measured* 12 Nov: the first call is refused). The budgets above are the
  spending alarm.
- `tmkg-monthly` counts credits and showed 0.0 USD on 12 Nov while the other budget showed 7.616 USD: it will most
  likely stay silent until the credit is used up. Owner decision: section 2 of the S09 record.
- Amazon Neptune is billed for every hour it runs, and it runs at its 8-unit ceiling even when idle, not at the 1-unit
  floor: about USD 2.10 an hour (*measured* by S16: USD 0.262 per unit-hour in Sydney, I/O-Optimized). Three guards stop
  it: (1) the idle stop, function `tmkg-idle-stop`, called by the alarm `tmkg-neptune-idle` (no query for 60 minutes) and
  every 30 minutes by the schedule `tmkg-neptune-idle-sweep`; it stops the cluster only when it has run for the whole
  hour without a query, so a woken, unused cluster stops 60 to 90 minutes after the wake (*by design*; all three
  callers *measured* working on 14 Nov, S17); (2) the nightly stop `tmkg-neptune-stop`, 23:00 IST; (3) until the
  submission, the session that used it stops it by hand (owner decision D7). Check: `bash aws/neptune_idle_stop.sh
  --status` (exit 0) and the cluster status above. Storage is billed while it is stopped. No manual snapshots and no
  second cluster are kept.
- When a budget mail arrives: Billing console, Cost Explorer, group by service. The things that can grow are data
  leaving the server (downloads of the large files), the server itself, and Amazon Neptune left running (above). The model is billed by NVIDIA, not AWS.

## 7. Before judging starts, and each day of it

Once (owner unless noted):

1. Subscribe your address to the topic `tmkg-app-status` (console, Sydney, Simple Notification Service, Topics,
   Create subscription, Email) and confirm the mail. Then `bash aws/status_alarm.sh --status` must exit 0 (Claude
   Science, or Terminal).
2. External monitor: a free account at a monitoring service (for example UptimeRobot: 5-minute checks on the free
   plan). Monitor type **keyword**, address `https://tmkg.16-178-197-241.sslip.io/api/pharos/status`, keyword
   `"edges"`, alert when the keyword is missing, alert contact your e-mail. Use the keyword type, not the plain type:
   it reads the answer, and on the server running on 12 Nov a request without a body read (HEAD) is refused with 405
   although the site is fine (*measured*).
3. After every relaunch: `bash aws/status_alarm.sh`, then `--status` (Claude Science; it is in the hand-off checklist).
4. Keep the Mac's `.env` key and the server's key the same (case C).

Each day, 2 minutes (owner): open the site; click one tested question; type one question (it uses 1 of your 20 an
hour); open `/downloads/`. Anything wrong: section 3.

## 8. Prompt for an emergency Claude Science session

    Grant ~/projects/TrialMindKG. Read docs/RUNBOOK_JUDGING.md, then docs/handoff/HANDOFF_2026-11-11.md section 2. The hosted site has a fault since <time>: <alarm mail / monitor mail / what the browser shows>. I have already <rebooted it / done nothing>. Diagnose with the runbook's checks, tell me which case it is, show me every AWS call before it runs, recover, then run the post-deploy checklist and bash aws/status_alarm.sh. Do nothing else. End with what happened, what you changed and my commit block.
