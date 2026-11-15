import sys
import pytest
from harness.dev_controller import SandboxValidator, Blocked, run_process, check_response

def test_process_timeout_and_failure(tmp_path):
    r = run_process([sys.executable, '-c', 'import time; time.sleep(5)'], tmp_path, 0.1)
    assert r['timeout'] and r['returncode'] != 0
    assert run_process([sys.executable, '-c', 'raise SystemExit(3)'], tmp_path, 2)['returncode'] == 3

def test_unavailable_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    with pytest.raises(Blocked): SandboxValidator().run({'argv': ['true'], 'timeout_seconds': 1}, tmp_path)

def test_judge_schema_rejects_malformed():
    with pytest.raises(ValueError): check_response('JUDGE', {'verdict': True})

def test_codex_adapter_real_executable(tmp_path, monkeypatch):
    import json
    from harness.dev_controller import CodexBackend
    executable = tmp_path / 'codex'
    executable.write_text('#!/usr/bin/python3\nimport json,sys\na=sys.argv[1:]\nopen("argv.json", "w").write(json.dumps(a))\nopen(a[a.index("-o")+1], "w").write(json.dumps({"status":"ready","reason":"","plan":"ok"}))\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path))
    request = {'schema': {}, 'route': {'model': 'test-model', 'effort': 'high'}, 'prompt': 'test'}
    assert CodexBackend().call(request, tmp_path, 5)['response']['plan'] == 'ok'
    argv = json.loads((tmp_path / 'argv.json').read_text())
    assert argv[argv.index('-a') + 1] == 'never'
    assert argv[argv.index('--sandbox') + 1] == 'read-only'
    assert argv[argv.index('-m') + 1] == 'test-model'
    assert '--ignore-user-config' in argv and '--ephemeral' in argv
    assert not any('bypass' in a or 'dangerously' in a for a in argv)
    executable.write_text('#!/usr/bin/python3\nimport sys\na=sys.argv\nopen(a[a.index("-o")+1], "w").write("{bad")\n')
    with pytest.raises(ValueError): CodexBackend().call(request, tmp_path, 5)
    executable.write_text('#!/usr/bin/python3\nraise SystemExit(7)\n')
    with pytest.raises(Blocked, match='transport failed'): CodexBackend().call(request, tmp_path, 5)

def test_sandbox_profile_denies_signal_and_start_failure(tmp_path, monkeypatch):
    import harness.dev_controller as dc
    monkeypatch.setattr(dc.sys, 'platform', 'darwin')
    monkeypatch.setattr(dc.Path, 'exists', lambda self: True)
    def fake(argv, *args, **kwargs):
        assert '(deny signal)' in argv[2]
        return {'returncode': 71, 'timeout': False, 'stdout': '', 'stderr': 'sandbox-exec: sandbox_apply: denied'}
    monkeypatch.setattr(dc, 'run_process', fake)
    with pytest.raises(Blocked, match='sandbox could not start'):
        SandboxValidator().run({'argv': ['true'], 'timeout_seconds': 1}, tmp_path)

def test_process_cleans_up_descendants(tmp_path):
    import time
    marker = tmp_path / 'escaped'
    child = 'import time,pathlib; time.sleep(1); pathlib.Path(%r).write_text("escaped")' % str(marker)
    parent = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",%r])' % child
    result = run_process([sys.executable, '-c', parent], tmp_path, 5)
    assert result['returncode'] == 0
    time.sleep(1.2)
    assert not marker.exists()

@pytest.mark.skipif(sys.platform != 'darwin', reason='requires macOS sandbox-exec')
def test_sandbox_cannot_signal_external_process(tmp_path):
    import subprocess
    import signal
    target = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        command = 'import os,signal; os.kill(%d, signal.SIGTERM)' % target.pid
        try:
            result = SandboxValidator().run({'argv': [sys.executable, '-c', command], 'timeout_seconds': 5}, tmp_path)
        except Blocked as exc:
            assert 'sandbox could not start' in str(exc)
        else:
            assert result['returncode'] != 0
        assert target.poll() is None
    finally:
        target.terminate()
        target.wait(timeout=5)
