"""aws/status_alarm.sh (S09) without AWS. Dry runs print every call and make none (a stub `aws` that fails the test when
it is executed); real runs go against a stub `aws` that answers from environment variables and logs each call. Checked:
one alarm on StatusCheckFailed of the one running instance, in the app's region, reporting to the tagged topic; nobody is
ever subscribed and no e-mail address is read; --status is read-only and exits 0 only when the alarm watches the running
instance and the topic has a confirmed subscription; teardown removes the alarm and the topic."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AWS = ROOT / "aws"
SCRIPT = AWS / "status_alarm.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not found")

CONFIG = ('AWS_REGION=ap-southeast-2\nAWS_ACCOUNT_ID=123456789012\nTMKG_BUCKET=b\n'
          'ALERT_EMAIL=someone@example.org\n')

# A stub aws for real (not dry) runs: logs the call, answers the four calls the script makes from STUB_* variables.
ANSWERING_STUB = r'''#!/bin/sh
echo "aws $*" >> "$STUB_LOG"
case "$*" in
  *"ec2 describe-instances"*) echo "$STUB_INSTANCES" ;;
  *"sns create-topic"*) echo "arn:aws:sns:ap-southeast-2:123456789012:tmkg-app-status" ;;
  *"cloudwatch describe-alarms"*) echo "$STUB_ALARM" ;;
  *"sns get-topic-attributes"*) echo "$STUB_SUBS" ;;
  *"cloudwatch put-metric-alarm"*) ;;
  *) echo "unexpected call: $*" >&2; exit 97 ;;
esac
'''


def _bin(tmp_path, body):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "aws"
    f.write_text(body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return bindir


def _run(tmp_path, bindir, *args, **stub_env):
    cfg = tmp_path / "config.env"
    cfg.write_text(CONFIG)
    env = {**os.environ, "TMKG_AWS_CONFIG": str(cfg), "STUB_LOG": str(tmp_path / "calls.log"), **stub_env}
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT)


def _calls(tmp_path):
    log = tmp_path / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def refusing(tmp_path):
    """An aws that must never run: a dry run that reaches it leaves a line in the call log."""
    return _bin(tmp_path, '#!/bin/sh\necho "aws $*" >> "$STUB_LOG"\nexit 99\n')


@pytest.fixture
def answering(tmp_path):
    return _bin(tmp_path, ANSWERING_STUB)


def test_script_parses_and_is_executable():
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert os.access(SCRIPT, os.X_OK)


def test_dry_run_prints_one_alarm_on_the_status_checks_and_calls_nothing(tmp_path, refusing):
    r = _run(tmp_path, refusing, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-800:]
    assert _calls(tmp_path) == [], "the dry run executed aws"
    assert "DRY RUN" in out
    alarm = [l for l in out.splitlines() if l.startswith("+ aws cloudwatch put-metric-alarm")]
    assert len(alarm) == 1
    line = alarm[0]
    for part in ("--alarm-name tmkg-app-status-check", "--namespace AWS/EC2", "--metric-name StatusCheckFailed",
                 "Name=InstanceId,Value=i-0example", "--statistic Maximum", "--period 60", "--evaluation-periods 2",
                 "--threshold 1", "--comparison-operator GreaterThanOrEqualToThreshold",
                 "--treat-missing-data breaching",          # a stopped or terminated instance reports nothing: that is an alarm
                 "--alarm-actions arn:aws:sns:ap-southeast-2:123456789012:tmkg-app-status",
                 "--ok-actions arn:aws:sns:ap-southeast-2:123456789012:tmkg-app-status",
                 "--region ap-southeast-2"):                # the app's region, not the billing region
        assert part in line, part
    assert "us-east-1" not in out


def test_the_topic_is_tagged_and_nobody_is_subscribed(tmp_path, refusing):
    r = _run(tmp_path, refusing, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and _calls(tmp_path) == []
    topic = [l for l in out.splitlines() if l.startswith("+ aws sns create-topic")]
    assert len(topic) == 1 and "--name tmkg-app-status" in topic[0]
    assert "project" in topic[0] and "trialmindkg" in topic[0]
    # the owner subscribes her own address in the console: the script neither subscribes nor reads the configured address
    assert not [l for l in out.splitlines() if l.startswith("+ aws") and " subscribe " in l + " "]
    assert "someone@example.org" not in out
    assert "ALERT_EMAIL" not in "\n".join(l for l in SCRIPT.read_text().splitlines() if not l.lstrip().startswith("#"))


def test_status_dry_run_makes_only_read_calls(tmp_path, refusing):
    r = _run(tmp_path, refusing, "--status", "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-800:]
    assert _calls(tmp_path) == []
    verbs = [l.split()[3] for l in out.splitlines() if l.startswith("+ aws ")]
    assert sorted(verbs) == ["describe-alarms", "describe-instances", "get-topic-attributes"]


def test_unknown_argument_is_refused(tmp_path, refusing):
    r = _run(tmp_path, refusing, "--dry-run", "--subscribe")
    assert r.returncode == 2 and "unknown argument" in r.stderr and _calls(tmp_path) == []


def test_real_run_points_the_alarm_at_the_running_instance(tmp_path, answering):
    r = _run(tmp_path, answering, STUB_INSTANCES="i-0abc123")
    assert r.returncode == 0, r.stderr[-800:]
    calls = _calls(tmp_path)
    assert [c.split()[2] for c in calls] == ["describe-instances", "create-topic", "put-metric-alarm"]
    assert "Name=InstanceId,Value=i-0abc123" in calls[2]
    assert "Name=instance-state-name,Values=pending,running" in calls[0] and "Name=tag:Name,Values=tmkg-app" in calls[0]
    assert not [c for c in calls if " subscribe " in c + " "]
    assert "watches i-0abc123" in r.stdout


@pytest.mark.parametrize("instances, message", [("", "no pending or running instance"), ("None", "no pending or running instance"),
                                                 ("i-0old i-0new", "more than one instance")])
def test_real_run_stops_before_any_change_without_exactly_one_instance(tmp_path, answering, instances, message):
    r = _run(tmp_path, answering, STUB_INSTANCES=instances)
    assert r.returncode == 1 and message in r.stderr
    assert [c.split()[2] for c in _calls(tmp_path)] == ["describe-instances"]      # no topic, no alarm


@pytest.mark.parametrize("alarm, subs, code, said", [
    ("i-0abc123 OK", "1 0", 0, "watches the running instance, state OK"),
    ("i-0abc123 ALARM", "1 0", 0, "state ALARM"),                                  # the state is reported, not judged
    ("i-0old OK", "1 0", 1, "STALE, it watches i-0old"),                           # a relaunch without a re-point
    ("None", "1 0", 1, "MISSING"),
    ("i-0abc123 OK", "0 1", 1, "nobody is told"),                                  # subscribed but not confirmed
    ("i-0abc123 OK", "0 0", 1, "nobody is told"),
])
def test_status_exit_code_says_whether_the_alarm_can_reach_someone(tmp_path, answering, alarm, subs, code, said):
    r = _run(tmp_path, answering, "--status", STUB_INSTANCES="i-0abc123", STUB_ALARM=alarm, STUB_SUBS=subs)
    assert r.returncode == code, (r.stdout, r.stderr)
    assert said in r.stdout
    calls = [c.split()[2] for c in _calls(tmp_path)]
    assert calls == ["describe-instances", "describe-alarms", "get-topic-attributes"]   # read-only


def test_teardown_removes_the_status_alarm_and_its_topic(tmp_path, refusing):
    cfg = tmp_path / "config.env"
    cfg.write_text(CONFIG)
    env = {**os.environ, "TMKG_AWS_CONFIG": str(cfg), "STUB_LOG": str(tmp_path / "calls.log")}
    env["PATH"] = f"{refusing}{os.pathsep}{env['PATH']}"
    r = subprocess.run([BASH, str(AWS / "teardown.sh"), "--dry-run"], capture_output=True, text=True, env=env, cwd=ROOT)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-800:]
    assert _calls(tmp_path) == []
    assert "delete-alarms --alarm-names tmkg-app-status-check --region ap-southeast-2" in out
    assert "sns delete-topic --topic-arn arn:aws:sns:ap-southeast-2:123456789012:tmkg-app-status --region ap-southeast-2" in out


def test_the_readme_and_the_runbook_name_the_script():
    assert "bash aws/status_alarm.sh" in (AWS / "README.md").read_text()
    runbook = (ROOT / "docs" / "RUNBOOK_JUDGING.md").read_text()
    assert "aws/status_alarm.sh" in runbook and "/api/pharos/status" in runbook
