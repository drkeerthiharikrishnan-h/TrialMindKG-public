import json
import subprocess
from pathlib import Path
import pytest
from harness.dev_controller import Controller, Blocked, ControllerError, digest, strict_load, validate_manifest

@pytest.fixture
def repo(tmp_path):
    subprocess.run(['git', 'init', '-q', '-b', 'codex/test', str(tmp_path)], check=True)
    return tmp_path

def manifest(**overrides):
    m = {'objective': 'create file', 'criteria': [{'id': 'c', 'text': 'correct'}], 'editable_paths': ['docs/test.txt'], 'evidence_paths': [], 'validations': [{'argv': ['true'], 'timeout_seconds': 5}], 'classification': 'routine', 'backend': 'codex'}
    m.update(overrides); return m

class Validator:
    def __init__(self, failures=0): self.failures = failures
    def run(self, spec, root):
        result = {'returncode': int(self.failures > 0), 'timeout': False, 'stdout': '', 'stderr': ''}
        self.failures -= 1; return result

class Backend:
    def call(self, req, root, timeout):
        phase = req['phase']
        if phase == 'PLAN': response = {'status': 'ready', 'reason': '', 'plan': 'write the file'}
        elif phase == 'IMPLEMENT': response = {'status': 'done', 'reason': '', 'edits': [{'path': 'docs/test.txt', 'content': 'ok\n'}]}
        else:
            review = json.loads(req['prompt'].split('Inputs: ', 1)[1])
            response = {k: review[k] for k in ('run_id', 'attempt_id', 'config_sha256', 'review_input_sha256')}
            response.update(verdict='accept', criteria=[{'id': 'c', 'status': 'pass', 'evidence_ids': ['validation:0'], 'reason': 'verified'}], findings=[], limitations=[])
        return {'response': response}

def test_success_and_rework(repo):
    c = Controller(repo, Backend(), Validator(1)); id = c.init(manifest()); s = c.execute(id)
    assert s['phase'] == 'ACCEPTED' and s['attempt_count'] == 2 and s['model_calls'] == 4
    assert (repo / 'docs/test.txt').read_text() == 'ok\n'

def test_budget_exhaustion(repo):
    c = Controller(repo, Backend(), Validator(4)); id = c.init(manifest(max_reworks=1)); assert c.execute(id)['phase'] == 'NEEDS_ATTENTION'

def test_manifest_and_json_strictness():
    with pytest.raises(ValueError): strict_load('{"x":1,"x":2}')
    with pytest.raises(ValueError): strict_load('{"x":NaN}')
    with pytest.raises(ValueError): validate_manifest(manifest(max_reworks=True))
    with pytest.raises(ValueError): validate_manifest(manifest(editable_paths=['../outside']))
    with pytest.raises(ValueError): validate_manifest(manifest(editable_paths=['pipelines/linkpred/a']))

def test_bridge_binding_and_duplicates(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest(backend='bridge'))
    assert c.execute(id)['phase'] == 'PLAN'; req = c.request(id)
    env = {k: req[k] for k in ('nonce', 'run_id', 'attempt_id', 'config_sha256', 'input_sha256')}; env['response'] = {'status': 'ready', 'reason': '', 'plan': 'ok'}
    bad = dict(env, nonce='stale')
    with pytest.raises(ControllerError): c.submit(id, bad)
    c.submit(id, env)
    with pytest.raises(ControllerError): c.submit(id, env)
    assert c.execute(id)['phase'] == 'IMPLEMENT'

def test_symlink_and_mutation(repo):
    (repo / 'docs').symlink_to(repo.parent)
    c = Controller(repo, Backend(), Validator())
    with pytest.raises(Blocked): c.init(manifest())
    (repo / 'docs').unlink()
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest(backend='bridge')); c.execute(id)
    (repo / 'docs').mkdir(); (repo / 'docs/test.txt').write_text('changed')
    req = c.request(id); env = {k: req[k] for k in ('nonce', 'run_id', 'attempt_id', 'config_sha256', 'input_sha256')}; env['response'] = {'status': 'ready', 'reason': '', 'plan': 'ok'}; c.submit(id, env)
    c.execute(id); req = c.request(id); env = {k: req[k] for k in ('nonce', 'run_id', 'attempt_id', 'config_sha256', 'input_sha256')}; env['response'] = {'status': 'done', 'reason': '', 'edits': [{'path': 'docs/test.txt', 'content': 'ok'}]}; c.submit(id, env)
    assert c.execute(id)['phase'] == 'BLOCKED'; assert (repo / 'docs/test.txt').read_text() == 'changed'

def test_journal_recovery(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest()); s = c.load(id); a = c.new_attempt(s)
    c.prepare(s, [{'path': 'docs/test.txt', 'content': 'ok\n'}]); s = c.load(id)
    (repo / 'docs').mkdir(); (repo / 'docs/test.txt').write_text('ok\n')
    c.apply(s); assert c.load(id)['phase'] == 'VALIDATE'

