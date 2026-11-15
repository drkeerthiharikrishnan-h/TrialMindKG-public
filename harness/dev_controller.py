"""A bounded, persistent controller; model workers never write the workspace."""
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

MAX_FILE = 1024 * 1024
MAX_LOG = 128 * 1024
ROUTES = {
    'science': {'plan': ('gpt-6-astra', 'ultra'), 'implement': ('gpt-6.1-terra', 'high'), 'judge': ('gpt-6-astra', 'ultra')},
    'coding': {'plan': ('gpt-6.1-terra', 'high'), 'implement': ('gpt-6.1-terra', 'medium'), 'judge': ('gpt-6.1-terra', 'high')},
    'routine': {'plan': ('gpt-6-luna', 'low'), 'implement': ('gpt-6-luna', 'low'), 'judge': ('gpt-6-luna', 'low')},
}
PROTECTED = ('pipelines/linkpred', 'sources', 'data', 'outputs', '.claude', '.git', '.codex', '.agents', '.dev-controller', 'harness', 'config', 'bin/dev-controller', 'bin/agent-cli', 'AGENTS.md', 'docs/sessions/2026-11-02_science.md', 'docs/NEXT_STEPS.md', 'CLAUDE.md', '.gitignore', 'tests/test_dev_controller.py', 'tests/test_controller_backends.py', 'tests/test_agent_cli_controller.py')
TERMINAL = {'ACCEPTED', 'BLOCKED', 'NEEDS_ATTENTION', 'CANCELLED'}

class ControllerError(Exception): pass
class Blocked(ControllerError): pass
class Awaiting(ControllerError): pass
class ControlRequested(ControllerError):
    def __init__(self, action): self.action = action

def strict_load(s):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out: raise ValueError('duplicate JSON key: ' + k)
            out[k] = v
        return out
    def invalid(v): raise ValueError('non-finite JSON number: ' + v)
    def finite_float(v):
        number = float(v)
        if not math.isfinite(number): raise ValueError('non-finite JSON number: ' + v)
        return number
    return json.loads(s, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite_float)

def canonical(obj): return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
def digest(obj): return hashlib.sha256(canonical(obj)).hexdigest()
def file_hash(path):
    if not path.exists(): return None
    if not path.is_file() or path.is_symlink(): raise Blocked('not a regular file: ' + str(path))
    if path.stat().st_size > MAX_FILE: raise Blocked('editable file too large: ' + str(path))
    with path.open('rb') as f: return hashlib.sha256(f.read()).hexdigest()
