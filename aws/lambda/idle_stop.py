"""Idle stop for the project's Amazon Neptune cluster (PK14 A4; deployed by aws/neptune_idle_stop.sh as the function tmkg-idle-stop).

The organizers' cost guidance: "set a CloudWatch alarm on query activity (e.g., no queries for 60 minutes) that triggers a small
Lambda function to stop the graph or cluster". Two things invoke this function: the alarm tmkg-neptune-idle (when it changes to
ALARM) and the schedule tmkg-neptune-idle-sweep every 30 minutes (an alarm only acts on a state CHANGE, so a cluster that was
woken and never queried would otherwise run until the nightly stop). Whatever invoked it, the function decides from the cluster
and its metrics alone, in this order:
  1. cluster status is not "available"                                -> nothing ("not running")
  2. fewer than IDLE_MINUTES / 5 five-minute datapoints of UPTIME_METRIC
     in the last IDLE_MINUTES                                           -> nothing ("running for less than the idle window")
  3. the sum of ACTIVITY_METRIC over the same window is above 0        -> nothing ("queries seen"); missing datapoints count as 0
  4. otherwise                                                         -> stop_db_cluster, {"action": "stopped"}
One JSON line is printed per run (CloudWatch Logs, log group /aws/lambda/tmkg-idle-stop).

Environment: CLUSTER_ID (tmkg-neptune), IDLE_MINUTES (60), ACTIVITY_METRIC (TotalRequestsPerSec), UPTIME_METRIC (CPUUtilization);
namespace AWS/Neptune, dimension DBClusterIdentifier. The two metric names were not verified against a live cluster (none existed
when this was written): confirm them with `aws cloudwatch list-metrics --namespace AWS/Neptune` and pass overrides through
NEPTUNE_ACTIVITY_METRIC / NEPTUNE_UPTIME_METRIC in aws/config.env.

Only the standard library and boto3 (present in the Lambda Python runtime) are used. Tests inject fake clients and a clock
(tests/test_idle_stop.py)."""
import json
import math
import os
from datetime import datetime, timedelta, timezone

NAMESPACE = "AWS/Neptune"
DIMENSION = "DBClusterIdentifier"
PERIOD = 300                      # seconds: one datapoint per five minutes


def settings(env):
    """Takes an environment mapping. Returns (cluster id, idle minutes, activity metric, uptime metric) with the defaults
    above; an idle time that is not a whole number of minutes of at least 5 falls back to 60."""
    try:
        idle = int(env.get("IDLE_MINUTES") or 60)
    except ValueError:
        idle = 60
    if idle < 5:
        idle = 60
    return (env.get("CLUSTER_ID") or "tmkg-neptune", idle,
            env.get("ACTIVITY_METRIC") or "TotalRequestsPerSec", env.get("UPTIME_METRIC") or "CPUUtilization")


def _datapoints(cloudwatch, cluster, metric, statistic, start, end):
    """Takes a CloudWatch client, the cluster id, a metric name, a statistic and the window. Returns the list of datapoints."""
    answer = cloudwatch.get_metric_statistics(
        Namespace=NAMESPACE, MetricName=metric, Dimensions=[{"Name": DIMENSION, "Value": cluster}],
        StartTime=start, EndTime=end, Period=PERIOD, Statistics=[statistic])
    return answer.get("Datapoints") or []


def _status(neptune, cluster):
    """Takes a Neptune client and the cluster id. Returns the cluster status ("absent" when the cluster does not exist)."""
    try:
        found = neptune.describe_db_clusters(DBClusterIdentifier=cluster).get("DBClusters") or []
    except Exception as err:          # botocore ClientError; only "not found" is an answer, anything else is a real failure
        code = str(getattr(err, "response", {}).get("Error", {}).get("Code", ""))
        if "NotFound" in code:
            return "absent"
        raise
    return (found[0].get("Status") or "unknown") if found else "absent"


def decide(neptune, cloudwatch, env, now):
    """Takes a Neptune client, a CloudWatch client, the environment and the current UTC time. Stops the cluster when it has
    been running for the whole idle window without a query. Returns (result, log) where result is the function's answer
    ({"action": "none", "why": ...} or {"action": "stopped"}) and log the fields of the one log line."""
    cluster, idle, activity_metric, uptime_metric = settings(env)
    log = {"cluster": cluster, "idle_minutes": idle, "activity_metric": activity_metric, "uptime_metric": uptime_metric}
    status = _status(neptune, cluster)
    log["status"] = status
    if status != "available":
        return {"action": "none", "why": "not running"}, log
    start, end = now - timedelta(minutes=idle), now
    need = math.ceil(idle / 5)
    uptime = len(_datapoints(cloudwatch, cluster, uptime_metric, "Average", start, end))
    log.update(uptime_datapoints=uptime, uptime_needed=need)
    if uptime < need:
        return {"action": "none", "why": "running for less than the idle window"}, log
    activity = sum(float(p.get("Sum") or 0.0) for p in _datapoints(cloudwatch, cluster, activity_metric, "Sum", start, end))
    log["activity_sum"] = activity
    if activity > 0:
        return {"action": "none", "why": "queries seen"}, log
    neptune.stop_db_cluster(DBClusterIdentifier=cluster)
    return {"action": "stopped"}, log


def handler(event, context, neptune=None, cloudwatch=None, env=None, now=None):
    """Lambda entry point. Takes the invoking event (alarm or schedule; only its source is logged) and the Lambda context;
    tests pass fake `neptune` / `cloudwatch` clients, an `env` mapping and a fixed `now`. Returns the decision of decide()."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    if neptune is None or cloudwatch is None:
        import boto3                  # only in the Lambda runtime; tests never import it
        neptune = neptune or boto3.client("neptune")
        cloudwatch = cloudwatch or boto3.client("cloudwatch")
    source = "alarm" if isinstance(event, dict) and "alarmData" in event else (
        (event or {}).get("source", "unknown") if isinstance(event, dict) else "unknown")
    result, log = decide(neptune, cloudwatch, env, now)
    print(json.dumps({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "invoked_by": source, **result, **log}, sort_keys=True))
    return result