def test_evidence_tamper(repo):
    (repo / 'evidence.txt').write_text('first')
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest(evidence_paths=['evidence.txt'])); (repo / 'evidence.txt').write_text('second')
    assert c.execute(id)['phase'] == 'BLOCKED'

def test_lock(repo):
    c = Controller(repo)
    with c.lock():
        with pytest.raises(Blocked):
            with c.lock(): pass

def test_budget_persists_across_bridge_waits(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest(backend='bridge', max_model_calls=1)); c.execute(id)
    req = c.request(id); env = {k: req[k] for k in ('nonce', 'run_id', 'attempt_id', 'config_sha256', 'input_sha256')}; env['response'] = {'status': 'ready', 'reason': '', 'plan': 'ok'}; c.submit(id, env)
    s = c.execute(id); assert s['phase'] == 'BLOCKED' and s['model_calls'] == 1

def test_judge_invalid_evidence_and_unknown_block(repo):
    class BadJudge(Backend):
        def call(self, req, root, timeout):
            result = super().call(req, root, timeout)
            if req['phase'] == 'JUDGE': result['response']['criteria'][0]['evidence_ids'] = ['invented']
            return result
    c = Controller(repo, BadJudge(), Validator()); id = c.init(manifest()); assert c.execute(id)['phase'] == 'BLOCKED'

def test_config_tamper_detected(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest()); s = c.load(id); s['manifest']['objective'] = 'different'; c.save(s)
    with pytest.raises(Blocked): c.load(id)

def test_interrupted_apply_does_not_overwrite_external_edit(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest()); s = c.load(id); c.new_attempt(s)
    c.prepare(s, [{'path': 'docs/test.txt', 'content': 'desired'}]); s = c.load(id)
    (repo / 'docs').mkdir(); (repo / 'docs/test.txt').write_text('external')
    with pytest.raises(Blocked): c.apply(s)
    assert (repo / 'docs/test.txt').read_text() == 'external'

def test_pause_and_cancel_idle(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest())
    assert c.control(id, 'pause')['paused']; assert c.execute(id)['phase'] == 'PLAN'
    c.control(id, 'resume'); assert c.control(id, 'cancel')['phase'] == 'CANCELLED'

def test_findings_prevent_acceptance(repo):
    class Findings(Backend):
        def call(self, req, root, timeout):
            result = super().call(req, root, timeout)
            if req['phase'] == 'JUDGE': result['response']['findings'] = ['unresolved issue']
            return result
    c = Controller(repo, Findings(), Validator()); id = c.init(manifest(max_reworks=0))
    assert c.execute(id)['phase'] == 'NEEDS_ATTENTION'

def test_scientific_criteria_are_required():
    with pytest.raises(ValueError): validate_manifest(manifest(classification='science'))
    with pytest.raises(ValueError): validate_manifest(manifest(editable_paths=['docs/NEXT_STEPS.md']))

def test_state_symlink_refused_before_writes(repo):
    target = repo.parent / 'target'; target.mkdir(); (repo / '.dev-controller').symlink_to(target, target_is_directory=True)
    with pytest.raises(Blocked): Controller(repo).init(manifest())
    assert list(target.iterdir()) == []

def test_live_cancel_terminates_child(repo):
    import sys
    import threading
    import time
    from harness.dev_controller import run_process
    started = threading.Event()
    class Slow(Backend):
        def call(self, req, root, timeout):
            started.set()
            run_process([sys.executable, '-c', 'import time; time.sleep(10)'], root, 15, control=self.control)
            raise AssertionError('cancel should interrupt')
    c = Controller(repo, Slow(), Validator()); id = c.init(manifest())
    results = []
    t = threading.Thread(target=lambda: results.append(c.execute(id))); t.start()
    assert started.wait(5)
    c.control(id, 'cancel'); t.join(5)
    assert not t.is_alive() and results[0]['phase'] == 'CANCELLED'

def test_interrupted_call_does_not_repeat(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest(backend='bridge')); c.execute(id)
    s = c.load(id); s['manifest']['backend'] = 'codex'; s['config_sha256'] = digest(s['manifest']); s['pending']['active'] = True; c.save(s)
    result = c.execute(id)
    assert result['phase'] == 'BLOCKED' and result['model_calls'] == 1

def test_branch_change_blocks_resume(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest())
    subprocess.run(['git', 'checkout', '-q', '-b', 'codex/other'], cwd=repo, check=True)
    with pytest.raises(Blocked): c.execute(id)

def test_overflow_and_casefold_guards(repo):
    from harness.dev_controller import is_original_workspace
    for value in ('1e999', '-1e999'):
        with pytest.raises(ValueError): strict_load(value)
    with pytest.raises(ValueError): validate_manifest(manifest(editable_paths=['HARNESS/dev_controller.py']))
    with pytest.raises(ValueError): validate_manifest(manifest(editable_paths=['docs/A', 'docs/a']))
    assert is_original_workspace(Path.home() / 'projects' / 'TRIALMINDKG' / 'subdir')
    with pytest.raises(ValueError):
        validate_manifest(manifest(classification='science', science_declarations=dict.fromkeys(('protocol', 'provenance', 'comparability', 'uncertainty'), 'c')))
    (repo / '.AWS').mkdir(); (repo / '.AWS' / 'secret').write_text('private')
    (repo / 'alias').symlink_to(repo / '.AWS' / 'secret')
    with pytest.raises(Blocked): Controller(repo).init(manifest(evidence_paths=['alias']))

