"""PK14 A4: the idle-stop function aws/lambda/idle_stop.py with fake Neptune / CloudWatch clients (no boto3, no AWS).
The four branches in their order (not running; running for less than the idle window; queries seen; stopped), the metric
requests it makes, missing activity datapoints counting as zero, the defaults and overrides, and the one JSON log line."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("idle_stop", ROOT / "aws" / "lambda" / "idle_stop.py")
idle_stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idle_stop)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


class NotFound(Exception):
    """Shaped like botocore's ClientError for a missing cluster."""
    response = {"Error": {"Code": "DBClusterNotFoundFault"}}


class FakeNeptune:
    def __init__(self, status="available", missing=False):
        self.status, self.missing, self.stopped = status, missing, []

    def describe_db_clusters(self, DBClusterIdentifier):
        if self.missing:
            raise NotFound()
        return {"DBClusters": [{"DBClusterIdentifier": DBClusterIdentifier, "Status": self.status}]}

    def stop_db_cluster(self, DBClusterIdentifier):
        self.stopped.append(DBClusterIdentifier)
        return {}


class FakeCloudWatch:
    """uptime = number of 5-minute datapoints of the uptime metric; activity = list of Sum values of the activity metric."""
    def __init__(self, uptime=12, activity=(), uptime_metric="CPUUtilization"):
        self.uptime, self.activity, self.uptime_metric, self.calls = uptime, list(activity), uptime_metric, []

    def get_metric_statistics(self, **kw):
        self.calls.append(kw)
        if kw["MetricName"] == self.uptime_metric:
            return {"Datapoints": [{"Average": 3.0} for _ in range(self.uptime)]}
        return {"Datapoints": [{"Sum": v} for v in self.activity]}


def call(neptune, cw, env=None, event=None):
    return idle_stop.handler(event or {"source": "tmkg-neptune-idle-sweep"}, None, neptune=neptune, cloudwatch=cw,
                             env=env or {}, now=NOW)


def test_a_cluster_that_is_not_available_is_left_alone():
    for status in ("stopped", "starting", "stopping", "modifying"):
        nep, cw = FakeNeptune(status), FakeCloudWatch()
        assert call(nep, cw) == {"action": "none", "why": "not running"}
        assert nep.stopped == [] and cw.calls == []                    # no metric is even read


def test_a_missing_cluster_counts_as_not_running():
    nep = FakeNeptune(missing=True)
    assert call(nep, FakeCloudWatch()) == {"action": "none", "why": "not running"}


def test_other_errors_from_neptune_are_raised_not_hidden():
    class Boom(FakeNeptune):
        def describe_db_clusters(self, DBClusterIdentifier):
            raise RuntimeError("AccessDenied")
    with pytest.raises(RuntimeError):
        call(Boom(), FakeCloudWatch())


def test_a_cluster_up_for_less_than_the_window_is_left_alone():
    nep, cw = FakeNeptune(), FakeCloudWatch(uptime=11)                # 55 of the 60 minutes
    assert call(nep, cw) == {"action": "none", "why": "running for less than the idle window"}
    assert nep.stopped == [] and len(cw.calls) == 1                   # the activity metric is not read


def test_queries_in_the_window_keep_it_running():
    nep, cw = FakeNeptune(), FakeCloudWatch(activity=[0.0, 0.0, 0.2, 0.0])
    assert call(nep, cw) == {"action": "none", "why": "queries seen"}
    assert nep.stopped == []


def test_a_whole_idle_window_stops_the_cluster():
    nep, cw = FakeNeptune(), FakeCloudWatch(activity=[0.0] * 12)
    assert call(nep, cw) == {"action": "stopped"}
    assert nep.stopped == ["tmkg-neptune"]


def test_missing_activity_datapoints_count_as_zero():
    nep, cw = FakeNeptune(), FakeCloudWatch(activity=[])              # no datapoint at all = no query
    assert call(nep, cw) == {"action": "stopped"}


def test_the_metric_requests_cover_exactly_the_idle_window_in_five_minute_periods():
    nep, cw = FakeNeptune(), FakeCloudWatch()
    call(nep, cw)
    uptime, activity = cw.calls
    for c in (uptime, activity):
        assert c["Namespace"] == "AWS/Neptune" and c["Period"] == 300
        assert c["Dimensions"] == [{"Name": "DBClusterIdentifier", "Value": "tmkg-neptune"}]
        assert c["EndTime"] == NOW and c["StartTime"] == NOW - timedelta(minutes=60)
    assert uptime["MetricName"] == "CPUUtilization" and uptime["Statistics"] == ["Average"]
    assert activity["MetricName"] == "TotalRequestsPerSec" and activity["Statistics"] == ["Sum"]


def test_environment_overrides_the_cluster_window_and_metric_names():
    env = {"CLUSTER_ID": "tmkg-neptune-2", "IDLE_MINUTES": "30", "ACTIVITY_METRIC": "OpenCypherRequestsPerSec",
           "UPTIME_METRIC": "ServerlessDatabaseCapacity"}
    nep, cw = FakeNeptune(), FakeCloudWatch(uptime=6, uptime_metric="ServerlessDatabaseCapacity")
    assert call(nep, cw, env=env) == {"action": "stopped"}
    assert nep.stopped == ["tmkg-neptune-2"]
    assert [c["MetricName"] for c in cw.calls] == ["ServerlessDatabaseCapacity", "OpenCypherRequestsPerSec"]
    assert cw.calls[0]["StartTime"] == NOW - timedelta(minutes=30)
    nep2, cw2 = FakeNeptune(), FakeCloudWatch(uptime=5, uptime_metric="ServerlessDatabaseCapacity")
    assert call(nep2, cw2, env=env)["why"] == "running for less than the idle window"      # 30 / 5 = 6 needed


def test_settings_defaults_and_bad_values():
    assert idle_stop.settings({}) == ("tmkg-neptune", 60, "TotalRequestsPerSec", "CPUUtilization")
    assert idle_stop.settings({"IDLE_MINUTES": "soon"})[1] == 60
    assert idle_stop.settings({"IDLE_MINUTES": "2"})[1] == 60
    assert idle_stop.settings({"IDLE_MINUTES": "", "ACTIVITY_METRIC": ""})[1:3] == (60, "TotalRequestsPerSec")


def test_one_json_log_line_per_run(capsys):
    call(FakeNeptune(), FakeCloudWatch(activity=[1.0]), event={"alarmData": {"state": {"value": "ALARM"}}})
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["action"] == "none" and rec["why"] == "queries seen" and rec["invoked_by"] == "alarm"
    assert rec["cluster"] == "tmkg-neptune" and rec["status"] == "available" and rec["activity_sum"] == 1.0
    assert rec["at"] == "2026-11-14T12:00:00Z"
    call(FakeNeptune("stopped"), FakeCloudWatch())
    assert json.loads(capsys.readouterr().out)["invoked_by"] == "tmkg-neptune-idle-sweep"


def test_the_module_needs_no_boto3_when_clients_are_given():
    src = (ROOT / "aws" / "lambda" / "idle_stop.py").read_text()
    assert "\nimport boto3" not in src and "import boto3" in src         # imported lazily, inside handler() only
