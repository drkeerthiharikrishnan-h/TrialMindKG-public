# Neptune cost guidance (received from the organizers' side by the owner, 2026-11-05)

What works best depends on which type of Neptune is running:

- **Neptune Analytics:** stop the graph whenever you're not using it. While stopped, it is charged about 10% of the normal
  compute rate, and it can be started again when needed (AWS docs: Stopping / Starting a Neptune Analytics graph).
- **Neptune Database (provisioned):** stop the cluster between work sessions. An idle cluster is still billed even with no
  queries running. A stopped cluster restarts on its own after 7 days, so it needs stopping again after that.
- **Neptune Serverless:** set the minimum capacity as low as it goes so it scales down when quiet; still stop it overnight
  (AWS docs: Capacity scaling in a Neptune Serverless DB cluster).

Neptune has no built-in "shut down after X minutes idle" setting. Two simple ways to do it yourself:

- **Idle-based:** a CloudWatch alarm on query activity (e.g., no queries for 60 minutes) that triggers a small Lambda function
  to stop the graph or cluster.
- **Schedule-based:** an EventBridge schedule that stops it every evening and starts it every morning.

Also delete any old snapshots or test clusters no longer needed. For current rates, see Amazon Neptune pricing.

## How TrialMindKG applies it (Claude Science, 2026-11-05)

| Guidance | Our kit | Status |
|---|---|---|
| Serverless, minimum capacity as low as it goes | `aws/neptune_create.sh` MIN_NCU=1 (raise MAX only for the bulk load) | in place |
| Stop overnight (EventBridge) + 7-day auto-restart | `aws/schedule_stop_start.sh` stops daily, so a self-restart is stopped again | in place |
| Idle stop: CloudWatch alarm (no queries for 60 min) -> Lambda stops the cluster | packet **CC-18** (`aws/idle_stop.sh`, Lambda `tmkg-idle-stop`) | to build |
| Delete old snapshots / test clusters | `aws/teardown.sh` skips the final snapshot by default; CC-18 adds listing + deletion of `tmkg-*` snapshots | to build |
| App keeps working while Neptune is stopped | CC-18: the app answers from its local card index and says the graph database is asleep | to verify/build |
| Neptune Analytics (stop = 10% rate) | not used: we run Neptune Database Serverless (openCypher bulk loader, kit already written) | decision kept |
