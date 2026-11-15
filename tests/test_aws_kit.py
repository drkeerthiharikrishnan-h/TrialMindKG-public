"""aws/ kit (CC-5) without AWS: every script runs with --dry-run, prints each call and makes none (a stub `aws` that fails the
test if it is ever executed sits first on PATH), resources are tagged, policies parse, the NIM key stays off command lines,
neptune_load.sh fails on a count mismatch against a stubbed endpoint, shellcheck is clean when it is installed."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AWS = ROOT / "aws"
SCRIPTS = ["budget_alarms", "iam_setup", "nim_key_ssm", "upload_s3", "neptune_create", "neptune_load", "deploy_app",
           "schedule_stop_start", "teardown", "cloudfront_front", "neptune_idle_stop"]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not found")


@pytest.fixture
def stubs(tmp_path):
    """A bin folder whose aws / awscurl / curl write a marker and exit 99: a dry run must never reach them."""
    bindir, marker = tmp_path / "bin", tmp_path / "called"
    bindir.mkdir()
    for name in ("aws", "awscurl", "curl", "docker"):
        f = bindir / name
        f.write_text(f'#!/bin/sh\necho "{name} $*" >> "{marker}"\nexit 99\n')
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return bindir, marker


def run(script, *args, env_extra=None, stubs_=None, cwd=ROOT):
    env = {**os.environ, **(env_extra or {})}
    env["PATH"] = f"{stubs_[0]}{os.pathsep}{env['PATH']}" if stubs_ else env["PATH"]
    env.setdefault("TMKG_AWS_CONFIG", str(AWS / "config.example.env"))     # a real config.env never changes a test
    return subprocess.run([BASH, str(AWS / f"{script}.sh"), *args], capture_output=True, text=True, env=env, cwd=cwd)


def dry(script, stubs_, *args, env_extra=None):
    r = run(script, "--dry-run", *args, env_extra=env_extra, stubs_=stubs_)
    assert r.returncode == 0, (script, r.stdout[-800:], r.stderr[-800:])
    assert not stubs_[1].exists(), f"{script} --dry-run executed: {stubs_[1].read_text()}"
    return r.stdout + r.stderr          # `cap` prints the call it would make on stderr (its stdout is the captured value)


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_script_dry_runs_and_calls_nothing(script, stubs):
    out = dry(script, stubs)
    assert "DRY RUN" in out and ("+ aws " in out or "+ POST" in out)


def test_a_dry_run_works_with_no_config_file_at_all(stubs, tmp_path):
    r = run("budget_alarms", "--dry-run", env_extra={"TMKG_AWS_CONFIG": str(tmp_path / "missing.env")}, stubs_=stubs)
    # the example values are used when the named config is missing and --dry-run is set
    assert r.returncode == 0 and "uses the example values" in r.stdout and not stubs[1].exists()
    real = run("budget_alarms", env_extra={"TMKG_AWS_CONFIG": str(tmp_path / "missing.env")}, stubs_=stubs)
    assert real.returncode == 2 and "config.env is missing" in real.stderr and not stubs[1].exists()


@pytest.mark.parametrize("script", SCRIPTS + ["lib", "monitor_task"])
def test_scripts_parse_and_are_executable_or_sourced(script):
    f = AWS / f"{script}.sh"
    assert subprocess.run([BASH, "-n", str(f)], capture_output=True).returncode == 0
    if script != "lib":
        assert os.access(f, os.X_OK)


def test_shellcheck_is_clean_when_installed(tmp_path):
    sc = shutil.which("shellcheck")
    if sc is None:
        pytest.skip("shellcheck not installed (uv tool run --from shellcheck-py shellcheck)")
    files = [*map(str, sorted(AWS.glob("*.sh")))]
    for cwd in (ROOT, AWS, tmp_path):        # `-P SCRIPTDIR` makes the `source=lib.sh` hints resolve wherever shellcheck is started (SC2218 otherwise)
        r = subprocess.run([sc, "-x", "-P", "SCRIPTDIR", "-S", "style", *files], capture_output=True, text=True, cwd=cwd)
        assert r.returncode == 0, (str(cwd), r.stdout)


def test_billing_alarms_come_first_in_the_readme_and_cover_three_thresholds(stubs):
    readme = (AWS / "README.md").read_text()
    order = [readme.index(f"bash aws/{s}.sh") for s in ("budget_alarms", "iam_setup", "neptune_create", "deploy_app", "teardown")]
    assert order == sorted(order)
    out = dry("budget_alarms", stubs)
    assert out.count("put-metric-alarm") == 3
    for usd in ("25", "75", "150"):
        assert f"tmkg-billing-{usd}usd" in out
    assert "create-budget" in out and "--region us-east-1" in out            # billing metrics live in us-east-1 only


def test_alarm_thresholds_and_budget_are_configurable(stubs, tmp_path):
    cfg = tmp_path / "my.env"
    cfg.write_text('TMKG_BUCKET=b\nALERT_EMAIL=me@example.org\nALARM_USD="10 20"\nBUDGET_USD=40\n')
    out = dry("budget_alarms", stubs, env_extra={"TMKG_AWS_CONFIG": str(cfg)})
    assert out.count("put-metric-alarm") == 2 and '"Amount":"40"' in out


CREATORS = ("create-topic", "create-security-group", "create-db-subnet-group", "create-db-cluster", "create-db-instance",
            "create-vpc-endpoint", "create-distribution-with-tags", "create-policy", "create-role", "create-instance-profile",
            "create-schedule-group", "put-bucket-tagging", "add-tags-to-resource")


def test_every_resource_creating_call_carries_the_project_tag(stubs):
    seen = set()
    for script in ("budget_alarms", "iam_setup", "nim_key_ssm", "neptune_create", "deploy_app", "schedule_stop_start"):
        for line in dry(script, stubs).splitlines():
            for c in CREATORS:
                if line.startswith("+ aws ") and f" {c} " in line + " ":
                    assert "project" in line and "trialmindkg" in line, line
                    seen.add(c)
    run_instances = [l for l in dry("deploy_app", stubs).splitlines() if " run-instances " in l][0]
    assert "ResourceType=instance" in run_instances and "ResourceType=volume" in run_instances
    assert "HttpTokens=required" in run_instances and "Encrypted=true" in run_instances        # IMDSv2, encrypted disk
    assert seen == set(CREATORS)               # every kind of creating call was found and checked


def test_app_instance_is_reachable_only_through_cloudfront(stubs):
    out = dry("deploy_app", stubs)
    assert "origin-facing" in out and "PrefixListIds" in out and "0.0.0.0/0" not in out
    assert "create-distribution-with-tags" in out and "redirect-to-https" in out
    cfg = (AWS / "deploy_app.sh").read_text()
    assert '"OriginProtocolPolicy": "http-only"' in cfg and "ssh" not in out.lower().replace("session manager", "")


def test_neptune_is_serverless_private_and_deletion_protected(stubs):
    out = dry("neptune_create", stubs)
    assert "db.serverless" in out and "MinCapacity=1,MaxCapacity=4" in out and "--deletion-protection" in out
    assert "--storage-encrypted" in out and "--port 8182 --source-group" in out and "0.0.0.0/0" not in out
    assert "add-role-to-db-cluster" in out and "com.amazonaws.us-east-1.s3" in out


def test_schedules_stop_neptune_nightly_and_start_is_disabled(stubs):
    out = dry("schedule_stop_start", stubs)
    stop = [l for l in out.splitlines() if "tmkg-neptune-stop" in l][0]
    start = [l for l in out.splitlines() if "tmkg-neptune-start" in l][0]
    assert "ENABLED" in stop and "stopDBCluster" in stop and "DISABLED" in start and "tmkg-app-stop" not in out
    assert "tmkg-app-stop" in dry("schedule_stop_start", stubs, "--ec2")


def test_nim_key_never_reaches_a_command_line(stubs):
    secret = "nvapi-SECRET-VALUE-123"
    r = run("nim_key_ssm", "--dry-run", env_extra={"NVIDIA_API_KEY": secret}, stubs_=stubs)
    assert r.returncode == 0 and secret not in r.stdout + r.stderr
    assert "--value file://" in r.stdout and "SecureString" in r.stdout and "--overwrite" in r.stdout
    assert not re.search(r"--value (?!file://)", r.stdout)
    for f in AWS.iterdir():
        if f.is_file():
            assert not re.search(r"nvapi-[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{12,}", f.read_text()), f.name


def test_teardown_lists_by_tag_and_keeps_the_bucket_unless_asked(stubs):
    listing = run("teardown", "--dry-run", "--list", stubs_=stubs)
    text = listing.stdout + listing.stderr
    assert listing.returncode == 0 and "resourcegroupstaggingapi get-resources" in text
    assert "Key=project,Values=trialmindkg" in text and "delete" not in text
    out = dry("teardown", stubs)
    for part in ("terminate-instances", "delete-db-cluster", "delete-distribution", "delete-schedule-group", "delete-role",
                 "delete-parameter", "no-deletion-protection"):
        assert part in out, part
    assert "s3 rb" not in out and "kept" in out
    assert "s3 rb s3://" in dry("teardown", stubs, "--include-bucket")
    assert "--final-db-snapshot-identifier" in dry("teardown", stubs, "--snapshot")
    bad = run("teardown", "--dry-run", "--nonsense", stubs_=stubs)
    assert bad.returncode == 2


def test_policies_parse_are_least_privilege_and_templates_fill_in():
    allowed = set()
    for f in sorted((AWS / "iam").glob("*.json")):
        doc = json.loads(f.read_text())
        assert doc["Version"] == "2012-10-17", f.name
        for st in doc["Statement"]:
            acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            if st["Effect"] == "Allow":
                assert "*" not in acts and not any(a.endswith(":*") for a in acts), (f.name, st.get("Sid"))
                allowed.update(acts)
    dep = json.loads((AWS / "iam" / "deployer_policy.json").read_text())["Statement"]
    pass_roles = [s for s in dep if s.get("Action") == "iam:PassRole"]
    assert pass_roles                                   # one statement per project role (AWS Access Analyzer: no wildcard)
    for s in pass_roles:
        assert "*" not in s["Resource"] and s["Resource"].split(":role/")[1].startswith("tmkg-")
        assert s["Condition"]["StringEquals"]["iam:PassedToService"].endswith(".amazonaws.com")
    deny = [s for s in dep if s["Effect"] == "Deny"][0]
    assert {"iam:CreateUser", "iam:CreateAccessKey"} <= set(deny["Action"])
    destructive = [s for s in dep if s.get("Sid") == "Ec2OnlyWhatCarriesTheProjectTag"][0]
    assert destructive["Condition"]["StringEquals"]["aws:ResourceTag/project"] == "TAG_VALUE"
    assert {"s3:GetObject", "ssm:GetParameter", "rds:StopDBCluster"} <= allowed
    for f in (AWS / "iam").glob("*.json"):
        filled = f.read_text().replace("BUCKET_NAME", "b").replace("ACCOUNT_ID", "1").replace("REGION", "r").replace("TAG_VALUE", "t")
        json.loads(filled)
        assert not re.search(r"BUCKET_NAME|ACCOUNT_ID|REGION\b", filled.replace("RequestedRegion", ""))


def test_example_config_defines_every_required_variable():
    cfg = (AWS / "config.example.env").read_text()
    needed = set()
    for f in AWS.glob("*.sh"):
        needed |= set(re.findall(r"\$\{([A-Z_]+):\?", f.read_text()))
    assert needed >= {"TMKG_BUCKET", "ALERT_EMAIL", "NEPTUNE_ENDPOINT", "NEPTUNE_LOAD_ROLE_ARN"}
    for var in needed:
        assert re.search(rf"^{var}=", cfg, re.M), var
    assert "AKIA" not in cfg and "secret" not in cfg.lower().replace("never commit", "")


def test_dockerfile_and_user_data_hold_no_secret_and_read_the_key_from_ssm():
    docker = (AWS / "Dockerfile").read_text()
    assert docker.startswith("# ") and "FROM python:3.11-slim" in docker and "EXPOSE 8000" in docker
    assert not re.search(r"^(ENV|ARG).*(KEY|SECRET|TOKEN|PASSWORD)", docker, re.M | re.I)
    assert "COPY .env" not in docker and "data/kg" not in docker.replace("/data/data/kg", "")
    ignore = (AWS / "Dockerfile.dockerignore").read_text().split()
    assert {".env", "aws/config.env", "data/internal", ".git"} <= set(ignore)
    ud = (AWS / "user_data.sh.tmpl").read_text()
    assert "ssm get-parameter --name /trialmindkg/NVIDIA_API_KEY --with-decryption" in ud and "nvapi-" not in ud


# ---- neptune_load.sh against a stubbed endpoint ----
CURL_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("https://"))
if url.endswith("/loader"):
    print(json.dumps({"payload": {"loadId": "L1"}}))
elif "/loader/L1" in url:
    print(json.dumps({"payload": {"overallStatus": {"status": os.environ.get("STUB_STATUS", "LOAD_COMPLETED")}}}))
elif url.endswith("/openCypher"):
    q = " ".join(args)
    key = "nodes" if "count(n)" in q else "edges"
    print(json.dumps({"results": [{key: int(os.environ["STUB_" + key.upper()])}]}))
'''


@pytest.fixture
def neptune_stub(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "curl"
    f.write_text(CURL_STUB)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    counts = json.loads((ROOT / "outputs" / "run_manifest.json").read_text())["counts"]
    return bindir, counts


def load(neptune_stub, tmp_path, **env):
    bindir, counts = neptune_stub
    base = {"TMKG_S3_VERSION": "abc123", "STUB_NODES": str(counts["nodes"]), "STUB_EDGES": str(counts["edges"])}
    base.update(env)
    cfg = tmp_path / "stub.env"
    cfg.write_text("TMKG_BUCKET=b\nNEPTUNE_ENDPOINT=n.example\nNEPTUNE_LOAD_ROLE_ARN=arn:aws:iam::1:role/r\n")
    e = {**os.environ, **base, "TMKG_AWS_CONFIG": str(cfg)}
    e["PATH"] = f"{bindir}{os.pathsep}{e['PATH']}"
    return subprocess.run([BASH, str(AWS / "neptune_load.sh")], capture_output=True, text=True, env=e, cwd=ROOT)


def test_neptune_load_passes_when_counts_equal_the_manifest(neptune_stub, tmp_path):
    r = load(neptune_stub, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Counts match the manifest" in r.stdout and "status: LOAD_COMPLETED" in r.stdout


def test_neptune_load_fails_on_a_count_mismatch(neptune_stub, tmp_path):
    r = load(neptune_stub, tmp_path, STUB_NODES="1")
    assert r.returncode == 1 and "COUNT MISMATCH" in r.stderr
    assert load(neptune_stub, tmp_path, STUB_EDGES="0").returncode == 1


def test_neptune_load_fails_when_the_loader_fails(neptune_stub, tmp_path):
    r = load(neptune_stub, tmp_path, STUB_STATUS="LOAD_FAILED")
    assert r.returncode == 1 and "COUNT MISMATCH" not in r.stderr


# --- CC-26: deploy defaults = the measured server; iam_setup.sh --roles-only and re-runs -----------------------------

def config_without(tmp_path, *names):
    """The example config with the named variables removed (so the scripts' own defaults apply)."""
    keep = [l for l in (AWS / "config.example.env").read_text().splitlines() if l.split("=", 1)[0] not in names]
    p = tmp_path / "config.env"
    p.write_text("\n".join(keep) + "\n")
    return p


def test_deploy_defaults_are_t3_large_and_50_gb(stubs, tmp_path):
    """Owner decision 11 Nov 2026: 8 GB (t3.large), so that several broad questions at once do not exhaust the memory."""
    cfg = config_without(tmp_path, "APP_INSTANCE_TYPE", "APP_DISK_GB")
    env = {k: v for k, v in os.environ.items() if k not in ("APP_INSTANCE_TYPE", "APP_DISK_GB")}
    env["TMKG_AWS_CONFIG"] = str(cfg)
    env["PATH"] = f"{stubs[0]}{os.pathsep}{env['PATH']}"
    r = subprocess.run([BASH, str(AWS / "deploy_app.sh"), "--dry-run"], capture_output=True, text=True, env=env, cwd=ROOT)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and not stubs[1].exists(), out[-800:]
    assert "--instance-type t3.large" in out and "VolumeSize=50" in out
    example = (AWS / "config.example.env").read_text()
    assert "APP_INSTANCE_TYPE=t3.large" in example and "APP_DISK_GB=50" in example
    assert "t3.large, 50 GB disk" in (AWS / "README.md").read_text()


def test_iam_setup_roles_only_skips_the_policy_and_the_attach(stubs):
    out = dry("iam_setup", stubs, "--roles-only")
    assert "create-policy" not in out and "attach-user-policy" not in out and "--roles-only" in out
    assert out.count(" create-role ") == 4 and " create-instance-profile " in out        # PK14: + tmkg-idle-stop
    full = dry("iam_setup", stubs)
    assert "create-policy" in full and "attach-user-policy" in full
    bad = run("iam_setup", "--dry-run", "--rolesonly", stubs_=stubs)
    assert bad.returncode == 2 and "unknown argument" in bad.stderr


def aws_answering(tmp_path, errors):
    """A stub `aws` that logs every call and, for a call whose IAM action is in `errors`, prints that error and fails."""
    bindir, log = tmp_path / "awsbin", tmp_path / "aws_calls"
    bindir.mkdir()
    cases = "".join(f'    *" {action} "*) echo "An error occurred ({err}) when calling {action}" >&2; exit 254 ;;\n'
                    for action, err in errors.items())
    f = bindir / "aws"
    f.write_text(f'#!/bin/bash\necho "aws $*" >> "{log}"\ncase " $* " in\n{cases}esac\nexit 0\n')
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return bindir, log


def test_iam_setup_rerun_tolerates_existing_roles_and_profile(tmp_path):
    bindir, log = aws_answering(tmp_path, {"create-role": "EntityAlreadyExists", "create-instance-profile":
                                           "EntityAlreadyExists", "add-role-to-instance-profile": "LimitExceeded"})
    r = run("iam_setup", "--roles-only", stubs_=(bindir, log))
    assert r.returncode == 0, r.stderr[-800:]
    for name in ("role tmkg-app", "role tmkg-neptune-s3-load", "role tmkg-scheduler", "role tmkg-idle-stop", "instance profile tmkg-app"):
        assert f"exists: {name}" in r.stdout
    calls = log.read_text()
    assert "create-policy" not in calls and "attach-user-policy" not in calls
    assert calls.count("put-role-policy") == 4                           # inline policies are still (re)written


def test_iam_setup_stops_on_any_other_error(tmp_path):
    bindir, log = aws_answering(tmp_path, {"create-role": "AccessDenied"})
    r = run("iam_setup", "--roles-only", stubs_=(bindir, log))
    assert r.returncode != 0 and "AccessDenied" in r.stderr and "put-role-policy" not in log.read_text()


# ---- 7 Nov: card index as zstd parts (owner curl upload) and the opt-in skip of deploy step 1 ----


def _index_block(tmp_path):
    """The parts -> zstd -d block of user_data.sh.tmpl, with /srv/ moved under tmp_path."""
    ud = (AWS / "user_data.sh.tmpl").read_text()
    start = ud.index('if aws s3 cp "s3://BUCKET_NAME/app/index/parts.txt"')
    end = ud.index("\nfi\n", start) + 4
    return ud[start:end].replace("/srv/", f"{tmp_path}/srv/")


def _s3_stub(tmp_path, store):
    """An `aws` stub for `aws s3 cp s3://BUCKET_NAME/<key> <dest|-> --region R`, served from the folder `store`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "aws"
    f.write_text('#!/bin/sh\nkey="${3#s3://BUCKET_NAME/}"\n[ -f "' + str(store) + '/$key" ] || exit 1\n'
                 'if [ "$4" = "-" ]; then cat "' + str(store) + '/$key"; else cp "' + str(store) + '/$key" "$4"; fi\n')
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return bindir


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
@pytest.mark.parametrize("corrupt", [False, True])
def test_user_data_streams_the_zstd_index_parts_into_the_app_path(tmp_path, corrupt):
    data = os.urandom(200_000) + b"card index " * 50_000
    packed = subprocess.run(["zstd", "-3", "-c"], input=data, capture_output=True, check=True).stdout
    store = tmp_path / "s3"
    (store / "app" / "index").mkdir(parents=True)
    names, step = [], len(packed) // 3 + 1
    for i in range(3):
        chunk = packed[i * step:(i + 1) * step]
        if corrupt and i == 1:
            chunk = chunk[:100] + bytes([chunk[100] ^ 0xFF]) + chunk[101:]
        name = f"card_index_v2.sqlite.zst.part-a{'abc'[i]}"
        (store / "app" / "index" / name).write_bytes(chunk)
        names.append(name)
    (store / "app" / "index" / "parts.txt").write_text("\n".join(names) + "\n")
    (tmp_path / "srv" / "app").mkdir(parents=True)
    env = {**os.environ, "PATH": f"{_s3_stub(tmp_path, store)}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run([BASH, "-c", "set -euo pipefail\n" + _index_block(tmp_path)], capture_output=True, env=env)
    out = tmp_path / "srv" / "data" / "data" / "kg" / "pharos_index" / "card_index.sqlite"
    if corrupt:
        assert r.returncode != 0 and not out.exists()          # a bad part stops the boot; no half-written index is served
    else:
        assert r.returncode == 0, r.stderr[-800:]
        assert out.read_bytes() == data and not out.with_suffix(".sqlite.partial").exists()


def test_user_data_without_parts_manifest_skips_the_index_step(tmp_path):
    store = tmp_path / "s3"
    store.mkdir()
    (tmp_path / "srv" / "app").mkdir(parents=True)
    env = {**os.environ, "PATH": f"{_s3_stub(tmp_path, store)}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run([BASH, "-c", "set -euo pipefail\n" + _index_block(tmp_path)], capture_output=True, env=env)
    assert r.returncode == 0 and not (tmp_path / "srv" / "data").exists()
    assert "dnf install -y docker zstd" in (AWS / "user_data.sh.tmpl").read_text()


def test_deploy_skip_s3_step_goes_straight_to_the_network_and_instance(stubs):
    out = dry("deploy_app", stubs, env_extra={"TMKG_SKIP_S3_STEP": "1"})
    assert "step 1 skipped (TMKG_SKIP_S3_STEP=1)" in out
    assert "create-bucket" not in out and "s3 cp" not in out and "git archive" not in out
    assert " run-instances " in out and "create-distribution-with-tags" in out
    default = dry("deploy_app", stubs)
    assert "create-bucket" in default and "step 1 skipped" not in default      # without the switch nothing changes


def test_user_data_never_prints_the_nim_key_in_the_boot_trace(tmp_path):
    """7 Nov: first boot ran with set -x and printed the key on the docker run line. Run the key block under bash -x with a
    stub aws (SSM returns a fake key) and a stub docker: the key must not appear in the trace and must reach the env file."""
    fake = "nvapi-FAKEKEY0123456789"
    ud = (AWS / "user_data.sh.tmpl").read_text()
    start = ud.index("# The key never reaches")
    block = ud[start:].replace("/srv/", f"{tmp_path}/srv/").replace("REGION", "ap-southeast-2")
    (tmp_path / "srv" / "app").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("aws", f'#!/bin/sh\necho "{fake}"\n'), ("docker", '#!/bin/sh\necho "docker $*" >> "$0.log"\n')):
        f = bindir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run([BASH, "-c", "set -euxo pipefail\n" + block], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-800:]
    assert fake not in r.stdout + r.stderr                                    # not in the trace / boot log
    assert (tmp_path / "srv" / "app" / "nim.env").read_text() == f"NVIDIA_API_KEY={fake}\n"
    assert oct((tmp_path / "srv" / "app" / "nim.env").stat().st_mode & 0o777) == "0o600"
    docker_calls = (bindir / "docker.log").read_text()
    assert fake not in docker_calls and "--env-file" in docker_calls          # not on a command line either


# ---- 7 Nov: front door = CloudFront (default) or Caddy + Let's Encrypt on <ip>.sslip.io (TMKG_TLS=caddy) ----


def _front_door(tmp_path, mode, public_host="", downloads=False, acme_email="", aws_fails=False, ip_ranges=None,
                neptune_host="", wake_min=""):
    """Run the user-data front-door block (rendered with TLS_MODE=mode) with stub docker / curl / aws / systemctl.
    ip_ranges: the text the curl stub serves for ip-ranges.amazonaws.com (None = nothing, as an unreachable host would).
    Returns (result, calls): one line per stub call, in order ("docker ...", "aws ...", "systemctl ...")."""
    ud = (AWS / "user_data.sh.tmpl").read_text()
    block = ud[ud.index("# Front door"):].replace("TLS_MODE", mode).replace("PUBLIC_HOST", public_host)
    block = block.replace("NEPTUNE_WAKE_MINUTES", "@W@").replace("NEPTUNE_WAKE_MIN", wake_min).replace("@W@", "NEPTUNE_WAKE_MINUTES")
    block = block.replace("NEPTUNE_HOST", neptune_host)
    block = block.replace("ACME_EMAIL", acme_email).replace("BUCKET_NAME", "tmkg-b").replace("REGION", "ap-southeast-4")
    block = block.replace("/srv/", f"{tmp_path}/srv/").replace("/etc/systemd/system", f"{tmp_path}/units")
    (tmp_path / "units").mkdir()
    (tmp_path / "srv" / "app").mkdir(parents=True)
    if downloads:
        (tmp_path / "srv" / "downloads" / "kgx").mkdir(parents=True)
        (tmp_path / "srv" / "downloads" / "kgx" / "edges.tsv.gz").write_bytes(b"x")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    ranges = tmp_path / "ip-ranges.json"
    if ip_ranges is not None:
        ranges.write_text(ip_ranges)
    for name, body in (("docker", f'#!/bin/sh\necho "docker $*" >> "{log}"\n'),
                       ("aws", f'#!/bin/sh\necho "aws $*" >> "{log}"\nexit {1 if aws_fails else 0}\n'),
                       ("systemctl", f'#!/bin/sh\necho "systemctl $*" >> "{log}"\n'),
                       ("curl", '#!/bin/sh\ncase "$*" in *api/token*) echo TOKEN ;; *public-ipv4*) echo 13.239.39.104 ;;\n'
                                f'  *ip-ranges.amazonaws.com*) [ -f "{ranges}" ] || exit 7; cat "{ranges}" ;; esac\n')):
        f = bindir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run([BASH, "-c", "set -euo pipefail\n" + block], capture_output=True, text=True, env=env)
    return r, (log.read_text() if log.exists() else "")


def test_front_door_cloudfront_publishes_the_app_on_port_80_and_starts_no_caddy(tmp_path):
    r, calls = _front_door(tmp_path, "cloudfront")
    assert r.returncode == 0, r.stderr[-500:]
    assert "-p 80:8000" in calls and "caddy" not in calls


def test_front_door_caddy_without_a_fixed_address_uses_this_boots_public_ip(tmp_path):
    r, calls = _front_door(tmp_path, "caddy")
    assert r.returncode == 0, r.stderr[-500:]
    assert "-p 127.0.0.1:8000:8000" in calls and "-p 80:8000" not in calls       # the app is not reachable from outside
    assert "--network host" in calls and "caddy:2" in calls
    caddyfile = (tmp_path / "srv" / "app" / "Caddyfile").read_text()
    assert caddyfile.startswith("tmkg.13-239-39-104.sslip.io {") and "reverse_proxy 127.0.0.1:8000" in caddyfile   # 9 Nov: tmkg. prefix
    assert "HOSTED_URL https://tmkg.13-239-39-104.sslip.io/" in r.stdout
    assert (tmp_path / "srv" / "app" / "hosted_url.txt").read_text().strip() == "HOSTED_URL https://tmkg.13-239-39-104.sslip.io/"
    assert "TMKG_DOWNLOADS_BASE" not in calls                                     # no downloads on the disk -> none listed


def test_front_door_caddy_with_the_elastic_ip_serves_downloads_and_tells_the_app(tmp_path):
    r, calls = _front_door(tmp_path, "caddy", public_host="3-25-9-9.sslip.io", downloads=True)
    assert r.returncode == 0, r.stderr[-500:]
    caddyfile = (tmp_path / "srv" / "app" / "Caddyfile").read_text()
    assert caddyfile.startswith("3-25-9-9.sslip.io {")                           # the fixed name, not this boot's IP
    assert "handle /downloads/* {" in caddyfile and "file_server browse" in caddyfile and "root * /srv" in caddyfile
    assert "-e TMKG_DOWNLOADS_BASE=https://3-25-9-9.sslip.io/downloads/" in calls
    # (the test moves every /srv/ path under tmp_path, the container side too)
    assert f"-v {tmp_path}/srv/downloads:{tmp_path}/srv/downloads:ro" in calls and "/etc/caddy/Caddyfile:ro" in calls
    assert "-v /srv/downloads:/srv/downloads:ro" in (AWS / "user_data.sh.tmpl").read_text()
    assert "HOSTED_URL https://3-25-9-9.sslip.io/" in r.stdout


def test_deploy_caddy_mode_allocates_once_reuses_and_attaches_the_elastic_ip(stubs):
    out = dry("deploy_app", stubs, env_extra={"TMKG_SKIP_S3_STEP": "1", "TMKG_TLS": "caddy"})
    assert "ec2 describe-addresses" in out and "ec2 allocate-address --domain vpc" in out
    assert "ResourceType=elastic-ip,Tags=[{Key=project,Value=trialmindkg},{Key=Name,Value=tmkg-app}]" in out
    err = run("deploy_app", "--dry-run", env_extra={"TMKG_SKIP_S3_STEP": "1", "TMKG_TLS": "caddy"}, stubs_=stubs).stderr
    assert err.index(" allocate-address ") < err.index(" run-instances ")      # the name exists before the instance boots
    assert " associate-address --instance-id i-0example --allocation-id eipalloc-0example" in out
    assert "--allow-reassociation" in out and "downloads: https://tmkg.3-25-0-1.sslip.io/downloads/" in out
    assert "PUBLIC_HOST" in (AWS / "lib.sh").read_text() and 'HOST="PUBLIC_HOST"' in (AWS / "user_data.sh.tmpl").read_text()


def test_deploy_caddy_mode_opens_80_and_443_skips_cloudfront_and_prints_the_link(stubs):
    out = dry("deploy_app", stubs, env_extra={"TMKG_SKIP_S3_STEP": "1", "TMKG_TLS": "caddy"})
    assert "FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0" in out and "FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0" in out
    assert "origin-facing" not in out and "create-distribution" not in out
    assert " run-instances " in out and "https://tmkg.3-25-0-1.sslip.io/" in out
    default = dry("deploy_app", stubs)
    assert "0.0.0.0/0" not in default and "create-distribution-with-tags" in default and "sslip.io" not in default


def test_deploy_rejects_an_unknown_front_door(stubs):
    r = run("deploy_app", "--dry-run", env_extra={"TMKG_TLS": "nginx"}, stubs_=stubs)
    assert r.returncode == 2 and "TMKG_TLS must be cloudfront or caddy" in r.stderr


def test_dockerfile_copies_every_file_the_app_opens_at_request_time():
    """7 Nov: the image copied only outputs/kgx, so KGIndex (outputs/kg_nodes.csv) failed and /api/landing, /api/status and the
    Trials tab returned HTTP 500 on the hosted app. Every committed file the app reads must sit under a COPY source."""
    docker = (AWS / "Dockerfile").read_text()
    sources = [line.split()[1] for line in docker.splitlines() if line.startswith("COPY ")]
    needed = ["outputs/kg_nodes.csv", "outputs/kg_edges.csv", "outputs/run_manifest.json", "outputs/resolution_report.csv",
              "outputs/kgx/nodes.tsv", "pipelines/app.py", "personas", "docs/SCHEMA_CARD.md",
              "docs/AGENT_LOOP.md", "docs/BRING_YOUR_OWN_SOURCE.md", "docs/demo_query_TUB040.md", "docs/organizers/BRIEF.md",
              "downloads.json", "examples/byo_source/lab_screen.yaml", "examples/byo_source/screen.tsv.gz"]
    for path in needed:
        assert any(path == s or path.startswith(s.rstrip("/") + "/") for s in sources), f"{path} is not copied into the image"
        assert (ROOT / path).exists(), path
    ignore = (AWS / "Dockerfile.dockerignore").read_text().split()
    assert {"outputs/g0", "outputs/g1", "outputs/g2", "outputs/g3", "outputs/g4"} <= set(ignore)   # big graphs stay out


def test_teardown_releases_the_project_elastic_ip(stubs):
    out = dry("teardown", stubs, "--yes")
    assert "ec2 describe-addresses --filters Name=tag:project,Values=trialmindkg" in out
    assert "ec2 release-address --allocation-id eipalloc-0example" in out
    assert out.index("terminate-instances") < out.index("release-address")        # after the instance is gone


# ---- 9 Nov (CC-42): Caddy's certificate survives a relaunch (Let's Encrypt: 5 certificates per hostname per week) ----


def test_caddy_certificate_is_restored_from_s3_before_caddy_starts(tmp_path):
    r, calls = _front_door(tmp_path, "caddy", public_host="tmkg.3-25-9-9.sslip.io")
    assert r.returncode == 0, r.stderr[-500:]
    lines = calls.splitlines()
    restore = [i for i, c in enumerate(lines) if c.startswith("aws s3 sync s3://tmkg-b/app/caddy/ ")]
    start = [i for i, c in enumerate(lines) if c.startswith("docker run") and "caddy:2" in c]
    assert restore and start and restore[0] < start[0]
    assert f"aws s3 sync s3://tmkg-b/app/caddy/ {tmp_path}/srv/caddy/ --region ap-southeast-4" in calls
    assert f"-v {tmp_path}/srv/caddy:/data" in calls          # the restored folder is the one Caddy keeps its data in


def test_caddy_certificate_is_saved_to_s3_every_10_minutes_and_at_shutdown(tmp_path):
    r, calls = _front_door(tmp_path, "caddy", public_host="tmkg.3-25-9-9.sslip.io")
    assert r.returncode == 0, r.stderr[-500:]
    sync = (tmp_path / "srv" / "app" / "caddy_sync.sh").read_text()
    assert f"aws s3 sync {tmp_path}/srv/caddy/ \"s3://tmkg-b/app/caddy/\" --region ap-southeast-4" in sync
    assert '--exclude "caddy/locks/*"' in sync
    assert oct((tmp_path / "srv" / "app" / "caddy_sync.sh").stat().st_mode & 0o777) == "0o700"
    units = tmp_path / "units"
    svc = (units / "tmkg-caddy-sync.service").read_text()
    assert f"ExecStop={tmp_path}/srv/app/caddy_sync.sh" in svc and "RemainAfterExit=yes" in svc
    assert "After=network-online.target" in svc                 # stopped (and synced) before the network goes down
    timer = (units / "tmkg-caddy-sync-periodic.timer").read_text()
    assert "OnUnitActiveSec=10min" in timer
    assert f"ExecStart={tmp_path}/srv/app/caddy_sync.sh" in (units / "tmkg-caddy-sync-periodic.service").read_text()
    assert "systemctl enable --now tmkg-caddy-sync.service tmkg-caddy-sync-periodic.timer" in calls
    # the sync script itself runs: it reaches aws with the folder and the bucket prefix
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    assert subprocess.run([BASH, str(tmp_path / "srv" / "app" / "caddy_sync.sh")], env=env).returncode == 0
    assert (tmp_path / "docker.log").read_text().splitlines()[-1].startswith(f"aws s3 sync {tmp_path}/srv/caddy/ s3://tmkg-b/app/caddy/")


def test_a_failed_certificate_restore_does_not_stop_the_boot(tmp_path):
    r, calls = _front_door(tmp_path, "caddy", public_host="tmkg.3-25-9-9.sslip.io", aws_fails=True)
    assert r.returncode == 0, r.stderr[-500:]
    assert "no saved Caddy certificate restored" in r.stdout and "caddy:2" in calls


def test_cloudfront_front_door_neither_restores_nor_saves_a_certificate(tmp_path):
    r, calls = _front_door(tmp_path, "cloudfront")
    assert r.returncode == 0, r.stderr[-500:]
    assert "aws " not in calls and "systemctl" not in calls and not (tmp_path / "units" / "tmkg-caddy-sync.service").exists()


def test_acme_email_adds_a_global_block_only_when_set(tmp_path):
    (tmp_path / "a").mkdir()
    r, _ = _front_door(tmp_path / "a", "caddy", public_host="tmkg.3-25-9-9.sslip.io", acme_email="owner@example.org")
    assert r.returncode == 0, r.stderr[-500:]
    cf = (tmp_path / "a" / "srv" / "app" / "Caddyfile").read_text()
    assert cf.startswith("{\n  email owner@example.org\n}\n\ntmkg.3-25-9-9.sslip.io {")
    (tmp_path / "b").mkdir()
    r, _ = _front_door(tmp_path / "b", "caddy", public_host="tmkg.3-25-9-9.sslip.io")
    assert (tmp_path / "b" / "srv" / "app" / "Caddyfile").read_text().startswith("tmkg.3-25-9-9.sslip.io {")


def test_render_fills_the_acme_email_and_deploy_rejects_a_malformed_one(stubs):
    lib = AWS / "lib.sh"
    out = subprocess.run([BASH, "-c", f'source "{lib}" >/dev/null 2>&1 || true; TMKG_BUCKET=b ACCOUNT_ID=1 AWS_REGION=r '
                          f'TAG_VALUE=t TMKG_ACME_EMAIL=me@example.org render "{AWS / "user_data.sh.tmpl"}"'],
                         capture_output=True, text=True)
    assert 'ACME_MAIL="me@example.org"' in out.stdout, out.stderr[-500:]
    r = run("deploy_app", "--dry-run", env_extra={"TMKG_SKIP_S3_STEP": "1", "TMKG_TLS": "caddy",
                                                 "TMKG_ACME_EMAIL": "x/y; rm -rf"}, stubs_=stubs)
    assert r.returncode == 2 and "TMKG_ACME_EMAIL is not a plain e-mail address" in r.stderr
    ok = run("deploy_app", "--dry-run", env_extra={"TMKG_SKIP_S3_STEP": "1", "TMKG_TLS": "caddy",
                                                  "TMKG_ACME_EMAIL": "owner@example.org"}, stubs_=stubs)
    assert ok.returncode == 0, ok.stderr[-500:]


def test_app_role_may_write_only_the_caddy_prefix():
    st = json.loads((AWS / "iam" / "app_instance_policy.json").read_text())["Statement"]
    writes = [s for s in st if any(a in ("s3:PutObject", "s3:DeleteObject") for a in s["Action"])]
    assert len(writes) == 1 and writes[0]["Action"] == ["s3:PutObject"]
    assert writes[0]["Resource"] == "arn:aws:s3:::BUCKET_NAME/app/caddy/*"


# ---- CC-44 (9 Nov): --roles-only never recreates a deleted role; --with-neptune forces tmkg-neptune-s3-load -----------

def aws_with_absent(tmp_path, absent, extra=""):
    """A stub `aws` that logs every call; `get-role` / `list-role-policies` for a role in `absent` answer NoSuchEntity."""
    bindir, log = tmp_path / "awsbin", tmp_path / "aws_calls"
    bindir.mkdir()
    cases = "".join(f'    *"get-role --role-name {r} "*|*"list-role-policies --role-name {r} "*) '
                    f'echo "An error occurred (NoSuchEntity) when calling the GetRole operation: The role with name {r} cannot be found." >&2; exit 254 ;;\n'
                    for r in absent)
    f = bindir / "aws"
    f.write_text(f'#!/bin/bash\necho "aws $*" >> "{log}"\ncase " $* " in\n{cases}{extra}esac\nexit 0\n')
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return bindir, log


def test_roles_only_skips_a_deleted_role_and_its_policy(tmp_path):
    bindir, log = aws_with_absent(tmp_path, ["tmkg-neptune-s3-load"])
    r = run("iam_setup", "--roles-only", stubs_=(bindir, log))
    assert r.returncode == 0, r.stderr[-800:]
    assert "absent: tmkg-neptune-s3-load, not created (run without --roles-only or pass --with-neptune)" in r.stdout
    calls = log.read_text()
    assert "create-role --role-name tmkg-neptune-s3-load" not in calls
    assert "put-role-policy --role-name tmkg-neptune-s3-load" not in calls
    for role in ("tmkg-app", "tmkg-scheduler"):                     # present roles are still updated
        assert f"get-role --role-name {role}" in calls and f"put-role-policy --role-name {role}" in calls
    assert "add-role-to-instance-profile" in calls


def test_roles_only_with_neptune_creates_it(tmp_path):
    bindir, log = aws_with_absent(tmp_path, ["tmkg-neptune-s3-load"])
    r = run("iam_setup", "--roles-only", "--with-neptune", stubs_=(bindir, log))
    assert r.returncode == 0, r.stderr[-800:]
    calls = log.read_text()
    assert "create-role --role-name tmkg-neptune-s3-load" in calls and "put-role-policy --role-name tmkg-neptune-s3-load" in calls
    assert "absent:" not in r.stdout


def test_roles_only_absent_app_role_leaves_the_instance_profile_alone(tmp_path):
    bindir, log = aws_with_absent(tmp_path, ["tmkg-app"])
    r = run("iam_setup", "--roles-only", stubs_=(bindir, log))
    calls = log.read_text()
    assert r.returncode == 0 and "absent: tmkg-app, not created" in r.stdout
    assert "create-instance-profile" not in calls and "add-role-to-instance-profile" not in calls


def test_roles_only_stops_when_get_role_fails_otherwise(tmp_path):
    bindir, log = aws_answering(tmp_path, {"get-role": "AccessDenied"})
    r = run("iam_setup", "--roles-only", stubs_=(bindir, log))
    assert r.returncode != 0 and "AccessDenied" in r.stderr and "create-role" not in log.read_text()


def test_roles_only_dry_run_prints_lookups_and_the_skip_rule(stubs):
    out = dry("iam_setup", stubs, "--roles-only")
    for role in ("tmkg-app", "tmkg-neptune-s3-load", "tmkg-scheduler", "tmkg-idle-stop"):
        assert f"aws iam get-role --role-name {role}" in out
        assert f"if it is absent it is skipped (absent: {role}, not created)" in out
    out_n = dry("iam_setup", stubs, "--roles-only", "--with-neptune")
    assert "aws iam get-role --role-name tmkg-neptune-s3-load" not in out_n and " create-role --role-name tmkg-neptune-s3-load" in out_n
    full = dry("iam_setup", stubs)                                   # a full run (first setup) does not look anything up
    assert "get-role" not in full and full.count(" create-role ") == 4
    bad = run("iam_setup", "--dry-run", "--with-neptune", stubs_=stubs)
    assert bad.returncode == 2 and "only makes sense with --roles-only" in bad.stderr


def test_iam_setup_header_and_readme_describe_the_rule():
    head = (AWS / "iam_setup.sh").read_text().split("set -euo pipefail")[0]
    assert "--with-neptune" in head and "absent: NAME, not created" in head and "--verify" in head
    readme = (AWS / "README.md").read_text()
    assert "--with-neptune" in readme and "absent:" in readme and "iam_setup.sh --verify" in readme


# ---- CC-45 (9 Nov): the deployer may read back the role policies it writes; --verify lists them ----------------------

def test_deployer_may_read_project_role_policies():
    st = [s for s in json.loads((AWS / "iam" / "deployer_policy.json").read_text())["Statement"] if s.get("Sid") == "OnlyTheProjectRoles"][0]
    assert {"iam:GetRolePolicy", "iam:ListRolePolicies", "iam:PutRolePolicy", "iam:GetRole"} <= set(st["Action"])
    assert all(r.split(":", 5)[5].split("/")[1].startswith("tmkg-") for r in st["Resource"])
    readme = (AWS / "README.md").read_text()
    assert "IAM → Policies → tmkg-deployer-policy → Edit → JSON → Save as default version" in readme
    assert "admin" in readme.split("tmkg-deployer-policy → Edit")[0][-600:]


def test_verify_is_read_only_and_prints_sids_and_s3_resources(tmp_path):
    doc = json.dumps({"RoleName": "tmkg-app", "PolicyName": "tmkg-app-inline", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
        {"Sid": "ReadTheAppBundle", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::b/app/*"},
        {"Sid": "ReadTheNimKey", "Effect": "Allow", "Action": "ssm:GetParameter", "Resource": "arn:aws:ssm:r:1:parameter/trialmindkg/nim"}]}})
    extra = (f"""    *"list-role-policies --role-name tmkg-app "*) echo '{{"PolicyNames": ["tmkg-app-inline"]}}' ;;\n"""
             f"""    *"list-role-policies --role-name tmkg-scheduler "*) echo '{{"PolicyNames": []}}' ;;\n"""
             f"""    *"get-role-policy --role-name tmkg-app "*) echo '{doc}' ;;\n""")
    bindir, log = aws_with_absent(tmp_path, ["tmkg-neptune-s3-load", "tmkg-idle-stop"], extra)
    r = run("iam_setup", "--verify", stubs_=(bindir, log))
    assert r.returncode == 0, r.stderr[-800:]
    out = r.stdout
    assert "role tmkg-app / policy tmkg-app-inline: 2 statements" in out
    assert "Sid ReadTheAppBundle  Resource arn:aws:s3:::b/app/*" in out
    assert "Sid ReadTheNimKey" in out and "parameter/trialmindkg" not in out          # Resource printed for S3 statements only
    assert "absent: tmkg-neptune-s3-load" in out and "role tmkg-scheduler: no inline policy" in out
    calls = log.read_text()
    assert not re.search(r"\b(create|put|delete|attach|add|tag)-", calls), calls    # nothing but reads


def test_verify_dry_run_calls_nothing(stubs):
    out = dry("iam_setup", stubs, "--verify")
    assert out.count("aws iam list-role-policies") == 4 and "create-role" not in out and "put-role-policy" not in out
    bad = run("iam_setup", "--dry-run", "--verify", "--roles-only", stubs_=stubs)
    assert bad.returncode == 2


def test_teardown_deletes_the_billing_alarms_named_by_alarm_usd(stubs, tmp_path):
    default = dry("teardown", stubs, "--yes")
    assert "tmkg-billing-25usd tmkg-billing-75usd tmkg-billing-150usd" in default
    cfg = tmp_path / "my.env"
    cfg.write_text('TMKG_BUCKET=b\nALERT_EMAIL=me@example.org\nALARM_USD="50 100 140"\n')
    out = dry("teardown", stubs, "--yes", env_extra={"TMKG_AWS_CONFIG": str(cfg)})
    assert "tmkg-billing-50usd tmkg-billing-100usd tmkg-billing-140usd" in out and "tmkg-billing-25usd" not in out


def test_budget_alarms_make_only_the_budget_when_sns_is_denied(tmp_path):
    """A stub aws that refuses create-topic (the organization policy) and records every other call."""
    bindir, log = tmp_path / "bin", tmp_path / "calls"
    bindir.mkdir()
    aws = bindir / "aws"
    aws.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\ncase "$*" in *create-topic*) echo "AccessDenied: explicit deny in a service control policy" >&2; exit 254;; esac\nexit 0\n')
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    cfg = tmp_path / "my.env"
    cfg.write_text("TMKG_BUCKET=b\nALERT_EMAIL=me@example.org\n")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "TMKG_AWS_CONFIG": str(cfg)}
    r = subprocess.run([BASH, str(AWS / "budget_alarms.sh")], capture_output=True, text=True, env=env, cwd=ROOT)
    calls = log.read_text()
    assert r.returncode == 0, r.stderr
    assert "create-budget" in calls and "put-metric-alarm" not in calls and "sns subscribe" not in calls
    assert "SKIPPED" in r.stderr and "alarms at" not in r.stdout and "monthly budget" in r.stdout


# =========================== PK14 (14 Nov): CloudFront front, Neptune state and wake, idle stop, IAM ===========================

def _fill(text):
    return text.replace("BUCKET_NAME", "b").replace("ACCOUNT_ID", "111122223333").replace("REGION", "ap-southeast-2").replace("TAG_VALUE", "trialmindkg")


def _actions(doc):
    out = []
    for st in doc["Statement"]:
        acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        out += acts
    return out


AWS_STUB = r'''#!/usr/bin/env python3
"""Stub aws: logs every call; the first rule whose "match" strings all occur in the arguments gives stdout / stderr / rc."""
import json, os, sys
line = " ".join(sys.argv[1:])
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("aws " + line + "\n")
for r in json.load(open(os.environ["STUB_RULES"])):
    if all(p in line for p in r["match"]):
        sys.stdout.write(r.get("out", ""))
        sys.stderr.write(r.get("err", ""))
        sys.exit(r.get("rc", 0))
'''


def aws_rules(tmp_path, rules, name="rules"):
    """A bin folder with the rule-driven aws stub. Returns (bindir, log, env_extra)."""
    bindir, log, rf = tmp_path / f"{name}_bin", tmp_path / f"{name}_calls", tmp_path / f"{name}.json"
    bindir.mkdir()
    rf.write_text(json.dumps(rules))
    f = bindir / "aws"
    f.write_text(AWS_STUB)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return bindir, log, {"STUB_LOG": str(log), "STUB_RULES": str(rf)}


def real(script, tmp_path, rules, *args, config=None):
    """Run a script for real against the rule stub. Returns (result, calls text)."""
    bindir, log, extra = aws_rules(tmp_path, rules)
    if config is not None:
        cfg = tmp_path / "cfg.env"
        cfg.write_text(config)
        extra["TMKG_AWS_CONFIG"] = str(cfg)
    r = run(script, *args, env_extra=extra, stubs_=(bindir, log))
    return r, (log.read_text() if log.exists() else "")


# ---- A5: IAM files and iam_setup.sh ----

def test_deployer_policy_2_is_the_attached_text_small_and_disjoint_from_policy_1():
    raw = (AWS / "iam" / "deployer_policy_2.json").read_text()
    doc = json.loads(_fill(raw))
    assert len(re.sub(r"\s", "", _fill(raw))) < 6144                  # IAM's managed-policy limit (whitespace not counted)
    assert [s["Sid"] for s in doc["Statement"]] == [
        "RunCommandDocumentForTheLoader", "RunCommandOnlyOnTheProjectInstance", "ReadCommandResultsAndParameterMetadata",
        "CloudFrontReadOnlyLookups", "NeptuneReadOnlyLookups", "ReadMetricsForTheIdleAlarm", "ReadIdleStopLambdaPolicy",
        "IdleStopLambdaLogs", "ReadOwnDeployerPolicies"]
    one = set(_actions(json.loads((AWS / "iam" / "deployer_policy.json").read_text())))
    two = _actions(doc)
    assert not (set(two) & one), set(two) & one
    run_on = [s for s in doc["Statement"] if s["Sid"] == "RunCommandOnlyOnTheProjectInstance"][0]
    assert run_on["Condition"] == {"StringEquals": {"ssm:resourceTag/project": "trialmindkg"}}
    assert all(s["Effect"] == "Allow" for s in doc["Statement"])
    assert len(re.sub(r"\s", "", (AWS / "iam" / "deployer_policy.json").read_text())) < 6144


def test_idle_stop_role_has_exactly_its_three_grants_and_a_lambda_trust():
    trust = json.loads(_fill((AWS / "iam" / "trust_lambda.json").read_text()))
    assert trust["Statement"][0]["Principal"] == {"Service": "lambda.amazonaws.com"}
    doc = json.loads(_fill((AWS / "iam" / "idle_stop_policy.json").read_text()))
    grants = {(tuple(sorted(s["Action"])), json.dumps(s["Resource"])) for s in doc["Statement"]}
    assert grants == {
        (("rds:DescribeDBClusters", "rds:StopDBCluster"), '"arn:aws:rds:ap-southeast-2:111122223333:cluster:tmkg-*"'),
        (("cloudwatch:GetMetricStatistics",), '"*"'),
        (("logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"),
         json.dumps(["arn:aws:logs:ap-southeast-2:111122223333:log-group:/aws/lambda/tmkg-idle-stop",
                     "arn:aws:logs:ap-southeast-2:111122223333:log-group:/aws/lambda/tmkg-idle-stop:*"]))}


def test_app_role_may_wake_but_never_stop_neptune_and_scheduler_may_invoke_only_the_idle_stop():
    app = json.loads(_fill((AWS / "iam" / "app_instance_policy.json").read_text()))["Statement"]
    wake = [s for s in app if s["Sid"] == "WakeTheProjectNeptune"][0]
    assert wake["Action"] == ["rds:DescribeDBClusters", "rds:StartDBCluster"]
    assert wake["Resource"] == "arn:aws:rds:ap-southeast-2:111122223333:cluster:tmkg-*"
    assert "rds:StopDBCluster" not in _actions({"Statement": app}) and not any(a.startswith("neptune-db:") for a in _actions({"Statement": app}))
    sched = json.loads(_fill((AWS / "iam" / "scheduler_policy.json").read_text()))["Statement"]
    inv = [s for s in sched if s["Sid"] == "InvokeTheIdleStopSweep"][0]
    assert inv["Action"] == ["lambda:InvokeFunction"]
    assert inv["Resource"] == "arn:aws:lambda:ap-southeast-2:111122223333:function:tmkg-idle-stop"


def test_iam_setup_full_run_creates_and_attaches_both_deployer_policies(stubs):
    full = dry("iam_setup", stubs)
    for name in ("tmkg-deployer-policy", "tmkg-deployer-policy-2"):
        assert f"create-policy --policy-name {name} " in full
        assert f"attach-user-policy --user-name tmkg-deployer --policy-arn arn:aws:iam::123456789012:policy/{name}" in full
    assert " create-role --role-name tmkg-idle-stop " in full
    roles_only = dry("iam_setup", stubs, "--roles-only")
    assert "create-policy" not in roles_only and "attach-user-policy" not in roles_only
    assert "tmkg-deployer-policy and tmkg-deployer-policy-2 are neither created nor attached" in roles_only


def test_with_neptune_creates_both_neptune_roles_and_without_it_both_stay_absent(tmp_path):
    (tmp_path / "a").mkdir()
    bindir, log = aws_with_absent(tmp_path / "a", ["tmkg-neptune-s3-load", "tmkg-idle-stop"])
    r = run("iam_setup", "--roles-only", stubs_=(bindir, log))
    calls = log.read_text()
    assert r.returncode == 0, r.stderr[-500:]
    for role in ("tmkg-neptune-s3-load", "tmkg-idle-stop"):
        assert f"absent: {role}, not created" in r.stdout and f"create-role --role-name {role}" not in calls
    (tmp_path / "b").mkdir()
    bindir, log = aws_with_absent(tmp_path / "b", ["tmkg-neptune-s3-load", "tmkg-idle-stop"])
    r = run("iam_setup", "--roles-only", "--with-neptune", stubs_=(bindir, log))
    calls = log.read_text()
    assert r.returncode == 0 and "absent:" not in r.stdout
    for role in ("tmkg-neptune-s3-load", "tmkg-idle-stop"):
        assert f"create-role --role-name {role}" in calls and f"put-role-policy --role-name {role}" in calls


def test_verify_lists_the_idle_stop_role(stubs):
    out = dry("iam_setup", stubs, "--verify")
    assert "aws iam list-role-policies --role-name tmkg-idle-stop" in out


# ---- A2: the host reads the Neptune state, the container gets three values and one folder ----

ENDPOINT = "tmkg-neptune.cluster-c1a2b3c4d5e6.ap-southeast-2.neptune.amazonaws.com"


def render_user_data(endpoint="", wake="", tls="caddy"):
    """render() of aws/lib.sh on user_data.sh.tmpl with the given NEPTUNE_ENDPOINT / NEPTUNE_WAKE_MINUTES. Returns (stdout, stderr)."""
    script = (f'source "{AWS / "lib.sh"}"; TMKG_BUCKET=tmkg-b ACCOUNT_ID=1 AWS_REGION=ap-southeast-2 TMKG_TLS={tls} '
              f'TMKG_PUBLIC_HOST=tmkg.3-25-9-9.sslip.io NEPTUNE_ENDPOINT="{endpoint}" NEPTUNE_WAKE_MINUTES="{wake}" '
              f'render "{AWS / "user_data.sh.tmpl"}"')
    r = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout, r.stderr


def _python_render_without_a2(tmpl):
    """What render() must print with no endpoint: the template without the A2 block bodies (first comment line kept)."""
    out, skip = [], False
    for line in tmpl.splitlines(keepends=True):
        if skip:
            if line.lstrip().startswith("# <<< PK14 A2"):
                skip = False
            continue
        if line.lstrip().startswith("# >>> PK14 A2"):
            out.append(line)
            skip = True
            continue
        out.append(line)
    text = "".join(out)
    for k, v in (("BUCKET_NAME", "tmkg-b"), ("ACCOUNT_ID", "1"), ("REGION", "ap-southeast-2"), ("TAG_VALUE", "trialmindkg"),
                 ("TLS_MODE", "caddy"), ("PUBLIC_HOST", "tmkg.3-25-9-9.sslip.io"), ("ACME_EMAIL", "")):
        text = text.replace(k, v)
    return text


def test_user_data_without_neptune_is_the_old_script_but_for_two_comment_lines():
    tmpl = (AWS / "user_data.sh.tmpl").read_text()
    assert tmpl.count("# >>> PK14 A2") == 2 and tmpl.count("# <<< PK14 A2") == 2
    out, err = render_user_data("")
    assert out == _python_render_without_a2(tmpl) and err == ""
    code = [l for l in out.splitlines() if not l.lstrip().startswith("#")]
    assert not [l for l in code if "neptune" in l.lower()]                     # no Neptune code at all, only the two markers
    assert len([l for l in out.splitlines() if l.startswith("# >>> PK14 A2")]) == 2
    for absent in ("tmkg-neptune.timer", "neptune_ctl.sh", "NEPTUNE_ENDPOINT", "NEPTUNE_STATUS_DIR", "/srv/neptune"):
        assert absent not in out


def _strip_pk14(tmpl):
    """The template with every PK14 block ('# >>> PK14' to '# <<< PK14', markers included) removed."""
    out, skip = [], False
    for line in tmpl.splitlines():
        if skip:
            skip = not line.lstrip().startswith("# <<< PK14")
            continue
        if line.lstrip().startswith("# >>> PK14"):
            skip = True
            continue
        out.append(line)
    return out


def test_user_data_changed_only_inside_the_marked_blocks_and_the_caddyfile_writing():
    """The PK14 commit (0ac26f6a) against its parent (ac8e9eed): outside the marked blocks, only the lines that write the
    Caddyfile's global block differ (A1b). Pinned to those two commits because later packets (FIX1) change the template
    for their own reasons. Skipped without git history."""
    import difflib
    old, new = (subprocess.run(["git", "show", f"{c}:aws/user_data.sh.tmpl"], capture_output=True, text=True, cwd=ROOT)
                for c in ("ac8e9eed", "0ac26f6a"))
    if old.returncode != 0 or new.returncode != 0:
        pytest.skip("git history not available")
    a, b = old.stdout.splitlines(), _strip_pk14(new.stdout)
    lo, hi = a.index('  ACME_MAIL="ACME_EMAIL"'), a.index("  cat >> /srv/app/Caddyfile <<CADDY")
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag != "equal":
            assert lo < i1 <= hi and i2 <= hi, (tag, a[i1:i2], b[j1:j2])


def test_user_data_with_neptune_has_the_timer_the_folder_and_the_three_values(tmp_path):
    out, err = render_user_data(ENDPOINT, wake="8")
    assert err == ""
    assert "cat > /etc/systemd/system/tmkg-neptune.service" in out and "cat > /etc/systemd/system/tmkg-neptune.timer" in out
    assert "OnUnitActiveSec=20s" in out and "AccuracySec=1s" in out and "systemctl enable --now tmkg-neptune.timer" in out
    assert "mkdir -p /srv/neptune\nchown 10001:10001 /srv/neptune\nchmod 775 /srv/neptune" in out
    assert f'NHOST="{ENDPOINT}"' in out and 'NWAKE="8"' in out
    assert '-e "NEPTUNE_ENDPOINT=${NHOST}" -e NEPTUNE_STATUS_DIR=/neptune -v /srv/neptune:/neptune' in out
    assert '-e "NEPTUNE_WAKE_MINUTES=${NWAKE}"' in out                    # the name survives the NEPTUNE_WAKE_MIN placeholder
    assert "-e TMKG_GRAPH_BACKEND" not in out and "TMKG_GRAPH_BACKEND=" not in out     # the local index stays the default
    assert "--region ap-southeast-2" in out and "REGION" not in out.replace("RequestedRegion", "")
    assert "openCypher" not in out and "8182" not in out                     # the host never queries Neptune
    assert len(out.encode()) < 16384                                          # EC2 user-data limit
    f = tmp_path / "ud.sh"
    f.write_text(out)
    assert subprocess.run([BASH, "-n", str(f)]).returncode == 0
    sc = shutil.which("shellcheck")
    if sc:
        r = subprocess.run([sc, "-S", "warning", "-s", "bash", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout


def test_only_the_project_cluster_endpoint_reaches_the_app():
    for bad in ("my-cluster.cluster-xxxx.us-east-1.neptune.amazonaws.com",                 # the example value
                "tmkg-neptune.cluster-ro-abc.ap-southeast-2.neptune.amazonaws.com",          # the reader endpoint
                "tmkg-neptune.cluster-abc.ap-southeast-2.neptune.amazonaws.com/x;rm", "evil.example.org"):
        out, err = render_user_data(bad, wake="8")
        assert "NEPTUNE_ENDPOINT" not in out and "not the endpoint of the cluster tmkg-neptune" in err, bad
    out, _ = render_user_data(ENDPOINT, wake="ten")
    assert 'NWAKE=""' in out                                                  # not a number of minutes -> left out
    out, _ = render_user_data("", wake="8")
    assert "NWAKE" not in out


def test_render_of_policies_is_unchanged_by_the_neptune_placeholders():
    for f in (AWS / "iam").glob("*.json"):
        r = subprocess.run([BASH, "-c", f'source "{AWS / "lib.sh"}"; TMKG_BUCKET=b ACCOUNT_ID=1 AWS_REGION=r NEPTUNE_ENDPOINT=x '
                            f'render "{f}"'], capture_output=True, text=True)
        assert r.returncode == 0 and r.stderr == "" and json.loads(r.stdout)


def test_deploy_passes_the_endpoint_to_render(stubs, tmp_path):
    cfg = tmp_path / "c.env"
    cfg.write_text((AWS / "config.example.env").read_text() + f"\nNEPTUNE_ENDPOINT={ENDPOINT}\nNEPTUNE_WAKE_MINUTES=7\n")
    out = dry("deploy_app", stubs, env_extra={"TMKG_AWS_CONFIG": str(cfg), "TMKG_SKIP_S3_STEP": "1", "TMKG_TLS": "caddy"})
    assert "not the endpoint" not in out
    src = (AWS / "deploy_app.sh").read_text()
    exp = src.index('export NEPTUNE_ENDPOINT="${NEPTUNE_ENDPOINT:-}" NEPTUNE_WAKE_MINUTES="${NEPTUNE_WAKE_MINUTES:-}"')
    assert exp < src.index('USER_DATA="$(render_tmp "$AWS_DIR/user_data.sh.tmpl")"')
    ex = dry("deploy_app", stubs, env_extra={"TMKG_SKIP_S3_STEP": "1", "TMKG_TLS": "caddy"})   # example config: Neptune off
    assert "launched without Amazon Neptune" in ex


def _neptune_ctl(tmp_path, describe="stopped", describe_rc=0, start_rc=0, wake=False):
    """Extract neptune_ctl.sh from the rendered user data, point it at tmp_path, run it with a stub aws."""
    out, _ = render_user_data(ENDPOINT)
    body = out.split("cat > /srv/app/neptune_ctl.sh <<'NEPTUNE'\n", 1)[1].split("\nNEPTUNE\n", 1)[0]
    d = tmp_path / "neptune"
    d.mkdir()
    logf = tmp_path / "tmkg-neptune.log"
    body = body.replace("/srv/neptune", str(d)).replace("/var/log/tmkg-neptune.log", str(logf))
    script = tmp_path / "neptune_ctl.sh"
    script.write_text(body + "\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "aws_calls"
    aws = bindir / "aws"
    aws.write_text(f'#!/bin/sh\necho "aws $*" >> "{calls}"\ncase "$*" in\n'
                   f'  *describe-db-clusters*) echo "{describe}"; exit {describe_rc} ;;\n'
                   f'  *start-db-cluster*) exit {start_rc} ;;\nesac\n')
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    if wake:
        (d / "wake.request").write_text("")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True, env=env)
    status = json.loads((d / "status.json").read_text()) if (d / "status.json").exists() else None
    return r, status, (calls.read_text() if calls.exists() else ""), (logf.read_text() if logf.exists() else ""), d


def test_neptune_ctl_writes_the_state_atomically_and_never_queries(tmp_path):
    r, status, calls, log, d = _neptune_ctl(tmp_path, describe="available")
    assert r.returncode == 0, r.stderr
    assert status["state"] == "available" and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", status["checked_at"])
    assert "describe-db-clusters --db-cluster-identifier tmkg-neptune --region ap-southeast-2" in calls
    assert "start-db-cluster" not in calls and log == ""
    assert [p.name for p in d.iterdir()] == ["status.json"]                   # no temp file left behind
    assert oct((d / "status.json").stat().st_mode & 0o777) == "0o644"


def test_neptune_ctl_wakes_a_stopped_cluster_once_and_removes_the_request(tmp_path):
    r, status, calls, log, d = _neptune_ctl(tmp_path, describe="stopped", wake=True)
    assert r.returncode == 0 and status["state"] == "stopped"
    assert calls.count("start-db-cluster --db-cluster-identifier tmkg-neptune --region ap-southeast-2") == 1
    assert not (d / "wake.request").exists() and log.count("\n") == 1 and "start-db-cluster sent" in log


def test_neptune_ctl_ignores_a_wake_request_unless_stopped(tmp_path):
    for i, state in enumerate(("available", "starting", "stopping")):
        sub = tmp_path / str(i)
        sub.mkdir()
        r, status, calls, log, d = _neptune_ctl(sub, describe=state, wake=True)
        assert r.returncode == 0 and status["state"] == state
        assert "start-db-cluster" not in calls and not (d / "wake.request").exists()
        assert f"no start needed (state {state})" in log


def test_neptune_ctl_survives_failing_aws_calls(tmp_path):
    (tmp_path / "a").mkdir()
    r, status, calls, log, d = _neptune_ctl(tmp_path / "a", describe="", describe_rc=255, wake=True)
    assert r.returncode == 0 and status["state"] == "unknown"                 # the timer keeps running
    assert "start-db-cluster" not in calls and not (d / "wake.request").exists()
    (tmp_path / "b").mkdir()
    r, status, calls, log, d = _neptune_ctl(tmp_path / "b", describe="stopped", start_rc=254, wake=True)
    assert r.returncode == 0 and "start-db-cluster FAILED" in log
    (tmp_path / "c").mkdir()
    r, status, _, _, _ = _neptune_ctl(tmp_path / "c", describe="None")         # the CLI's answer for no cluster
    assert status["state"] == "unknown"
    (tmp_path / "e").mkdir()
    r, status, _, _, _ = _neptune_ctl(tmp_path / "e", describe='available", "x": "y')
    assert status == {"state": "unknown", "checked_at": status["checked_at"]}   # nothing odd gets into the JSON


def test_the_app_container_gets_the_values_only_with_an_endpoint(tmp_path):
    (tmp_path / "on").mkdir()
    r, calls = _front_door(tmp_path / "on", "caddy", public_host="tmkg.3-25-9-9.sslip.io", neptune_host=ENDPOINT, wake_min="8")
    assert r.returncode == 0, r.stderr[-500:]
    run_line = [c for c in calls.splitlines() if c.startswith("docker run") and "tmkg-app" in c][0]
    assert f"-e NEPTUNE_ENDPOINT={ENDPOINT}" in run_line and "-e NEPTUNE_STATUS_DIR=/neptune" in run_line
    assert "-e NEPTUNE_WAKE_MINUTES=8" in run_line and f"-v {tmp_path / 'on'}/srv/neptune:/neptune" in run_line
    assert "TMKG_GRAPH_BACKEND" not in run_line and ":/neptune:ro" not in run_line   # the app writes wake.request
    (tmp_path / "off").mkdir()
    r, calls = _front_door(tmp_path / "off", "caddy", public_host="tmkg.3-25-9-9.sslip.io")
    assert r.returncode == 0 and "NEPTUNE" not in calls and "/neptune" not in calls


# ---- A1b: Caddy trusts X-Forwarded-For only from CloudFront's origin-facing ranges ----

IP_RANGES = json.dumps({"prefixes": [
    {"ip_prefix": "130.176.0.0/18", "service": "CLOUDFRONT_ORIGIN_FACING"},
    {"ip_prefix": "15.158.0.0/16", "service": "CLOUDFRONT_ORIGIN_FACING"},
    {"ip_prefix": "13.32.0.0/15", "service": "CLOUDFRONT"},                    # viewer-facing: must not be trusted
    {"ip_prefix": "3.0.0.0/15", "service": "EC2"}],
    "ipv6_prefixes": [{"ipv6_prefix": "2600:9000:5300::/45", "service": "CLOUDFRONT_ORIGIN_FACING"},
                      {"ipv6_prefix": "2600:9000::/28", "service": "CLOUDFRONT"}]})


def test_caddy_trusts_only_cloudfront_origin_facing_ranges_strictly(tmp_path):
    r, _ = _front_door(tmp_path, "caddy", public_host="tmkg.3-25-9-9.sslip.io", ip_ranges=IP_RANGES)
    assert r.returncode == 0, r.stderr[-500:]
    cf = (tmp_path / "srv" / "app" / "Caddyfile").read_text()
    assert cf.startswith("{\n  servers {\n    trusted_proxies static 130.176.0.0/18 15.158.0.0/16 2600:9000:5300::/45\n"
                         "    trusted_proxies_strict\n  }\n}\n\ntmkg.3-25-9-9.sslip.io {")
    assert "13.32.0.0/15" not in cf and "3.0.0.0/15" not in cf and "2600:9000::/28" not in cf
    assert "    reverse_proxy 127.0.0.1:8000 {\n      header_up X-Forwarded-For {client_ip}\n    }\n" in cf   # one address to the app
    assert "Caddy trusts X-Forwarded-For from 3 CloudFront origin-facing ranges" in r.stdout


def test_caddy_global_block_holds_the_acme_email_and_the_proxies_together(tmp_path):
    r, _ = _front_door(tmp_path, "caddy", public_host="tmkg.3-25-9-9.sslip.io", ip_ranges=IP_RANGES, acme_email="owner@example.org")
    cf = (tmp_path / "srv" / "app" / "Caddyfile").read_text()
    assert r.returncode == 0 and cf.count("{\n") >= 1 and cf.startswith("{\n  email owner@example.org\n  servers {\n")
    assert cf.index("trusted_proxies_strict") < cf.index("tmkg.3-25-9-9.sslip.io {")


@pytest.mark.parametrize("ranges", [None, "not json", json.dumps({"prefixes": [{"ip_prefix": "130.176.0.1/18",
                                                                                 "service": "CLOUDFRONT_ORIGIN_FACING"}]}),
                                    json.dumps({"prefixes": []})])
def test_without_the_ranges_caddy_trusts_nobody_as_before(tmp_path, ranges):
    r, calls = _front_door(tmp_path, "caddy", public_host="tmkg.3-25-9-9.sslip.io", ip_ranges=ranges)
    assert r.returncode == 0, r.stderr[-500:]                                  # the boot goes on
    cf = (tmp_path / "srv" / "app" / "Caddyfile").read_text()
    assert cf.startswith("tmkg.3-25-9-9.sslip.io {") and "trusted_proxies" not in cf and "header_up" not in cf
    assert "    reverse_proxy 127.0.0.1:8000\n" in cf
    assert r.stdout.count("WARNING: CloudFront origin-facing ranges not read") == 1


def test_the_cloudfront_front_door_mode_fetches_no_ranges(tmp_path):
    r, calls = _front_door(tmp_path, "cloudfront", ip_ranges=IP_RANGES)
    assert r.returncode == 0 and "WARNING: CloudFront" not in r.stdout and "trusts X-Forwarded-For" not in r.stdout


# ---- A1: one CloudFront distribution in front of the Caddy site ----

def _dist_config(out):
    """The distribution config the dry run prints (the '#   ' lines)."""
    body = "\n".join(l[4:] for l in out.splitlines() if l.startswith("#   "))
    return json.loads(body)


def test_cloudfront_front_dry_run_builds_one_tagged_distribution_in_front_of_caddy(stubs):
    out = dry("cloudfront_front", stubs)
    assert out.count("create-distribution-with-tags") == 1 and "create-distribution " not in out
    assert "describe-addresses --filters Name=tag:project,Values=trialmindkg Name=tag:Name,Values=tmkg-app" in out
    for name in ("Managed-CachingDisabled", "Managed-CachingOptimized"):
        assert f"list-cache-policies --type managed --query 'CachePolicyList.Items[?CachePolicy.CachePolicyConfig.Name=='\\''{name}'\\''" in out
    assert "list-origin-request-policies --type managed" in out and "Managed-AllViewerExceptHostHeader" in out
    d = _dist_config(out)
    c = d["DistributionConfig"]
    assert c["CallerReference"] == "tmkg-front" and c["Comment"] == "tmkg front" and c["Enabled"] is True
    assert c["HttpVersion"] == "http2and3" and c["IsIPV6Enabled"] is True and c["PriceClass"] == "PriceClass_All"
    o = c["Origins"]["Items"][0]
    assert c["Origins"]["Quantity"] == 1 and o["DomainName"] == "tmkg.3-25-0-1.sslip.io"
    assert o["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"
    assert o["CustomOriginConfig"]["OriginSslProtocols"] == {"Quantity": 1, "Items": ["TLSv1.2"]}
    assert o["CustomOriginConfig"]["OriginReadTimeout"] == 60 and o["CustomOriginConfig"]["OriginKeepaliveTimeout"] == 60
    dflt = c["DefaultCacheBehavior"]
    assert dflt["AllowedMethods"]["Quantity"] == 7 and set(dflt["AllowedMethods"]["Items"]) == {
        "GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"}
    assert dflt["ViewerProtocolPolicy"] == "redirect-to-https" and dflt["Compress"] is True
    assert dflt["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"            # CachingDisabled (dry-run stand-in)
    assert dflt["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac"    # AllViewerExceptHostHeader
    dl = c["CacheBehaviors"]["Items"]
    assert c["CacheBehaviors"]["Quantity"] == 1 and dl[0]["PathPattern"] == "/downloads/*"
    assert dl[0]["AllowedMethods"]["Items"] == ["GET", "HEAD"] and dl[0]["CachePolicyId"] == "658327ea-f89d-4fab-a63d-7e88639e58f6"
    assert dl[0]["OriginRequestPolicyId"] == dflt["OriginRequestPolicyId"]
    assert {"Key": "project", "Value": "trialmindkg"} in d["Tags"]["Items"]
    assert "https://d111111abcdef8.cloudfront.net/" in out and "fallback: https://tmkg.3-25-0-1.sslip.io/" in out
    src = (AWS / "deploy_app.sh").read_text()
    assert '"CallerReference": "tmkg-app-${INSTANCE_ID}", "Comment": "tmkg app"' in src      # step 4 left as it was


CF_DIST = "E3REALFRONT\tarn:aws:cloudfront::111122223333:distribution/E3REALFRONT\td3abc.cloudfront.net\tDeployed\n"
CF_BASE = [{"match": ["describe-addresses"], "out": "16.178.197.241\n"},
           {"match": ["list-cache-policies", "Managed-CachingDisabled"], "out": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad\n"},
           {"match": ["list-cache-policies", "Managed-CachingOptimized"], "out": "658327ea-f89d-4fab-a63d-7e88639e58f6\n"},
           {"match": ["list-origin-request-policies"], "out": "b689b0a8-53d0-40ab-baf2-68738e2966ac\n"}]
CF_CONFIG = "TMKG_BUCKET=b\nAWS_REGION=ap-southeast-2\nAWS_ACCOUNT_ID=111122223333\n"


def test_cloudfront_front_creates_once_and_a_rerun_finds_it_by_comment_and_tag(tmp_path):
    (tmp_path / "a").mkdir()
    r, calls = real("cloudfront_front", tmp_path / "a", CF_BASE + [
        {"match": ["list-distributions"], "out": "None\n"},
        {"match": ["create-distribution-with-tags"], "out": "E3REALFRONT\td3abc.cloudfront.net\n"}], config=CF_CONFIG)
    assert r.returncode == 0, r.stderr[-500:]
    assert calls.count("create-distribution-with-tags") == 1 and "tmkg.16-178-197-241.sslip.io" in r.stdout
    assert "Amazon CloudFront: https://d3abc.cloudfront.net/" in r.stdout
    (tmp_path / "b").mkdir()
    r, calls = real("cloudfront_front", tmp_path / "b", CF_BASE + [
        {"match": ["list-distributions"], "out": CF_DIST},
        {"match": ["list-tags-for-resource", "distribution/E3REALFRONT"], "out": "trialmindkg\n"},
        {"match": ["get-distribution-config"], "out": "tmkg.16-178-197-241.sslip.io\n"}], config=CF_CONFIG)
    assert r.returncode == 0 and "create-distribution" not in calls and "exists: distribution E3REALFRONT" in r.stdout


def test_cloudfront_front_refuses_a_second_one_and_ignores_untagged_lookalikes(tmp_path):
    two = CF_DIST + CF_DIST.replace("E3REALFRONT", "E4OTHER")
    (tmp_path / "a").mkdir()
    r, calls = real("cloudfront_front", tmp_path / "a", CF_BASE + [
        {"match": ["list-distributions"], "out": two}, {"match": ["list-tags-for-resource"], "out": "trialmindkg\n"}], config=CF_CONFIG)
    assert r.returncode == 1 and "more than one distribution" in r.stderr and "create-distribution" not in calls
    (tmp_path / "b").mkdir()
    r, calls = real("cloudfront_front", tmp_path / "b", CF_BASE + [
        {"match": ["list-distributions"], "out": CF_DIST}, {"match": ["list-tags-for-resource"], "out": "None\n"},
        {"match": ["create-distribution-with-tags"], "out": "E5\td5.cloudfront.net\n"}], config=CF_CONFIG)
    assert r.returncode == 0 and "not ours, left alone" in r.stderr and calls.count("create-distribution-with-tags") == 1
    (tmp_path / "c").mkdir()
    r, calls = real("cloudfront_front", tmp_path / "c", CF_BASE + [
        {"match": ["list-distributions"], "out": CF_DIST}, {"match": ["list-tags-for-resource"], "out": "trialmindkg\n"},
        {"match": ["get-distribution-config"], "out": "tmkg.1-2-3-4.sslip.io\n"}], config=CF_CONFIG)
    assert r.returncode == 1 and "its origin is tmkg.1-2-3-4.sslip.io" in r.stderr
    (tmp_path / "d").mkdir()
    r, calls = real("cloudfront_front", tmp_path / "d", [{"match": ["describe-addresses"], "out": "None\n"}], config=CF_CONFIG)
    assert r.returncode == 1 and "no Elastic IP" in r.stderr and "list-distributions" not in calls


def test_cloudfront_front_looks_the_managed_policies_up_by_name(tmp_path):
    rules = [r for r in CF_BASE if "Managed-CachingOptimized" not in r["match"]] + [
        {"match": ["list-cache-policies", "Managed-CachingOptimized"], "out": "None\n"},
        {"match": ["list-distributions"], "out": "None\n"}]
    r, calls = real("cloudfront_front", tmp_path, rules, config=CF_CONFIG)
    assert r.returncode == 1 and "not found by name" in r.stderr and "create-distribution" not in calls


@pytest.mark.parametrize("state,enabled,rc", [("Deployed", "True", 0), ("InProgress", "True", 1), ("Deployed", "False", 1)])
def test_cloudfront_front_status_is_read_only_and_needs_deployed_and_enabled(tmp_path, state, enabled, rc):
    r, calls = real("cloudfront_front", tmp_path, [
        {"match": ["list-distributions"], "out": CF_DIST}, {"match": ["list-tags-for-resource"], "out": "trialmindkg\n"},
        {"match": ["get-distribution "], "out": f"{state}\t{enabled}\ttmkg.16-178-197-241.sslip.io\t4135ea2d-6df8-44a3-9df3-4b5a84be39ad\tb689b0a8-53d0-40ab-baf2-68738e2966ac\n"},
        {"match": ["get-cache-policy"], "out": "Managed-CachingDisabled\n"},
        {"match": ["get-origin-request-policy"], "out": "Managed-AllViewerExceptHostHeader\n"}], "--status", config=CF_CONFIG)
    assert r.returncode == rc, r.stdout + r.stderr
    assert "id:      E3REALFRONT" in r.stdout and "https://d3abc.cloudfront.net/" in r.stdout and f"status:  {state}" in r.stdout
    assert "cache policy Managed-CachingDisabled, origin request policy Managed-AllViewerExceptHostHeader" in r.stdout
    assert not re.search(r"\b(create|update|delete|tag)-", calls), calls


def test_cloudfront_front_status_missing_exits_1(tmp_path):
    r, _ = real("cloudfront_front", tmp_path, [{"match": ["list-distributions"], "out": "None\n"}], "--status", config=CF_CONFIG)
    assert r.returncode == 1 and "MISSING" in r.stdout


def test_teardown_matches_the_new_front_distribution_and_skips_none(stubs, tmp_path):
    out = dry("teardown", stubs, "--yes")
    assert "Comment=='\\''tmkg app'\\'' || Comment=='\\''tmkg front'\\''" in out
    assert "delete-distribution --id E2FRONTEXAMPLE" in out
    r, calls = real("teardown", tmp_path, [{"match": ["list-distributions"], "out": "None\n"}], "--yes", config=CF_CONFIG)
    assert r.returncode == 0, r.stderr[-500:]
    assert "--id None" not in calls                                           # no distribution: nothing to disable


# ---- A4: idle stop ----

def test_idle_stop_deploy_dry_run_makes_function_alarm_permission_and_sweep(stubs):
    out = dry("neptune_idle_stop", stubs)
    fn = [l for l in out.splitlines() if " lambda create-function " in l][0]
    for part in ("--function-name tmkg-idle-stop", "--runtime python3.12", "--role arn:aws:iam::123456789012:role/tmkg-idle-stop",
                 "--handler idle_stop.handler", "--timeout 30", "--tags project=trialmindkg", '"IDLE_MINUTES":"60"',
                 '"ACTIVITY_METRIC":"TotalRequestsPerSec"', '"UPTIME_METRIC":"CPUUtilization"', '"CLUSTER_ID":"tmkg-neptune"'):
        assert part in fn, part
    assert "update-function-code + update-function-configuration when it exists" in out
    alarm = [l for l in out.splitlines() if " put-metric-alarm " in l][0]
    for part in ("--alarm-name tmkg-neptune-idle", "--namespace AWS/Neptune", "--metric-name TotalRequestsPerSec",
                 "Name=DBClusterIdentifier,Value=tmkg-neptune", "--statistic Sum", "--period 300", "--evaluation-periods 12",
                 "--threshold 0", "--comparison-operator LessThanOrEqualToThreshold", "--treat-missing-data breaching",
                 "--alarm-actions arn:aws:lambda:us-east-1:123456789012:function:tmkg-idle-stop", "--actions-enabled",
                 "--tags Key=project,Value=trialmindkg"):
        assert part in alarm, part
    perm = [l for l in out.splitlines() if " add-permission " in l][0]
    assert "--principal lambda.alarms.cloudwatch.amazonaws.com" in perm
    assert "--source-arn arn:aws:cloudwatch:us-east-1:123456789012:alarm:tmkg-neptune-idle" in perm
    sweep = [l for l in out.splitlines() if l.startswith("+ aws scheduler create-schedule ")][0]
    assert "--group-name tmkg --name tmkg-neptune-idle-sweep" in sweep and "'rate(30 minutes)'" in sweep and "--state ENABLED" in sweep
    assert '"RoleArn":"arn:aws:iam::123456789012:role/tmkg-scheduler"' in sweep
    assert '"Arn":"arn:aws:lambda:us-east-1:123456789012:function:tmkg-idle-stop"' in sweep
    assert "create-schedule-group --name tmkg --tags Key=project,Value=trialmindkg" in out


def test_idle_stop_metric_names_come_from_the_config(stubs, tmp_path):
    cfg = tmp_path / "c.env"
    cfg.write_text("TMKG_BUCKET=b\nNEPTUNE_ACTIVITY_METRIC=OpenCypherRequestsPerSec\nNEPTUNE_UPTIME_METRIC=ServerlessDatabaseCapacity\n")
    out = dry("neptune_idle_stop", stubs, env_extra={"TMKG_AWS_CONFIG": str(cfg)})
    assert "--metric-name OpenCypherRequestsPerSec" in out and '"UPTIME_METRIC":"ServerlessDatabaseCapacity"' in out
    cfg.write_text("TMKG_BUCKET=b\nNEPTUNE_ACTIVITY_METRIC='x; rm -rf /'\n")
    bad = run("neptune_idle_stop", "--dry-run", env_extra={"TMKG_AWS_CONFIG": str(cfg)}, stubs_=stubs)
    assert bad.returncode == 2 and "letters and digits only" in bad.stderr
    assert run("neptune_idle_stop", "--dry-run", "--stop-now", stubs_=stubs).returncode == 2


def test_idle_stop_pause_resume_remove_dry_runs(stubs):
    pause = dry("neptune_idle_stop", stubs, "--pause")
    assert "--no-actions-enabled" in pause and "--state DISABLED" in pause and "create-function" not in pause
    assert "PAUSED" in pause
    resume = dry("neptune_idle_stop", stubs, "--resume")
    assert " --actions-enabled " in resume and "--state ENABLED" in resume
    remove = dry("neptune_idle_stop", stubs, "--remove")
    for part in ("scheduler delete-schedule --group-name tmkg --name tmkg-neptune-idle-sweep", "lambda remove-permission",
                 "cloudwatch delete-alarms --alarm-names tmkg-neptune-idle", "lambda delete-function --function-name tmkg-idle-stop",
                 "logs delete-log-group --log-group-name /aws/lambda/tmkg-idle-stop"):
        assert part in remove, part
    status = dry("neptune_idle_stop", stubs, "--status")
    for call_ in ("lambda get-function", "lambda get-policy", "cloudwatch describe-alarms", "scheduler get-schedule"):
        assert call_ in status
    assert "list-schedules" not in status + pause + resume + remove          # denied for the deployer (probe 14 Nov)


IDLE_CONFIG = "TMKG_BUCKET=b\nAWS_REGION=ap-southeast-2\nAWS_ACCOUNT_ID=111122223333\n"
FN_ARN = "arn:aws:lambda:ap-southeast-2:111122223333:function:tmkg-idle-stop"
ALARM_ARN = "arn:aws:cloudwatch:ap-southeast-2:111122223333:alarm:tmkg-neptune-idle"


def _idle_status_rules(fn="Active\tpython3.12\n", policy=None, alarm=None, sweep=None):
    policy = policy if policy is not None else json.dumps({"Statement": [{"Sid": "tmkg-neptune-idle-alarm",
                                                                         "Condition": {"ArnLike": {"AWS:SourceArn": ALARM_ARN}}}]}) + "\n"
    alarm = alarm if alarm is not None else f"True\tALARM\tTotalRequestsPerSec\t{FN_ARN}\n"
    sweep = sweep if sweep is not None else f"ENABLED\trate(30 minutes)\t{FN_ARN}\n"
    return [{"match": ["get-function"], **({"out": fn} if fn else {"err": "ResourceNotFoundException: Function not found", "rc": 254})},
            {"match": ["get-policy"], **({"out": policy} if policy != "-" else {"err": "ResourceNotFoundException", "rc": 254})},
            {"match": ["describe-alarms"], "out": alarm},
            {"match": ["get-schedule"], **({"out": sweep} if sweep != "-" else {"err": "ResourceNotFoundException", "rc": 254})}]


@pytest.mark.parametrize("kw,rc,needle", [
    ({}, 0, "permission: the alarm tmkg-neptune-idle may invoke tmkg-idle-stop"),
    ({"fn": ""}, 1, "function tmkg-idle-stop: MISSING"),
    ({"policy": "-"}, 1, "permission: MISSING"),
    ({"alarm": "None\n"}, 1, "alarm tmkg-neptune-idle: MISSING"),
    ({"alarm": f"False\tALARM\tTotalRequestsPerSec\t{FN_ARN}\n"}, 1, "its action is OFF"),
    ({"alarm": "True\tALARM\tTotalRequestsPerSec\tarn:aws:sns:x\n"}, 1, "its action is not"),
    ({"sweep": "-"}, 1, "sweep tmkg-neptune-idle-sweep: MISSING"),
    ({"sweep": f"DISABLED\trate(30 minutes)\t{FN_ARN}\n"}, 1, "the sweep is not enabled"),
])
def test_idle_stop_status_exits_0_only_when_everything_is_there(tmp_path, kw, rc, needle):
    r, calls = real("neptune_idle_stop", tmp_path, _idle_status_rules(**kw), "--status", config=IDLE_CONFIG)
    assert r.returncode == rc, r.stdout + r.stderr
    assert needle in r.stdout
    assert not re.search(r"\b(create|put|update|delete|add|remove)-", calls), calls


def test_idle_stop_rerun_updates_the_function_and_tolerates_existing_pieces(tmp_path):
    r, calls = real("neptune_idle_stop", tmp_path, [
        {"match": ["lambda get-function"], "out": FN_ARN + "\n"},
        {"match": ["add-permission"], "err": "An error occurred (ResourceConflictException): The statement id exists", "rc": 254},
        {"match": ["create-schedule-group"], "err": "An error occurred (ConflictException): exists", "rc": 254},
        {"match": ["get-schedule"], "out": "ENABLED\n"}], config=IDLE_CONFIG)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "create-function" not in calls and "update-function-code --function-name tmkg-idle-stop --zip-file fileb://" in calls
    assert "update-function-configuration --function-name tmkg-idle-stop --runtime python3.12" in calls
    assert "update-schedule --group-name tmkg --name tmkg-neptune-idle-sweep" in calls and "create-schedule " not in calls
    assert "exists: permission tmkg-neptune-idle-alarm" in r.stdout


def test_idle_stop_first_run_creates_and_zips_the_function(tmp_path):
    r, calls = real("neptune_idle_stop", tmp_path, [
        {"match": ["lambda get-function"], "err": "An error occurred (ResourceNotFoundException): Function not found", "rc": 254},
        {"match": ["get-schedule"], "err": "An error occurred (ResourceNotFoundException)", "rc": 254}], config=IDLE_CONFIG)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "lambda create-function --function-name tmkg-idle-stop" in calls and "lambda wait function-active-v2" in calls
    assert "create-schedule --group-name tmkg --name tmkg-neptune-idle-sweep" in calls


def test_idle_stop_alarm_falls_back_to_no_tag_only_when_tagging_is_refused(tmp_path):
    r, calls = real("neptune_idle_stop", tmp_path, [
        {"match": ["put-metric-alarm", "--tags"], "err": "AccessDenied: not authorized to perform: cloudwatch:TagResource", "rc": 254},
        {"match": ["get-schedule"], "out": "DISABLED\n"}], "--pause", config=IDLE_CONFIG)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls.count("put-metric-alarm") == 2 and "written without the project tag" in r.stdout
    (tmp_path / "x").mkdir()
    r, _ = real("neptune_idle_stop", tmp_path / "x", [
        {"match": ["put-metric-alarm"], "err": "AccessDenied: cloudwatch:PutMetricAlarm", "rc": 254}], "--pause", config=IDLE_CONFIG)
    assert r.returncode == 1 and "PutMetricAlarm" in r.stderr


def test_the_lambda_zip_holds_the_module_at_its_root(tmp_path):
    import zipfile
    z = tmp_path / "f.zip"
    subprocess.run([sys.executable, "-m", "zipfile", "-c", str(z), str(AWS / "lambda" / "idle_stop.py")], check=True)
    assert zipfile.ZipFile(z).namelist() == ["idle_stop.py"]                  # handler idle_stop.handler resolves


def test_teardown_removes_the_idle_stop_pieces(stubs):
    out = dry("teardown", stubs, "--yes")
    for part in ("delete-schedule --group-name tmkg --name tmkg-neptune-idle-sweep", "delete-alarms --alarm-names tmkg-neptune-idle",
                 "lambda delete-function --function-name tmkg-idle-stop", "logs delete-log-group --log-group-name /aws/lambda/tmkg-idle-stop",
                 "delete-role --role-name tmkg-idle-stop", "policy/tmkg-deployer-policy-2"):
        assert part in out, part


# ---- A6: schedules ----

def test_schedule_status_reads_the_four_by_name(stubs):
    out = dry("schedule_stop_start", stubs, "--status")
    assert out.count("scheduler get-schedule --group-name tmkg --name ") == 4 and "list-schedules" not in out
    assert "create-schedule" not in out and "tmkg-neptune-stop    ENABLED   cron(0 4 * * ? *)" in out


@pytest.mark.parametrize("stop,rc", [("ENABLED\tcron(0 4 * * ? *)\n", 0), ("DISABLED\tcron(0 4 * * ? *)\n", 1), (None, 1)])
def test_schedule_status_exit_code_follows_the_neptune_stop(tmp_path, stop, rc):
    rules = [{"match": ["--name tmkg-neptune-stop"], **({"out": stop} if stop else {"err": "ResourceNotFoundException", "rc": 254})},
             {"match": ["--name tmkg-neptune-start"], "out": "DISABLED\tcron(0 6 * * ? *)\n"},
             {"match": ["get-schedule"], "err": "An error occurred (ResourceNotFoundException)", "rc": 254}]
    r, calls = real("schedule_stop_start", tmp_path, rules, "--status", config=CF_CONFIG)
    assert r.returncode == rc, r.stdout + r.stderr
    assert "tmkg-app-stop        absent" in r.stdout and "tmkg-neptune-start   DISABLED  cron(0 6 * * ? *)" in r.stdout
    assert "create-" not in calls and "update-" not in calls
    if stop is None:
        assert "tmkg-neptune-stop    absent" in r.stdout and "nothing stops Amazon Neptune overnight" in r.stdout


def test_example_config_stops_at_us_evening_and_has_the_new_values():
    cfg = (AWS / "config.example.env").read_text()
    assert 'STOP_CRON="cron(0 4 * * ? *)"' in cfg and "04:00 UTC = 20:00 US Pacific, 23:00 US Eastern" in cfg
    for var in ("NEPTUNE_WAKE_MINUTES", "NEPTUNE_ACTIVITY_METRIC", "NEPTUNE_UPTIME_METRIC"):
        assert re.search(rf"^{var}=\s", cfg, re.M), var
    r = subprocess.run([BASH, "-c", f'set -u; source "{AWS / "config.example.env"}"; echo "[$NEPTUNE_WAKE_MINUTES][$STOP_CRON]"'],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "[][cron(0 4 * * ? *)]"


# ---- A7: README ----

def test_readme_lists_the_new_scripts_cost_control_and_no_stale_neptune_claim():
    readme = (AWS / "README.md").read_text()
    for s in ("cloudfront_front.sh", "neptune_idle_stop.sh", "tmkg-deployer-policy-2", "deployer_policy_2.json"):
        assert s in readme, s
    steps = readme.split("## Steps, in this order")[1].split("\n## ")[0]
    for s in ("bash aws/cloudfront_front.sh", "bash aws/neptune_idle_stop.sh", "bash aws/schedule_stop_start.sh --status"):
        assert s in steps, s
    assert "No Neptune cluster runs on our AWS account" not in readme
    cost = readme.split("## Cost control")[1].split("\n## ")[0].lower()
    for rule in ("minimum capacity", "1 ncu", "every night", "60 minutes", "snapshot", "test cluster", "idle"):
        assert rule in cost, rule
    neptune = readme.split("## The whole graph in Amazon Neptune")[1].split("\n## ")[0]
    assert "outputs/review/" in neptune and not re.search(r"\b\d+(\.\d+)? ?(h|hours|minutes)\b.*load", neptune)


# ---- A3: neptune_load.sh from outside the VPC, two steps, retry ----

def test_neptune_load_full_remote_dry_run_shows_two_jobs_the_dependency_and_run_command(stubs):
    out = dry("neptune_load", stubs, "--full", "--remote", env_extra={"TMKG_NEPTUNE_S3_URI": "s3://tmkg-b/neptune/v14"})
    posts = [l for l in out.splitlines() if l.startswith("+ POST ") and "/loader" in l]
    assert len(posts) == 2
    bodies = [json.loads(l.split("/loader   ", 1)[1].split("}   (on ", 1)[0] + "}") for l in posts]
    assert bodies[0]["source"] == "s3://tmkg-b/neptune/v14/nodes/" and bodies[1]["source"] == "s3://tmkg-b/neptune/v14/edges/"
    for b in bodies:
        assert b["format"] == "opencypher" and b["failOnError"] == "TRUE" and b["userProvidedEdgeIds"] == "TRUE"
        assert b["queueRequest"] == "TRUE" and b["parallelism"] == "HIGH"
    assert "dependencies" not in bodies[0] and bodies[1]["dependencies"] == ["<loadId of the nodes job>"]
    assert "aws ssm send-command --document-name AWS-RunShellScript --instance-ids i-0example" in out
    assert "get-command-invocation" in out and "Name=tag:Name,Values=tmkg-app" in out
    assert "count(n) AS nodes" in out and "count(r) AS edges" in out and "manifest.json" in out
    single = dry("neptune_load", stubs, "--full")
    assert '"parallelism":"HIGH"' in single and "userProvidedEdgeIds" not in single     # without the URI: one job as before
    assert run("neptune_load", "--dry-run", env_extra={"TMKG_NEPTUNE_S3_URI": "s3://b/x; rm -rf /"}, stubs_=stubs).returncode == 2


def _retry(*args):
    r = subprocess.run([BASH, "-c", f'source "{AWS / "neptune_load.sh"}"; retry_decision "$@"', "x", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_retry_decision_is_one_step_lower_at_most_three_times():
    assert _retry("LOAD_COMPLETED", "HIGH", "0") == "done"
    assert _retry("LOAD_DATA_DEADLOCK", "OVERSUBSCRIBE", "0") == "retry HIGH"
    assert _retry("LOAD_DATA_DEADLOCK", "HIGH", "0") == "retry MEDIUM"
    assert _retry("LOAD_FAILED", "MEDIUM", "1", '{"errors": {"errorCode": "LOAD_DATA_DEADLOCK"}}') == "retry LOW"
    assert _retry("LOAD_DATA_DEADLOCK", "LOW", "2") == "retry LOW"
    assert _retry("LOAD_DATA_DEADLOCK", "HIGH", "3") == "fail"
    assert _retry("LOAD_FAILED", "HIGH", "0", '{"errors": {"errorCode": "PARSING_ERROR"}}') == "fail"
    assert _retry("LOAD_CANCELLED_BY_USER", "HIGH", "0") == "fail"


LOADER_STUB = r'''#!/usr/bin/env python3
"""Stub curl for the Neptune loader: POST /loader assigns L1, L2, ... and logs the body; GET /loader/Ln answers the n-th
status of STUB_PLAN (comma list); details=true adds the error code; openCypher answers STUB_NODES / STUB_EDGES."""
import json, os, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("https://"))
state = os.environ["STUB_STATE"]
n = int(open(state).read()) if os.path.exists(state) else 0
plan = os.environ.get("STUB_PLAN", "LOAD_COMPLETED").split(",")
if url.endswith("/loader"):
    body = args[args.index("-d") + 1]
    n += 1
    open(state, "w").write(str(n))
    with open(os.environ["STUB_BODIES"], "a") as f:
        f.write(body + "\n")
    print(json.dumps({"payload": {"loadId": f"L{n}"}}))
elif "/loader/L" in url:
    i = int(url.split("/loader/L")[1].split("?")[0])
    st = plan[min(i, len(plan)) - 1]
    if "details=true" in url:
        print(json.dumps({"payload": {"overallStatus": {"status": st}, "errors": {"errorCode": st}}}))
    else:
        print(json.dumps({"payload": {"overallStatus": {"status": st}}}))
elif url.endswith("/openCypher"):
    q = " ".join(args)
    key = "nodes" if "count(n)" in q else "edges"
    print(json.dumps({"results": [{key: int(os.environ["STUB_" + key.upper()])}]}))
'''


def _two_step(tmp_path, plan, remote=False):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("curl", LOADER_STUB), ("sleep", "#!/bin/sh\nexit 0\n")):
        f = bindir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    if remote:      # aws stub: ec2 lookup + Run Command that really runs the curl line (through the curl stub) and returns its output
        aws = bindir / "aws"
        aws.write_text(r'''#!/usr/bin/env python3
import json, os, subprocess, sys
a = sys.argv[1:]
open(os.environ["STUB_AWS_LOG"], "a").write(" ".join(a) + "\n")
if a[:2] == ["ec2", "describe-instances"]:
    print("i-0abc123")
elif a[:2] == ["ssm", "send-command"]:
    cmd = json.loads(a[a.index("--parameters") + 1])["commands"][0]
    out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True)
    d = os.environ["STUB_STATE"] + ".cmd"
    n = len(os.listdir(d)) if os.path.isdir(d) else 0
    os.makedirs(d, exist_ok=True)
    json.dump({"Status": "Success" if out.returncode == 0 else "Failed", "StandardOutputContent": out.stdout,
               "StandardErrorContent": out.stderr}, open(f"{d}/c{n}", "w"))
    print(f"c{n}")
elif a[:2] == ["ssm", "get-command-invocation"]:
    cid = a[a.index("--command-id") + 1]
    print(open(os.environ["STUB_STATE"] + ".cmd/" + cid).read())
''')
        aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"counts": {"nodes": 1326108, "edges": 29295399}}))
    cfg = tmp_path / "stub.env"
    cfg.write_text("TMKG_BUCKET=b\nAWS_REGION=ap-southeast-2\nNEPTUNE_ENDPOINT=n.example\nNEPTUNE_LOAD_ROLE_ARN=arn:aws:iam::1:role/r\n")
    env = {**os.environ, "TMKG_AWS_CONFIG": str(cfg), "TMKG_MANIFEST": str(manifest), "TMKG_NEPTUNE_S3_URI": "s3://tmkg-b/neptune/v14/",
           "STUB_STATE": str(tmp_path / "state"), "STUB_BODIES": str(tmp_path / "bodies"), "STUB_PLAN": plan,
           "STUB_NODES": "1326108", "STUB_EDGES": "29295399", "STUB_AWS_LOG": str(tmp_path / "aws_log")}
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    args = [BASH, str(AWS / "neptune_load.sh"), "--full"] + (["--remote"] if remote else [])
    r = subprocess.run(args, capture_output=True, text=True, env=env, cwd=ROOT)
    bodies = [json.loads(l) for l in (tmp_path / "bodies").read_text().splitlines()] if (tmp_path / "bodies").exists() else []
    return r, bodies


def test_two_step_load_retries_a_deadlock_one_step_lower_and_edges_wait_for_the_nodes_that_completed(tmp_path):
    r, bodies = _two_step(tmp_path, "LOAD_DATA_DEADLOCK,LOAD_COMPLETED,LOAD_COMPLETED")
    assert r.returncode == 0, r.stdout + r.stderr
    assert [b["source"].rsplit("/", 2)[-2] for b in bodies] == ["nodes", "nodes", "edges"]
    assert [b["parallelism"] for b in bodies] == ["HIGH", "MEDIUM", "HIGH"]
    assert "mode" not in bodies[0] and bodies[1]["mode"] == "RESUME" and "mode" not in bodies[2]
    assert bodies[2]["dependencies"] == ["L2"]                                # the retried nodes job, not the deadlocked one
    assert "retry 1 of 3 with parallelism MEDIUM" in r.stdout and "Counts match the manifest." in r.stdout


def test_two_step_load_gives_up_after_three_retries(tmp_path):
    r, bodies = _two_step(tmp_path, "LOAD_DATA_DEADLOCK,LOAD_DATA_DEADLOCK,LOAD_DATA_DEADLOCK,LOAD_DATA_DEADLOCK")
    assert r.returncode == 1 and len(bodies) == 4 and "ended LOAD_DATA_DEADLOCK" in r.stderr
    assert [b["parallelism"] for b in bodies] == ["HIGH", "MEDIUM", "LOW", "LOW"]
    assert all("edges" not in b["source"] for b in bodies) and "COUNT MISMATCH" not in r.stderr


def test_two_step_load_remote_runs_every_call_through_run_command(tmp_path):
    r, bodies = _two_step(tmp_path, "LOAD_COMPLETED,LOAD_COMPLETED", remote=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(bodies) == 2 and bodies[1]["dependencies"] == ["L1"]
    log = (tmp_path / "aws_log").read_text()
    assert log.count("ssm send-command --document-name AWS-RunShellScript --instance-ids i-0abc123") == 6   # 2 POST + 2 GET + 2 counts
    assert log.count("ssm get-command-invocation") == 6 and "Counts match the manifest." in r.stdout
    (tmp_path / "m").mkdir()
    r, _ = _two_step(tmp_path / "m", "LOAD_COMPLETED,LOAD_COMPLETED", remote=True)
    assert r.returncode == 0


def test_neptune_ctl_cannot_be_turned_against_the_host_by_the_app_folder(tmp_path):
    """The app (uid 10001) can write /srv/neptune. A link or a folder it leaves at status.json must not make root write elsewhere."""
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    out, _ = render_user_data(ENDPOINT)
    body = out.split("cat > /srv/app/neptune_ctl.sh <<'NEPTUNE'\n", 1)[1].split("\nNEPTUNE\n", 1)[0]
    for case in ("link", "dir", "dirlink"):
        d = tmp_path / case
        d.mkdir()
        (tmp_path / f"{case}_target").mkdir()
        if case == "link":
            (d / "status.json").symlink_to(outside)
        elif case == "dir":
            (d / "status.json").mkdir()
        else:
            (d / "status.json").symlink_to(tmp_path / f"{case}_target")
        script = tmp_path / f"{case}.sh"
        script.write_text(body.replace("/srv/neptune", str(d)).replace("/var/log/tmkg-neptune.log", str(tmp_path / "log")) + "\n")
        bindir = tmp_path / f"{case}_bin"
        bindir.mkdir()
        (bindir / "aws").write_text("#!/bin/sh\necho available\n")
        (bindir / "aws").chmod(0o755)
        r = subprocess.run([BASH, str(script)], capture_output=True, text=True,
                           env={**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"})
        assert r.returncode == 0, (case, r.stderr)
        assert outside.read_text() == "keep" and not list((tmp_path / f"{case}_target").iterdir()), case
        assert not [p for p in d.iterdir() if p.name.startswith(".status.")], case        # no temp file left
        if case != "dir":
            assert not (d / "status.json").is_symlink() and json.loads((d / "status.json").read_text())["state"] == "available"


def test_show_quotes_every_argument_so_sh_reads_it_back_unchanged():
    """macOS bash 3.2 and bash 5 must print the same dry-run line: show() no longer uses ${a//'/...} (14 Nov, macOS)."""
    words = ["aws", "Comment=='tmkg app' || Comment=='tmkg front'", "it's", "'", "''", "", "a b", "x\\'y", "plain"]
    script = f'source "{AWS / "lib.sh"}"; show "$@"'
    line = subprocess.run([BASH, "-c", script, "x", *words], capture_output=True, text=True).stdout
    assert line.startswith("+ ") and "Comment=='\\''tmkg app'\\'' || Comment=='\\''tmkg front'\\''" in line
    back = subprocess.run(["sh", "-c", "eval \"set -- $1\"; for a in \"$@\"; do printf '%s\\0' \"$a\"; done", "x", line[2:].rstrip("\n")],
                          capture_output=True, text=True).stdout
    assert back.split("\0")[:-1] == words