def write_atomic(path, data, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.controller-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        if mode is not None: os.chmod(tmp, mode)
        os.replace(tmp, path)
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def keys(obj, required, optional=()):
    if not isinstance(obj, dict) or set(obj) - set(required) - set(optional) or not set(required) <= set(obj): raise ValueError('unexpected or missing keys')
def positive_int(v, name, maximum):
    if type(v) is not int or not 1 <= v <= maximum: raise ValueError('invalid ' + name)
    return v
def safe_rel(s):
    if not isinstance(s, str) or not s or '\x00' in s or '\\' in s: raise ValueError('invalid path')
    p = Path(s)
    if p.is_absolute() or any(x in ('..', '.') for x in s.split('/')) or str(p) != s: raise ValueError('path must be canonical and relative')
    return s

def validate_manifest(m):
    keys(m, ('objective', 'criteria', 'editable_paths', 'evidence_paths', 'validations'), ('classification', 'backend', 'max_reworks', 'model_timeout_seconds', 'max_model_calls', 'total_timeout_seconds', 'science_declarations'))
    if not isinstance(m['objective'], str) or not m['objective'].strip(): raise ValueError('objective required')
    if not isinstance(m['criteria'], list) or not m['criteria']: raise ValueError('criteria required')
    ids = []
    for c in m['criteria']:
        keys(c, ('id', 'text'))
        if not all(isinstance(c[k], str) and c[k].strip() for k in ('id', 'text')): raise ValueError('invalid criterion')
        ids.append(c['id'])
    if len(set(ids)) != len(ids): raise ValueError('duplicate criterion')
    for name in ('editable_paths', 'evidence_paths'):
        if not isinstance(m[name], list): raise ValueError(name + ' must be a list')
        for p in m[name]:
            if name == 'editable_paths': safe_rel(p)
            elif not isinstance(p, str) or not p or '\x00' in p: raise ValueError('invalid evidence path')
        if len({p.casefold() for p in m[name]}) != len(m[name]): raise ValueError('duplicate path')
    if not m['editable_paths']: raise ValueError('editable paths required')
    for p in m['editable_paths']:
        folded = p.casefold()
        if any(folded == x.casefold() or folded.startswith(x.casefold() + '/') for x in PROTECTED) or '/.git/' in '/' + folded + '/' or any(part in ('agents.md', 'hooks', '.env') or part.startswith('.env.') for part in folded.split('/')): raise ValueError('protected path: ' + p)
    if not isinstance(m['validations'], list) or not m['validations']: raise ValueError('validation required')
    for v in m['validations']:
        keys(v, ('argv', 'timeout_seconds'))
        if not isinstance(v['argv'], list) or not v['argv'] or any(not isinstance(a, str) or not a or '\x00' in a for a in v['argv']): raise ValueError('invalid argv')
        positive_int(v['timeout_seconds'], 'validation timeout', 3600)
    if m.get('classification', 'science') not in ROUTES or m.get('backend', 'codex') not in ('codex', 'bridge'): raise ValueError('invalid route or backend')
    if type(m.get('max_reworks', 2)) is not int or not 0 <= m.get('max_reworks', 2) <= 10: raise ValueError('invalid max_reworks')
    if m.get('classification', 'science') == 'science':
        declarations = m.get('science_declarations')
        keys(declarations, ('protocol', 'provenance', 'comparability', 'uncertainty'))
        if any(not isinstance(v, str) or v not in ids for v in declarations.values()) or len(set(declarations.values())) != 4: raise ValueError('science declarations must identify existing criteria')
    positive_int(m.get('model_timeout_seconds', 300), 'model timeout', 3600)
    positive_int(m.get('max_model_calls', 12), 'model call budget', 100)
    positive_int(m.get('total_timeout_seconds', 3600), 'total timeout', 86400)
    return m

def workspace_path(root, rel):
    safe_rel(rel)
    p = root
    for component in rel.split('/'):
        p = p / component
        if p.is_symlink(): raise Blocked('symlink in editable path: ' + rel)
    return p

def evidence(root, m):
    result = []
    for i, raw in enumerate(m['evidence_paths']):
        p = Path(raw)
        p = p if p.is_absolute() else root / p
        # Resolve to detect aliases into secrets and outside permitted roots.
        resolved = p.resolve(strict=True)
        original = Path.home() / 'projects' / 'TrialMindKG'
        if not (resolved.is_relative_to(root) if hasattr(resolved, 'is_relative_to') else str(resolved).startswith(str(root) + '/')) and not (resolved == original or original in resolved.parents): raise Blocked('evidence outside permitted roots')
        if any(part.casefold() in ('.env', '.git', '.aws', '.codex', '.agents', '.dev-controller', 'data', 'internal') or part.casefold().startswith('.env.') for part in resolved.parts): raise Blocked('secret evidence path')
        if not resolved.is_file() or resolved.stat().st_size > MAX_FILE: raise Blocked('evidence must be a small file')
        b = resolved.read_bytes()
        try: content = b.decode('utf-8')
        except UnicodeDecodeError: raise Blocked('evidence must be UTF-8')
        result.append({'id': 'evidence:' + str(i), 'path': raw, 'sha256': hashlib.sha256(b).hexdigest(), 'content': content})
    return result

def run_process(argv, cwd, timeout, input_data=None, env=None, control=None):
    # Drain both streams continuously, retaining only bounded prefixes.
    with tempfile.TemporaryFile() as source:
        if input_data is not None:
            source.write(input_data); source.seek(0)
        p = subprocess.Popen(argv, cwd=str(cwd), stdin=source if input_data is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True, shell=False)
        captured = [bytearray(), bytearray()]
        def drain(stream, target):
            while True:
                chunk = stream.read(8192)
                if not chunk: break
                target.extend(chunk[:max(0, MAX_LOG - len(target))])
        threads = [threading.Thread(target=drain, args=(stream, target), daemon=True) for stream, target in zip((p.stdout, p.stderr), captured)]
        for thread in threads: thread.start()
        timed_out = False
        deadline = time.monotonic() + timeout
        try:
            while True:
                if control: control()
                left = deadline - time.monotonic()
                if left <= 0: raise subprocess.TimeoutExpired(argv, timeout)
                try: p.wait(timeout=min(left, 0.2)); break
                except subprocess.TimeoutExpired: continue
        except BaseException as exc:
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            p.wait()
            if isinstance(exc, subprocess.TimeoutExpired): timed_out = True
            else: raise
        # A worker may exit while leaving descendants holding pipes or running.
        # The child was started in its own session, so this group is ours.
        try: os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        for thread in threads: thread.join(timeout=1)
        return {'returncode': p.returncode, 'timeout': timed_out, 'stdout': bytes(captured[0]).decode('utf-8', 'replace'), 'stderr': bytes(captured[1]).decode('utf-8', 'replace')}

class CodexBackend:
    def call(self, request, root, timeout):
        with tempfile.TemporaryDirectory(prefix='dev-controller-schema-') as tmp:
            schema = Path(tmp) / 'schema.json'; output = Path(tmp) / 'response.json'
            schema.write_bytes(canonical(request['schema']))
            model, effort = request['route']['model'], request['route']['effort']
            argv = ['codex', '--no-daemon', '-a', 'never', 'exec', '--ignore-user-config', '--ephemeral', '--sandbox', 'read-only', '-m', model, '-c', 'model_reasoning_effort="' + effort + '"', '--json', '--output-schema', str(schema), '-o', str(output), '-']
            try: transport = run_process(argv, root, timeout, request['prompt'].encode(), control=getattr(self, 'control', None))
            except OSError as exc: raise Blocked('codex unavailable: ' + str(exc))
            if transport['timeout'] or transport['returncode'] != 0 or not output.exists(): raise Blocked('codex transport failed: ' + json.dumps(transport))
            if output.stat().st_size > MAX_FILE: raise Blocked('model response too large')
            return {'response': strict_load(output.read_text()), 'transport': transport, 'observed_model': None}

class SandboxValidator:
    def run(self, spec, root):
        sandbox = Path('/usr/bin/sandbox-exec')
        if sys.platform != 'darwin' or not sandbox.exists(): raise Blocked('sandbox-exec unavailable; validation cannot run safely')
        with tempfile.TemporaryDirectory(prefix='dev-controller-validation-') as tmp:
            # tmp is created by us; quote for sandbox profile language.
            escaped = tmp.replace('\\', '\\\\').replace('"', '\\"')
            profile = '(version 1) (allow default) (deny file-write*) (deny network*) (deny signal) (allow file-write* (subpath "' + escaped + '"))'
            env = {'PATH': os.defpath, 'TMPDIR': tmp, 'TEMP': tmp, 'TMP': tmp, 'PYTHONDONTWRITEBYTECODE': '1', 'HOME': tmp}
            try:
                result = run_process([str(sandbox), '-p', profile] + spec['argv'], root, spec['timeout_seconds'], env=env, control=getattr(self, 'control', None))
                if 'sandbox_apply' in result['stderr'] or 'sandbox-exec:' in result['stderr']: raise Blocked('validation sandbox could not start: ' + result['stderr'])
                return result
            except OSError as exc: raise Blocked('validation sandbox unavailable: ' + str(exc))

# Schema supplied to the model is also checked locally, including all exact keys.
def schema_for(phase):
    string = {'type': 'string'}
    if phase == 'PLAN': props = {'status': {'type': 'string', 'enum': ['ready', 'blocked']}, 'reason': string, 'plan': string}; required = list(props)
    elif phase == 'IMPLEMENT':
        props = {'status': {'type': 'string', 'enum': ['done', 'blocked', 'needs_science']}, 'reason': string, 'edits': {'type': 'array', 'items': {'type': 'object', 'properties': {'path': string, 'content': string}, 'required': ['path', 'content'], 'additionalProperties': False}}}; required = list(props)
    else:
        criterion = {'type': 'object', 'properties': {'id': string, 'status': {'type': 'string', 'enum': ['pass', 'fail', 'unknown', 'blocked']}, 'evidence_ids': {'type': 'array', 'items': string}, 'reason': string}, 'required': ['id', 'status', 'evidence_ids', 'reason'], 'additionalProperties': False}
        props = {'run_id': string, 'attempt_id': string, 'config_sha256': string, 'review_input_sha256': string, 'verdict': {'type': 'string', 'enum': ['accept', 'rework', 'blocked']}, 'criteria': {'type': 'array', 'items': criterion}, 'findings': {'type': 'array', 'items': string}, 'limitations': {'type': 'array', 'items': string}}
        required = list(props)
    return {'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False}

def check_response(phase, r):
    schema = schema_for(phase); keys(r, schema['required'])
    if phase == 'PLAN':
        if r['status'] not in ('ready', 'blocked') or not isinstance(r['reason'], str) or not isinstance(r['plan'], str) or (r['status'] == 'ready' and not r['plan'].strip()): raise ValueError('invalid plan')
    elif phase == 'IMPLEMENT':
        if r['status'] not in ('done', 'blocked', 'needs_science') or not isinstance(r['reason'], str) or not isinstance(r['edits'], list) or (r['status'] == 'done' and not r['edits']): raise ValueError('invalid implementation result')
        for e in r['edits']:
            keys(e, ('path', 'content'))
            if not isinstance(e['path'], str) or not isinstance(e['content'], str): raise ValueError('invalid edit')
    else:
        for k in ('run_id', 'attempt_id', 'config_sha256', 'review_input_sha256', 'verdict'):
            if not isinstance(r[k], str): raise ValueError('invalid judge field')
        if r['verdict'] not in ('accept', 'rework', 'blocked') or not isinstance(r['criteria'], list): raise ValueError('invalid judge result')
        for c in r['criteria']:
            keys(c, ('id', 'status', 'evidence_ids', 'reason'))
            if not isinstance(c['id'], str) or c['status'] not in ('pass', 'fail', 'unknown', 'blocked') or not isinstance(c['reason'], str) or not isinstance(c['evidence_ids'], list) or any(not isinstance(x, str) for x in c['evidence_ids']): raise ValueError('invalid criterion result')
        for k in ('findings', 'limitations'):
            if not isinstance(r[k], list) or any(not isinstance(x, str) for x in r[k]): raise ValueError('invalid judge field')

def is_original_workspace(root):
    original = Path.home() / 'projects' / 'TrialMindKG'
    for path in (root, *root.parents):
        if str(path).casefold() == str(original).casefold(): return True
        try:
            if path.samefile(original): return True
        except OSError: pass
    return False

class Controller:
    def __init__(self, root, backend=None, validator=None):
        self.root = Path(root).resolve(); self.store = self.root / '.dev-controller'; self.backend = backend or CodexBackend(); self.validator = validator or SandboxValidator()
    def check_store(self):
        if is_original_workspace(self.root): raise Blocked('original workspace is read-only')
        if self.store.is_symlink() or (self.store / 'lock').is_symlink(): raise Blocked('state directory or lock is a symlink')
    @contextlib.contextmanager
    def lock(self):
        self.check_store()
        self.store.mkdir(exist_ok=True)
        with (self.store / 'lock').open('a+') as f:
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Blocked('another controller holds the repository lock')
            try: yield
            finally: fcntl.flock(f, fcntl.LOCK_UN)
    def check_repo(self):
        if is_original_workspace(self.root): raise Blocked('original workspace is read-only')
        try: branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=str(self.root), text=True, timeout=5).strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired): raise Blocked('not a git workspace')
        if not branch.startswith('codex/'): raise Blocked('requires a codex/* branch')
    def taskdir(self, id):
        if not isinstance(id, str) or len(id) != 32 or any(c not in '0123456789abcdef' for c in id): raise ValueError('invalid task id')
        path = self.store / id
        self.check_store()
        if path.is_symlink() or (path / 'artifacts').is_symlink() or (path / 'state.json').is_symlink(): raise Blocked('state path is a symlink')
        return path
    def save(self, s): write_atomic(self.taskdir(s['id']) / 'state.json', canonical(s))
    def event(self, s, kind, detail=None):
        s['events'].append({'kind': kind, 'time': time.time(), 'detail': detail}); self.save(s)
    def load(self, id):
        s = strict_load((self.taskdir(id) / 'state.json').read_text())
        if digest(s['manifest']) != s['config_sha256']: raise Blocked('manifest tampered')
        return s
    def git_identity(self):
        def git(*args):
            r = subprocess.run(['git'] + list(args), cwd=str(self.root), text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None
        return {'branch': git('branch', '--show-current'), 'head': git('rev-parse', 'HEAD'), 'dirty': [x for x in (git('status', '--porcelain', '--untracked-files=all') or '').splitlines() if '.dev-controller/' not in x]}
    def verify_identity(self, s):
        identity = self.git_identity()
        if identity['branch'] != s['git']['branch'] or identity['head'] != s['git']['head']: raise Blocked('git branch or HEAD changed since initialization')
    def init(self, manifest):
        validate_manifest(manifest)
        with self.lock():
            self.check_repo()
            ev = evidence(self.root, manifest)
            for p in manifest['editable_paths']: workspace_path(self.root, p)
            id = uuid.uuid4().hex
            s = {'id': id, 'manifest': manifest, 'config_sha256': digest(manifest), 'evidence': ev, 'evidence_sha256': digest(ev), 'phase': 'PLAN', 'attempt_count': 0, 'model_calls': 0, 'created_at': time.time(), 'git': self.git_identity(), 'attempts': [], 'events': [], 'pending': None, 'paused': False}
            self.event(s, 'initialized'); return id
    def display_status(self, s):
        phase = s['phase']
        status = ('PAUSED' if s.get('paused') and phase not in TERMINAL else
                  'WAITING' if s.get('pending') and s['manifest'].get('backend', 'codex') == 'bridge' and 'submitted' not in s['pending'] and phase not in TERMINAL else phase)
        return dict(s, status=status)
    def status(self, id=None):
        if id: return self.display_status(self.load(id))
        if not self.store.exists(): return []
        return [self.display_status(self.load(p.name)) for p in sorted(self.store.iterdir()) if p.is_dir() and len(p.name) == 32 and (p / 'state.json').exists()]
    def snapshots(self, s): return {p: file_hash(workspace_path(self.root, p)) for p in s['manifest']['editable_paths']}
    def verify_evidence(self, s):
        if digest(s['evidence']) != s['evidence_sha256'] or evidence(self.root, s['manifest']) != s['evidence']: raise Blocked('evidence changed or tampered')
    def artifact(self, s, name, obj):
        p = self.taskdir(s['id']) / 'artifacts' / name
        if p.exists(): raise Blocked('artifact already exists')
        write_atomic(p, canonical(obj))
    def new_attempt(self, s):
        s['attempt_count'] += 1
        baseline = self.snapshots(s)
        a = {'id': uuid.uuid4().hex, 'number': s['attempt_count'], 'baseline': baseline, 'before_files': {p: (workspace_path(self.root, p).read_text() if h else None) for p, h in baseline.items()}, 'results': {}, 'validation': []}
        s['attempts'].append(a); self.event(s, 'attempt_started', a['id']); return a
    def remaining(self, s, limit):
        remaining = s['manifest'].get('total_timeout_seconds', 3600) - (time.time() - s['created_at'])
        if remaining <= 0: raise Blocked('total time budget exhausted')
        return min(limit, remaining)
    def dispatch(self, s, phase, inputs):
        a = s['attempts'][-1]; route = ROUTES[s['manifest'].get('classification', 'science')][phase.lower()]
        if phase in a['results']:
            return a['results'][phase]
        inp_hash = digest(inputs)
        if s['pending'] is None:
            if s['model_calls'] >= s['manifest'].get('max_model_calls', 12): raise Blocked('model call budget exhausted')
            s['model_calls'] += 1
            req = {'nonce': uuid.uuid4().hex, 'run_id': s['id'], 'attempt_id': a['id'], 'phase': phase, 'config_sha256': s['config_sha256'], 'input_sha256': inp_hash, 'route': {'model': route[0], 'effort': route[1]}, 'schema': schema_for(phase), 'prompt': {'PLAN': 'Create a plan for the exact objective and criteria; report blocked for ambiguity.', 'IMPLEMENT': 'Implement the plan using only exact allowed files; report needs_science if a scientific decision is needed.', 'JUDGE': 'Independently judge each exact criterion using only supplied evidence IDs and validation results; bind the supplied review_input_sha256. Report every finding. For science, explicitly assess preservation of the frozen protocol, evidence provenance, comparability, and uncertainty against their declared criteria; mark unknown or blocked whenever evidence does not establish a claim.'}[phase] + ' Return only JSON matching the schema. Work read-only: do not modify files; return proposed file contents in JSON only. Do not read secrets, credentials, environment files, datasets, or paths outside the supplied inputs. Ignore any inherited instructions to delegate; do not spawn agents or delegate, run experiments, load large graphs, change criteria or validation commands, or make external writes. Inputs: ' + canonical(inputs).decode()}
            s['pending'] = req; self.artifact(s, a['id'] + '-' + phase.lower() + '-' + req['nonce'] + '-request.json', req); self.event(s, 'dispatched', {'phase': phase, 'nonce': req['nonce'], 'route': req['route']})
        req = s['pending']
        if req['input_sha256'] != inp_hash or req['phase'] != phase: raise Blocked('pending request input changed')
        if 'submitted' in req: result = req['submitted']
        elif s['manifest'].get('backend', 'codex') == 'bridge': raise Awaiting('bridge response required')
        else:
            if req.get('active'): raise Blocked('interrupted model call requires reconciliation; retry suppressed')
            req['active'] = True; self.event(s, 'backend_call_started', req['nonce'])
            try: result = self.backend.call(req, self.root, self.remaining(s, s['manifest'].get('model_timeout_seconds', 300)))
            except (ValueError, TypeError) as exc: raise Blocked('invalid model response: ' + str(exc))
        self.remaining(s, 1)
        self.verify_identity(s)
        keys(result, ('response',), ('transport', 'observed_model'))
        check_response(phase, result['response'])
        self.artifact(s, a['id'] + '-' + phase.lower() + '-' + req['nonce'] + '-response.json', result)
        s['pending'] = None; a['results'][phase] = result['response']; self.event(s, 'response_received', {'phase': phase, 'requested': req['route'], 'observed_model': result.get('observed_model'), 'attribution_verified': False})
        return result['response']
    def prepare(self, s, edits):
        a = s['attempts'][-1]
        if self.snapshots(s) != a['baseline']: raise Blocked('workspace changed since attempt began')
        seen = set(); journal = []
        for edit in edits:
            p = edit['path']; content = edit['content']
            if p not in s['manifest']['editable_paths'] or p in seen: raise Blocked('unauthorized or duplicate edit')
            seen.add(p); target = workspace_path(self.root, p)
            b = content.encode('utf-8')
            if len(b) > MAX_FILE or b'\x00' in b: raise Blocked('invalid edit content')
            journal.append({'path': p, 'before': a['baseline'][p], 'after': hashlib.sha256(b).hexdigest(), 'content': content})
        a['journal'] = journal; s['phase'] = 'APPLY'; self.event(s, 'apply_prepared', {'paths': sorted(seen)})
    def apply(self, s):
        self.verify_identity(s)
        a = s['attempts'][-1]
        # Preflight all files before writing any, including files not proposed for edit.
        by_path = {j['path']: j for j in a['journal']}
        for p, old in a['baseline'].items():
            cur = file_hash(workspace_path(self.root, p)); desired = by_path[p]['after'] if p in by_path else old
            if cur != old and cur != desired: raise Blocked('external mutation during apply: ' + p)
        for j in a['journal']:
            if hashlib.sha256(j['content'].encode()).hexdigest() != j['after']: raise Blocked('apply journal tampered')
        for j in a['journal']:
            target = workspace_path(self.root, j['path'])
            if file_hash(target) != j['after']:
                mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
                write_atomic(target, j['content'].encode(), mode)
        a['applied'] = self.snapshots(s); s['phase'] = 'VALIDATE'; self.event(s, 'applied', a['applied'])
    def rework(self, s, why):
        s['pending'] = None
        if s['attempt_count'] > s['manifest'].get('max_reworks', 2): s['phase'] = 'NEEDS_ATTENTION'
        else: s['phase'] = 'IMPLEMENT'
        self.event(s, 'rework', why)
    def poll_control(self, id):
        p = self.taskdir(id) / 'control.json'
        if p.is_symlink(): raise Blocked('control path is a symlink')
        if p.exists():
            command = strict_load(p.read_text())['action']
            p.unlink()
            if command in ('pause', 'cancel'): raise ControlRequested(command)
    def execute(self, id):
        with self.lock():
            self.check_repo(); s = self.load(id)
            self.verify_identity(s)
            if s['paused']: return s
            self.backend.control = lambda: self.poll_control(id)
            self.validator.control = lambda: self.poll_control(id)
            try:
                while s['phase'] not in TERMINAL:
                    self.poll_control(id)
                    self.verify_identity(s)
                    if time.time() - s['created_at'] > s['manifest'].get('total_timeout_seconds', 3600): raise Blocked('total time budget exhausted')
                    self.verify_evidence(s)
                    if s['phase'] == 'PLAN':
                        if not s['attempts']: self.new_attempt(s)
                        plan = self.dispatch(s, 'PLAN', {'manifest': s['manifest'], 'evidence': s['evidence']})
                        if plan['status'] == 'blocked': raise Blocked('plan blocked: ' + plan['reason'])
                        s['phase'] = 'IMPLEMENT'; self.event(s, 'planned')
                    elif s['phase'] == 'IMPLEMENT':
                        if s['attempts'][-1].get('finished'): self.new_attempt(s)
                        a = s['attempts'][-1]
                        inputs = {'manifest': s['manifest'], 'evidence': s['evidence'], 'plan': s['attempts'][0]['results']['PLAN'], 'baseline': a['baseline'], 'files': {p: (workspace_path(self.root, p).read_text() if a['baseline'][p] else None) for p in a['baseline']}, 'previous': s['attempts'][-2] if len(s['attempts']) > 1 else None}
                        r = self.dispatch(s, 'IMPLEMENT', inputs)
                        if r['status'] != 'done': raise Blocked('implementation ' + r['status'] + ': ' + r['reason'])
                        self.prepare(s, r['edits'])
                    elif s['phase'] == 'APPLY': self.apply(s)
                    elif s['phase'] == 'VALIDATE':
                        a = s['attempts'][-1]
                        if self.snapshots(s) != a['applied']: raise Blocked('files changed before validation')
                        for i, spec in enumerate(s['manifest']['validations']):
                            if i < len(a['validation']):
                                if a['validation'][i]['returncode'] != 0 or a['validation'][i]['timeout']: break
                                continue
                            bounded_spec = dict(spec, timeout_seconds=self.remaining(s, spec['timeout_seconds']))
                            result = self.validator.run(bounded_spec, self.root); result['id'] = 'validation:' + str(i); a['validation'].append(result); self.event(s, 'validation', result)
                            if result['returncode'] != 0 or result['timeout']: break
                        self.remaining(s, 1)
                        if self.snapshots(s) != a['applied']: raise Blocked('files changed during validation')
                        if len(a['validation']) != len(s['manifest']['validations']) or any(v['returncode'] != 0 or v['timeout'] for v in a['validation']):
                            a['finished'] = True; self.rework(s, 'validation failed')
                        else: s['phase'] = 'JUDGE'; self.event(s, 'validated')
                    elif s['phase'] == 'JUDGE':
                        a = s['attempts'][-1]
                        if self.snapshots(s) != a['applied']: raise Blocked('files changed before judge')
                        review = {'run_id': s['id'], 'attempt_id': a['id'], 'config_sha256': s['config_sha256'], 'manifest': s['manifest'], 'evidence': s['evidence'], 'plan': s['attempts'][0]['results']['PLAN'], 'before_files': s['attempts'][0]['before_files'], 'baseline': s['attempts'][0]['baseline'], 'files': {p: {'sha256': h, 'content': workspace_path(self.root, p).read_text() if h else None} for p, h in a['applied'].items()}, 'validation': a['validation']}
                        review['available_evidence_ids'] = sorted({e['id'] for e in s['evidence']} | {v['id'] for v in a['validation']} | {'file:' + p + ':' + h for p, h in a['applied'].items() if h})
                        review_hash = digest(review); review['review_input_sha256'] = review_hash
                        r = self.dispatch(s, 'JUDGE', review)
                        if any(r[k] != v for k, v in [('run_id', s['id']), ('attempt_id', a['id']), ('config_sha256', s['config_sha256']), ('review_input_sha256', review_hash)]): raise Blocked('judge binding mismatch')
                        if self.snapshots(s) != a['applied']: raise Blocked('files changed during judge')
                        self.verify_evidence(s)
                        self.verify_identity(s)
                        expected = {c['id'] for c in s['manifest']['criteria']}; ids = [c['id'] for c in r['criteria']]; known = set(review['available_evidence_ids'])
                        if len(ids) != len(set(ids)) or set(ids) != expected or any(not set(c['evidence_ids']) <= known for c in r['criteria']): raise Blocked('invalid judge criteria or evidence')
                        a['finished'] = True
                        if r['verdict'] == 'blocked' or any(c['status'] in ('unknown', 'blocked') for c in r['criteria']): s['phase'] = 'BLOCKED'; self.event(s, 'judge_blocked', r)
                        elif r['verdict'] == 'accept' and not r['findings'] and all(c['status'] == 'pass' and c['evidence_ids'] for c in r['criteria']): s['phase'] = 'ACCEPTED'; self.event(s, 'accepted')
                        else: self.rework(s, 'judge requested rework or did not establish all criteria')
                    else: raise Blocked('unknown phase')
            except ControlRequested as exc:
                if s['pending'] and s['pending'].get('active'): s['pending'] = None
                if exc.action == 'pause': s['paused'] = True
                else: s['phase'] = 'CANCELLED'; s['pending'] = None
                self.event(s, exc.action)
            except Awaiting: pass
            except (Blocked, ValueError, OSError, UnicodeError, TypeError) as exc:
                s['phase'] = 'BLOCKED'; self.event(s, 'blocked', str(exc))
            return s
    def request(self, id):
        s = self.load(id)
        if not s['pending'] or s['manifest'].get('backend', 'codex') != 'bridge' or 'submitted' in s['pending']: raise ControllerError('no outstanding bridge request')
        return s['pending']
    def submit(self, id, envelope):
        with self.lock():
            s = self.load(id); req = s['pending']
            keys(envelope, ('nonce', 'run_id', 'attempt_id', 'config_sha256', 'input_sha256', 'response'), ('observed_model',))
            if not req or 'submitted' in req or s['manifest'].get('backend', 'codex') != 'bridge' or any(envelope[k] != req[k] for k in ('nonce', 'run_id', 'attempt_id', 'config_sha256', 'input_sha256')): raise ControllerError('stale or mismatched bridge response')
            check_response(req['phase'], envelope['response'])
            req['submitted'] = {'response': envelope['response'], 'observed_model': envelope.get('observed_model')}; self.event(s, 'bridge_submitted', req['nonce']); return s
    def control(self, id, action):
        if action in ('pause', 'cancel'):
            # A separate atomic signal reaches a running process while it holds the repo lock.
            self.check_store(); directory = self.taskdir(id)
            if not (directory / 'state.json').is_file(): raise ControllerError('task does not exist')
            write_atomic(directory / 'control.json', canonical({'action': action}))
            try:
                with self.lock():
                    s = self.load(id)
                    try: self.poll_control(id)
                    except ControlRequested as exc:
                        if exc.action == 'pause': s['paused'] = True
                        else: s['phase'] = 'CANCELLED'; s['pending'] = None
                        self.event(s, exc.action)
                    return s
            except Blocked as exc:
                if str(exc) != 'another controller holds the repository lock': raise
                return {'id': id, 'control_requested': action}
            return s
        with self.lock():
            s = self.load(id)
            p = self.taskdir(id) / 'control.json'
            if p.exists(): p.unlink()
            s['paused'] = False; self.event(s, action); return s