def test_review_uses_original_baseline_and_file_evidence(repo):
    (repo / 'docs').mkdir(); (repo / 'docs/test.txt').write_text('original')
    class Rework(Backend):
        reviews = []
        def call(self, req, root, timeout):
            result = super().call(req, root, timeout)
            if req['phase'] == 'JUDGE':
                review = json.loads(req['prompt'].split('Inputs: ', 1)[1]); self.reviews.append(review)
                result['response']['criteria'][0]['evidence_ids'] = [next(x for x in review['available_evidence_ids'] if x.startswith('file:'))]
                if len(self.reviews) == 1: result['response']['verdict'] = 'rework'
            return result
    backend = Rework(); c = Controller(repo, backend, Validator()); id = c.init(manifest())
    assert c.execute(id)['phase'] == 'ACCEPTED'
    assert len(backend.reviews) == 2
    assert all(r['before_files']['docs/test.txt'] == 'original' for r in backend.reviews)
    assert all(r['baseline'] == backend.reviews[0]['baseline'] for r in backend.reviews)
    for review in backend.reviews:
        bound = dict(review); claimed = bound.pop('review_input_sha256')
        assert digest(bound) == claimed

@pytest.mark.parametrize('bad', ['unknown', 'blocked', True, False])
def test_judge_unknown_and_boolean_never_accept(repo, bad):
    class Bad(Backend):
        def call(self, req, root, timeout):
            result = super().call(req, root, timeout)
            if req['phase'] == 'JUDGE': result['response']['criteria'][0]['status'] = bad
            return result
    c = Controller(repo, Bad(), Validator()); id = c.init(manifest())
    assert c.execute(id)['phase'] == 'BLOCKED'

def test_bridge_wait_status_is_separate(repo):
    c = Controller(repo, Backend(), Validator()); id = c.init(manifest(backend='bridge'))
    c.execute(id)
    assert c.status(id)['status'] == 'WAITING' and c.status(id)['phase'] == 'PLAN'

def test_branch_change_during_worker_blocks_apply(repo):
    class Switch(Backend):
        def call(self, req, root, timeout):
            result = super().call(req, root, timeout)
            if req['phase'] == 'IMPLEMENT':
                subprocess.run(['git', 'checkout', '-q', '-b', 'codex/switched'], cwd=root, check=True)
            return result
    c = Controller(repo, Switch(), Validator()); id = c.init(manifest())
    assert c.execute(id)['phase'] == 'BLOCKED'
    assert not (repo / 'docs/test.txt').exists()

def test_live_pause_resume_increments_model_budget(repo):
    import sys, threading
    from harness.dev_controller import run_process
    started = threading.Event()
    class SlowOnce(Backend):
        calls = 0
        def call(self, req, root, timeout):
            self.calls += 1
            if self.calls == 1:
                started.set()
                run_process([sys.executable, '-c', 'import time; time.sleep(10)'], root, 15, control=self.control)
                raise AssertionError('pause should interrupt')
            return super().call(req, root, timeout)
    c = Controller(repo, SlowOnce(), Validator()); id = c.init(manifest())
    results = []; thread = threading.Thread(target=lambda: results.append(c.execute(id))); thread.start()
    assert started.wait(5)
    c.control(id, 'pause'); thread.join(5)
    assert not thread.is_alive() and results[0]['paused'] and results[0]['model_calls'] == 1
    c.control(id, 'resume'); result = c.execute(id)
    assert result['phase'] == 'ACCEPTED' and result['model_calls'] == 4

@pytest.mark.parametrize('phase,paused,pending,expected', [
    ('ACCEPTED', False, None, 0), ('BLOCKED', False, None, 2),
    ('NEEDS_ATTENTION', False, None, 2), ('CANCELLED', False, None, 4),
    ('PLAN', True, None, 3), ('PLAN', False, {'nonce': 'x'}, 3),
])
def test_cli_run_exit_codes(monkeypatch, capsys, phase, paused, pending, expected):
    import runpy, sys
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/dev-controller'))
    state = {'phase': phase, 'paused': paused, 'pending': pending, 'manifest': {'backend': 'bridge'}}
    class Stub:
        def __init__(self, root): pass
        def execute(self, id): return state
        display_status = Controller.display_status
    module['main'].__globals__['Controller'] = Stub
    monkeypatch.setattr(sys, 'argv', ['dev-controller', 'run', 'task'])
    assert module['main']() == expected
    assert json.loads(capsys.readouterr().out)['phase'] == phase
