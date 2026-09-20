"""Original ICAIF participant automation. Business rules remain in the original kit.

Only the native Codabench submission API creates uploads. Backend queries are GET.
An uncertain creation is recovered by original ID and raw-file SHA256, never retried.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import zipfile

import httpx

from kit.config import load_config, load_symbols
from kit.contracts import MAX_BYTES, SubmissionError, dumps_json, timestamp, validate_payload

MAX_RESPONSE = 16 * 1024 * 1024
PHASES = ('registration', 'validation', 'official', 'final_submission')
RETRY_STATUSES = {429, 502, 503, 504}


class AutomationError(ValueError):
    """Safe error text: never includes request bodies, signed URLs, or credentials."""


class AmbiguousSubmission(AutomationError):
    """A create request may have committed; recovery must precede another upload."""


def strict_json(raw):
    def pairs(entries):
        out = {}
        for key, value in entries:
            if key in out:
                raise ValueError()
            out[key] = value
        return out
    def constant(_):
        raise ValueError()
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise AutomationError('Invalid UTF-8 JSON object.') from None


def _url(value, *, storage=False):
    try:
        p = urlsplit(value)
        p.port
        if (p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment
                or (not storage and p.query) or any(c.isspace() for c in value)):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AutomationError('Use a valid HTTPS URL without embedded credentials.') from None
    return value.rstrip('/') if not storage else value


def _number(value, label, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise AutomationError(label + ' must be a finite positive number.')
    return value


def _secret(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise AutomationError(label + ' is missing or malformed.')
    return value


def _id(value):
    if type(value) is not int or value <= 0:
        raise AutomationError('Expected a positive submission or phase ID.')
    return value


@dataclass(frozen=True)
class Profile:
    base_url: str
    competition_id: int
    phases: dict
    backend_base_url: str | None = None
    tasks: list | None = None
    label: str = ''
    test_mode: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'base_url', _url(self.base_url))
        _id(self.competition_id)
        if not isinstance(self.label, str) or len(self.label) > 500 or type(self.test_mode) is not bool:
            raise AutomationError('Profile label and test_mode must be explicit text and a boolean.')
        if isinstance(self.phases, dict) and set(self.phases) == {'unified'}:
            object.__setattr__(self, 'phases', {kind:self.phases['unified'] for kind in PHASES})
        if not isinstance(self.phases, dict) or set(self.phases) != set(PHASES):
            raise AutomationError('The profile must map all four original competition phases.')
        for value in self.phases.values():
            _id(value)
        if len(set(self.phases.values())) not in (1, 4):
            raise AutomationError('Use one shared native phase ID or four distinct native phase IDs.')
        object.__setattr__(self, 'phases', dict(self.phases))
        if self.tasks is not None:
            if not isinstance(self.tasks, list) or len(self.tasks) != 1:
                raise AutomationError('Configure exactly one native trading task, or omit tasks.')
            _id(self.tasks[0])
        object.__setattr__(self, 'backend_base_url', _url(self.backend_base_url or
            f'{self.base_url}/extensions/icaif2026/{self.competition_id}/backend'))

    @property
    def gateway_base(self):
        return f'{self.base_url}/extensions/icaif2026/{self.competition_id}'

    def as_dict(self):
        return {'base_url': self.base_url, 'competition_id': self.competition_id,
                'phases': self.phases, 'backend_base_url': self.backend_base_url, 'tasks': self.tasks,
                'label': self.label, 'test_mode': self.test_mode}


def load_profile(path):
    try:
        value = strict_json(Path(path).read_bytes())
        return Profile(**value)
    except (OSError, TypeError):
        raise AutomationError('Provide the organizer profile containing the actual competition and native phase mapping.') from None


def _safe_file(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise AutomationError('Private state paths must be regular files, never symlinks.')


def write_private_json(path, value):
    """Atomic replacement, mode 0600, synchronized file and directory on POSIX."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_file(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.chmod(name, 0o600)
            stream.write(dumps_json(value).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if hasattr(os, 'O_DIRECTORY'):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_private(path):
    _safe_file(path)
    if path.stat().st_size > 32 * MAX_RESPONSE:
        raise AutomationError('Private state file exceeds the supported size.')
    os.chmod(path, 0o600)
    value = strict_json(path.read_bytes())
    if not isinstance(value, dict):
        raise AutomationError('Private state file must contain an object.')
    return value


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {key: redact(child, secrets) for key, child in value.items()
                if not any(word in key.lower() for word in ('token', 'secret', 'authorization', 'api_key'))}
    if isinstance(value, list):
        return [redact(child, secrets) for child in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
    return value


class OriginalSession:
    def __init__(self, *, profile, token, checkpoint, credentials, transport=None,
                 storage_transport=None, timeout=30, result_timeout=180, poll_interval=2,
                 retry_delay=.5, clock=None):
        if not isinstance(profile, Profile):
            raise AutomationError('Load an organizer profile before creating the session.')
        self.profile, self.token = profile, _secret(token, 'CODABENCH_TOKEN')
        self.result_timeout = _number(result_timeout, 'result_timeout')
        self.poll_interval = _number(poll_interval, 'poll_interval', zero=True)
        self.retry_delay = _number(retry_delay, 'retry_delay', zero=True)
        self.checkpoint_path, self.credentials_path = Path(checkpoint), Path(credentials)
        if self.checkpoint_path.resolve() == self.credentials_path.resolve():
            raise AutomationError('Credentials and checkpoint must use separate files.')
        self.scope = {'profile_sha256': hashlib.sha256(dumps_json(profile.as_dict()).encode()).hexdigest(),
                      'account_sha256': hashlib.sha256(token.encode()).hexdigest()}
        self.http = httpx.Client(timeout=_number(timeout, 'timeout'), follow_redirects=False, transport=transport)
        self.storage = httpx.Client(timeout=timeout, follow_redirects=False, transport=storage_transport or transport)
        self.clock = clock
        self.username = None
        self.creds = None
        self.lock = None
        try:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock_path = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + '.lock')
            _safe_file(lock_path)
            self.lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            os.fchmod(self.lock, 0o600)
            self._lock()
            self.state = _read_private(self.checkpoint_path) if self.checkpoint_path.exists() else {'scope': self.scope, 'operations': {}}
            if self.state.get('scope') != self.scope:
                raise AutomationError('Checkpoint belongs to a different account or competition profile.')
            if not isinstance(self.state.get('operations'), dict):
                raise AutomationError('Checkpoint operations are malformed.')
            if self.credentials_path.exists():
                self.creds = _read_private(self.credentials_path)
                if self.creds.get('scope') != self.scope:
                    raise AutomationError('Team credentials belong to a different account or competition profile.')
                _secret(self.creds.get('team_id'), 'TEAM_ID')
                _secret(self.creds.get('team_token'), 'TEAM_TOKEN')
            self._save()
        except BaseException:
            self.close()
            raise

    def _lock(self):
        try:
            if os.name == 'nt':
                import msvcrt
                os.write(self.lock, b'0')
                os.lseek(self.lock, 0, 0)
                msvcrt.locking(self.lock, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AutomationError('Another session is using this checkpoint; stop it before resuming.') from None

    def close(self):
        self.http.close()
        self.storage.close()
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _save(self):
        write_private_json(self.checkpoint_path, self.state)

    def save_credentials(self, team_id, team_token):
        team_id, team_token = _secret(team_id, 'TEAM_ID'), _secret(team_token, 'TEAM_TOKEN')
        if self.credentials_path.exists():
            previous = _read_private(self.credentials_path)
            if previous.get('scope') != self.scope or previous.get('team_id') != team_id:
                raise AutomationError('Refusing to overwrite credentials for a different account, profile or team.')
        if self.creds and self.creds['team_id'] != team_id:
            raise AutomationError('Refusing to overwrite credentials for a different team.')
        value = {'scope': self.scope, 'team_id': team_id, 'team_token': team_token}
        write_private_json(self.credentials_path, value)
        self.creds = value

    def _request(self, method, url, *, storage=False, **kwargs):
        _url(url, storage=storage)
        safe = method == 'GET' or method == 'PUT'
        attempts = 3 if safe else 1
        client = self.storage if storage else self.http
        if not storage:
            headers = dict(kwargs.pop('headers', {}))
            headers.setdefault('Authorization', 'Token ' + self.token)
            kwargs['headers'] = headers
        for attempt in range(attempts):
            try:
                with client.stream(method, url, **kwargs) as response:
                    if response.status_code in RETRY_STATUSES and attempt + 1 < attempts:
                        pass
                    elif not 200 <= response.status_code < 300:
                        raise AutomationError(f'HTTP {response.status_code}; inspect the existing submission or organizer status.')
                    else:
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_RESPONSE:
                                raise AutomationError('Response exceeds the supported size.')
                        return bytes(body) if storage else (strict_json(body) if body else {})
            except httpx.TransportError:
                if attempt + 1 == attempts:
                    raise AutomationError('Network request failed; resume this checkpoint or submission ID.') from None
            if attempt + 1 < attempts:
                time.sleep(self.retry_delay * 2 ** attempt)
        raise AutomationError('Service unavailable after bounded safe retries.')

    def _api(self, method, path, **kwargs):
        return self._request(method, self.profile.base_url + path, **kwargs)

    def _identity(self):
        if self.username is None:
            who = self._api('GET', '/api/my_profile/')
            name = who.get('username') if isinstance(who, dict) else None
            if not isinstance(name, str) or not name:
                raise AutomationError('Platform did not return the authenticated username.')
            self.username = name
        return self.username

    def _preflight(self):
        self._identity()
        value = self._api('GET', f'/api/competitions/{self.profile.competition_id}/')
        if value.get('id') != self.profile.competition_id or value.get('participant_status') != 'approved':
            raise AutomationError('An approved participant in the configured competition is required.')
        ids = {v.get('id') for v in value.get('phases', []) if isinstance(v, dict)}
        if not set(self.profile.phases.values()).issubset(ids):
            raise AutomationError('The configured phase IDs do not belong to this competition.')

    def _team(self):
        if not self.creds:
            raise AutomationError('Register once or import the organizer-issued TEAM_ID and TEAM_TOKEN credentials.')
        return self.creds

    def _backend(self, path, *, private=True, **query):
        headers = {}
        base = self.profile.backend_base_url
        if private:
            token = self._team()['team_token']
            if base == self.profile.base_url + f'/extensions/icaif2026/{self.profile.competition_id}/backend':
                headers['X-ICAIF-Team-Token'] = token
            else:
                headers['Authorization'] = 'Bearer ' + token
        elif base != self.profile.base_url + f'/extensions/icaif2026/{self.profile.competition_id}/backend':
            headers['Authorization'] = ''
        return self._request('GET', base + path, headers=headers, params=query or None)

    def schedule(self):
        value = self._backend('/api/v1/schedule', private=False)
        if not isinstance(value, dict) or set(value.get('symbols', [])) != set(load_symbols()) or value.get('universe_confirmed') is not True:
            raise AutomationError('Organizer schedule must confirm the original 30-stock universe.')
        rows = value.get('rounds')
        if not isinstance(rows, list) or not rows:
            raise AutomationError('Organizer schedule contains no rounds.')
        ids = set()
        for row in rows:
            try:
                if row['phase'] not in ('validation', 'official') or row['id'] in ids:
                    raise ValueError()
                start, end, execution, close = [timestamp(row[k]) for k in ('opens_at', 'deadline', 'execution_time', 'close_time')]
                if not start < end <= execution <= close:
                    raise ValueError()
                ids.add(row['id'])
            except (KeyError, TypeError, ValueError):
                raise AutomationError('Organizer schedule contains an invalid round or timestamp.') from None
        return value

    @staticmethod
    def _phase(phase):
        if phase not in ('validation', 'official'):
            raise AutomationError('phase must be validation or official.')
        return phase

    def portfolio(self, phase='official'):
        return self._backend('/api/v1/me/portfolio', phase=self._phase(phase))

    def decisions(self, phase='official', *, offset=0, limit=200):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise AutomationError('offset must be nonnegative and limit must be between 1 and 200.')
        return self._backend('/api/v1/me/decisions', phase=self._phase(phase), offset=offset, limit=limit)

    def round(self, round_id):
        if not isinstance(round_id, str) or not re.fullmatch(r'(validation|official)-\d{4}-\d{2}-\d{2}-r[1-7]', round_id):
            raise AutomationError('Use an original validation or official round ID.')
        return self._backend('/api/v1/me/rounds/' + round_id)

    def metrics(self, phase='validation'):
        return self._backend('/api/v1/me/metrics', phase=self._phase(phase))

    def ledger(self, phase='official'):
        return self._backend('/api/v1/me/ledger', phase=self._phase(phase))

    @staticmethod
    def _occupied(own_round):
        # The original sealed backend explicitly excludes out-of-window receipts.
        return any(item.get('selection_status') != 'NOT_ELIGIBLE'
                   for item in own_round.get('decisions', []))

    def _now(self, schedule):
        # The server clock is authoritative, including explicitly isolated test clocks.
        return timestamp(schedule['current_time']) if schedule.get('current_time') else datetime.now(timezone.utc)

    def _read_submission(self, path, kind):
        path = Path(path)
        expected = {'register': 'register.json', 'decision': 'decision.json', 'final_submission': 'final_submission.json'}[kind]
        if path.name != expected:
            raise AutomationError('Submission filename must be exactly ' + expected + '.')
        try:
            with path.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
        except OSError:
            raise AutomationError('Could not read the requested submission file.') from None
        if len(raw) > MAX_BYTES:
            raise AutomationError('Submission exceeds the original 120000-byte local limit.')
        payload = strict_json(raw)
        if not isinstance(payload, dict) or payload.get('submission_type') != kind:
            raise AutomationError('The original filename and submission_type must match.')
        return raw, payload

    def _validated(self, raw, payload, filename, schedule=None):
        if len(raw) > MAX_BYTES:
            raise AutomationError('Submission exceeds the original 120000-byte local limit.')
        try:
            options = {}
            if schedule:
                schedule_path = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + '.schedule.json')
                write_private_json(schedule_path, schedule)
                options['schedule_path'] = schedule_path
            validate_payload(payload, filename, **options)
        except SubmissionError as error:
            raise AutomationError(str(error)) from None
        if payload['submission_type'] != 'register':
            creds = self._team()
            if payload.get('team_id') != creds['team_id'] or payload.get('team_token') != creds['team_token']:
                raise AutomationError('Submission TEAM_ID and TEAM_TOKEN must match this session credentials.')

    def register(self, path):
        raw, payload = self._read_submission(path, 'register')
        self._validated(raw, payload, 'register.json')
        if 'registration' not in self.state['operations'] and self.creds:
            raise AutomationError('Team credentials already exist. Fetch the original registration; do not register again.')
        return self._submit('registration', raw, 'register.json', 'registration')

    def decision(self, path):
        raw, payload = self._read_submission(path, 'decision')
        return self._decision(raw, payload)

    def _decision(self, raw, payload):
        key = 'decision:' + str(payload.get('phase')) + ':' + str(payload.get('round_id'))
        schedule = self.schedule()
        self._validated(raw, payload, 'decision.json', schedule)
        if key in self.state['operations']:
            return self._submit(key, raw, 'decision.json', payload['phase'])
        row = next(r for r in schedule['rounds'] if r['id'] == payload['round_id'])
        now = self._now(schedule)
        if not timestamp(row['opens_at']) <= now < timestamp(row['deadline']):
            raise AutomationError('Decision window is closed. Deadline is exclusive; no upload was created.')
        own_round = self.round(payload['round_id'])
        # Any attributable first attempt consumes the original slot, including INVALID.
        if self._occupied(own_round):
            return {'status': 'SLOT_CONSUMED', 'round_id': payload['round_id'], 'decisions': own_round['decisions']}
        return self._submit(key, raw, 'decision.json', payload['phase'], deadline=row['deadline'])

    def final(self, path):
        raw, payload = self._read_submission(path, 'final_submission')
        self._validated(raw, payload, 'final_submission.json')
        host = urlsplit(payload['materials_url']).hostname.lower()
        if any(word in host.split('.') for word in ('replace', 'demo', 'example')) or host.endswith(('.example', '.invalid', '.test')):
            raise AutomationError('Final materials_url must be your real shared HTTPS materials location.')
        if 'final_submission' not in self.state['operations']:
            schedule = self.schedule()
            now = self._now(schedule)
            closes = [timestamp(r['close_time']) for r in schedule['rounds'] if r['phase'] == 'official']
            deadline = schedule.get('final_deadline') or schedule.get('evaluation_policy', {}).get('final_deadline') or load_config()['final_deadline']
            if not closes or not max(closes) <= now <= timestamp(deadline):
                raise AutomationError('Final window is closed. Submit real materials after the last official close, through the inclusive Final deadline.')
        return self._submit('final_submission', raw, 'final_submission.json', 'final_submission')

    def _submit(self, key, raw, filename, phase, *, deadline=None):
        self._preflight()
        digest = hashlib.sha256(raw).hexdigest()
        op = self.state['operations'].get(key)
        if op:
            if op.get('sha256') != digest or op.get('file_name') != filename or op.get('phase_id') != self.profile.phases[phase]:
                raise AutomationError('This operation already has a different immutable file. Resume the original submission; accepted Final and consumed rounds cannot be replaced.')
            if op['state'] == 'finished':
                return op['result']
        else:
            op = {'state': 'new', 'file_name': filename, 'phase_id': self.profile.phases[phase],
                  'sha256': digest, 'raw_b64': base64.b64encode(raw).decode(), 'deadline': deadline}
            self.state['operations'][key] = op
            self._save()
        if op['state'] == 'creating':
            self._reconcile(key)
        if op['state'] == 'allocating':
            raise AmbiguousSubmission('Dataset creation was interrupted. No automatic POST retry is permitted; ask the organizer to resolve this checkpoint before continuing.')
        if op['state'] == 'new':
            op['state'] = 'allocating'
            self._save()
            try:
                created = self._api('POST', '/api/datasets/', json={'name': 'ICAIF original ' + digest[:16], 'type': 'submission',
                    'is_public': False, 'competition': self.profile.competition_id, 'file_name': filename,
                    'request_sassy_file_name': filename, 'file_size': len(raw)})
                data_key = created.get('key')
                if not isinstance(data_key, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', data_key):
                    raise AutomationError('Platform did not return a valid dataset key.')
                op.update(state='allocated', data_key=data_key, upload_url=_url(created.get('sassy_url'), storage=True))
                self._save()
            except AutomationError as error:
                raise AmbiguousSubmission('Dataset allocation may have committed. Resume this checkpoint; do not create another submission. ' + str(error)) from None
        if op['state'] == 'allocated':
            # Native Codabench may sign application/zip even for an explicitly
            # allowed JSON filename. Signed metadata does not transform bytes.
            signed_types = parse_qs(urlsplit(op['upload_url']).query).get('content-type', ['application/json'])
            if len(signed_types) != 1 or signed_types[0] not in ('application/json','application/zip','application/octet-stream'):
                raise AutomationError('Unsupported signed storage Content-Type; keep the allocated object checkpoint.')
            self._request('PUT', op['upload_url'], storage=True, content=raw, headers={'Content-Type': signed_types[0]})
            op['state'] = 'stored'
            self._save()
        if op['state'] == 'stored':
            self._api('PUT', f'/api/datasets/completed/{op["data_key"]}/')
            op['state'] = 'completed'
            op.pop('upload_url', None)
            self._save()
        if op['state'] == 'completed':
            # Recheck authoritative time after slow storage; the platform stamp still decides.
            if phase != 'registration':
                live = self.schedule()
                now = self._now(live)
                if phase == 'final_submission':
                    end = live.get('final_deadline') or live.get('evaluation_policy', {}).get('final_deadline') or load_config()['final_deadline']
                    missed = now > timestamp(end)
                else:
                    original_payload = strict_json(raw)
                    live_round = next((r for r in live['rounds'] if r['id'] == original_payload['round_id']), None)
                    missed = (live_round is None or live_round['status'] == 'CANCELLED'
                        or not timestamp(live_round['opens_at']) <= now < timestamp(live_round['deadline']))
                if missed:
                    result = {'status': 'MISSED_DEADLINE', 'phase': phase,
                              'note': 'Window closed or round cancelled before creation. No platform submission was created.'}
                    op.update(state='finished', result=result)
                    op.pop('raw_b64', None)
                    self._save()
                    return result
            op['state'] = 'creating'
            self._save()
            request = {'phase': op['phase_id'], 'data': op['data_key']}
            if self.profile.tasks is not None:
                request['tasks'] = self.profile.tasks
            try:
                created = self._api('POST', '/api/submissions/', json=request)
                op.update(state='submitted', submission_id=_id(created.get('id')))
                self._save()
            except AutomationError as error:
                raise AmbiguousSubmission('Submission creation may have committed. Resume this checkpoint to recover its original ID; never upload a replacement. ' + str(error)) from None
        if op['state'] == 'submitted':
            result = self.fetch(op['submission_id'], expected=op)
            op.update(state='finished', result=result)
            op.pop('raw_b64', None)
            self._save()
            return result
        raise AutomationError('Checkpoint contains an unsupported operation state.')

    def _receipt(self, identity, expected=None):
        identity = _id(identity)
        username = self._identity()
        native = self._api('GET', f'/api/submissions/{identity}/')
        if native.get('owner') != username or native.get('phase') not in self.profile.phases.values():
            raise AutomationError('Submission owner or phase does not match this account and competition.')
        value = self._request('GET', f'{self.profile.gateway_base}/submissions/{identity}/receipt')
        if (value.get('submission_id') != identity or value.get('owner_username') != username
                or value.get('phase_id') != native['phase']):
            raise AutomationError('Submission receipt owner, identity or phase is inconsistent.')
        names = {'register.json': self.profile.phases['registration'], 'final_submission.json': self.profile.phases['final_submission'],
                 'decision.json': native['phase'] if native['phase'] in (self.profile.phases['validation'], self.profile.phases['official']) else None}
        if names.get(value.get('file_name')) != native['phase'] or not re.fullmatch(r'[a-f0-9]{64}', value.get('sha256', '')):
            raise AutomationError('Receipt does not describe a canonical original submission.')
        if expected and any(value.get(k) != expected.get(k) for k in ('sha256', 'phase_id', 'file_name')):
            raise AutomationError('Submission phase, exact filename or raw file hash does not match the checkpoint.')
        return native, value

    def _reconcile(self, key):
        op = self.state['operations'][key]
        matches = []
        page = 1
        while page <= 100:
            listing = self._api('GET', '/api/submissions/', params={'phase': op['phase_id'], 'page': page})
            rows = listing.get('results') if isinstance(listing, dict) else listing
            if not isinstance(rows, list):
                raise AmbiguousSubmission('Could not inspect platform submissions; keep the checkpoint and resolve the original ID.')
            for item in rows:
                if item.get('owner') == self._identity() and item.get('phase') == op['phase_id'] and not item.get('parent'):
                    _, receipt = self._receipt(_id(item.get('id')))
                    if all(receipt.get(k) == op[k] for k in ('sha256', 'phase_id', 'file_name')):
                        matches.append(item['id'])
            if isinstance(listing, list) or not listing.get('next'):
                break
            page += 1
        if page > 100 or len(set(matches)) != 1:
            raise AmbiguousSubmission('Creation is unresolved or has multiple matching uploads. Use resolve with the verified original submission ID; no duplicate was uploaded.')
        op.update(state='submitted', submission_id=matches[0])
        self._save()

    def resolve(self, operation, submission_id):
        op = self.state['operations'].get(operation)
        if not op or op.get('state') not in ('creating', 'allocating', 'submitted'):
            raise AutomationError('Only a pending immutable operation can be resolved.')
        self._receipt(submission_id, expected=op)
        op.update(state='submitted', submission_id=submission_id)
        self._save()
        result = self.fetch(submission_id, expected=op)
        op.update(state='finished', result=result)
        op.pop('raw_b64', None)
        self._save()
        return result

    def _result_archive(self, identity, native):
        children = native.get('children') or []
        if len(children) > 1:
            raise AutomationError('Multiple task outputs are ambiguous; contact the organizer.')
        if children:
            identity = _id(children[0]['id'] if isinstance(children[0], dict) else children[0])
            child = self._api('GET', f'/api/submissions/{identity}/')
            if child.get('owner') != self._identity() or child.get('phase') != native['phase']:
                raise AutomationError('Child submission owner or phase mismatch.')
        details = self._api('GET', f'/api/submissions/{identity}/get_details/')
        blob = self._request('GET', _url(details.get('scoring_result'), storage=True), storage=True)
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
                members = bundle.infolist()
                if len(members) > 32 or sum(m.file_size for m in members) > 2 * MAX_RESPONSE:
                    raise ValueError()
                selected = [m for m in members if m.filename == 'result.json']
                if len(selected) != 1:
                    raise ValueError()
                item = selected[0]
                if item.file_size > MAX_RESPONSE or item.flag_bits & 1 or stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError()
                with bundle.open(item) as stream:
                    raw = stream.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ValueError()
                return strict_json(raw)
        except (ValueError, OSError, RuntimeError, zipfile.BadZipFile):
            raise AutomationError('Private scoring output must contain one bounded top-level result.json.') from None

    def fetch(self, submission_id, *, expected=None):
        started = time.monotonic()
        while True:
            native, receipt = self._receipt(submission_id, expected)
            if isinstance(receipt.get('result'), dict):
                result = receipt['result']
                break
            if native.get('status') == 'Finished':
                result = self._result_archive(submission_id, native)
                break
            if native.get('status') in ('Failed', 'Cancelled'):
                raise AutomationError(f'Submission {submission_id} needs worker recovery. Keep this original ID; contact the organizer instead of uploading again.')
            if time.monotonic() - started >= self.result_timeout:
                raise AutomationError(f'Submission {submission_id} is pending. Resume fetch with this original ID.')
            time.sleep(min(self.poll_interval, max(0, self.result_timeout - (time.monotonic() - started))))
        if not isinstance(result, dict):
            raise AutomationError('Private result must be a JSON object.')
        if receipt['file_name'] == 'register.json' and result.get('team_token'):
            self.save_credentials(result.get('team_id'), result['team_token'])
        secrets = [self.token, result.get('team_token'), self.creds['team_token'] if self.creds else None]
        safe_result = redact(result, secrets)
        safe_result['platform_submission_id'] = submission_id
        # PUBLISHED_READY is a frozen scorer receipt, not public leaderboard release.
        if receipt['file_name'] == 'final_submission.json':
            safe_result['publication_note'] = 'Final receipt recorded. Official scores are public only after organizer publication and platform reveal.'
        return safe_result

    def watch(self, strategy, *, phase='official', once=False, max_wait_seconds=3600, poll_seconds=30):
        self._phase(phase)
        if not callable(strategy):
            raise AutomationError('watch requires your local strategy callable and real data inputs.')
        _number(max_wait_seconds, 'max_wait_seconds')
        _number(poll_seconds, 'poll_seconds')
        # Recover older pending IDs before opening another round. A lost response
        # does not become permission to abandon that operation at its deadline.
        for key, existing in list(self.state['operations'].items()):
            if key.startswith('decision:' + phase + ':') and existing['state'] != 'finished':
                raw = base64.b64decode(existing['raw_b64'])
                self._submit(key, raw, 'decision.json', phase)
        started = time.monotonic()
        while True:
            schedule = self.schedule()
            now = self._now(schedule)
            rows = sorted((r for r in schedule['rounds'] if r['phase'] == phase and r['status'] != 'CANCELLED'), key=lambda r: timestamp(r['opens_at']))
            if rows and now >= max(timestamp(r['close_time']) for r in rows):
                return {'status': 'TRADING_COMPLETE_FINAL_REQUIRED' if phase == 'official' else 'VALIDATION_COMPLETE', 'phase': phase}
            row = next((r for r in rows if timestamp(r['opens_at']) <= now < timestamp(r['deadline'])), None)
            if row:
                key = 'decision:' + phase + ':' + row['id']
                existing = self.state['operations'].get(key)
                if existing:
                    if existing['state'] == 'finished':
                        result = existing['result']
                    else:
                        raw = base64.b64decode(existing['raw_b64'])
                        result = self._submit(key, raw, 'decision.json', phase)
                else:
                    own = self.round(row['id'])
                    if self._occupied(own):
                        result = {'status': 'SLOT_CONSUMED', 'round_id': row['id']}
                    else:
                        observation = {'phase': phase, 'round': row, 'symbols': schedule['symbols'],
                            'as_of': now.isoformat(), 'portfolio': self.portfolio(phase), 'round_state': own}
                        try:
                            weights = strategy(observation)
                        except Exception:
                            raise AutomationError('Local strategy failed; no submission was uploaded for this round.') from None
                        creds = self._team()
                        payload = {'submission_type': 'decision', 'team_id': creds['team_id'], 'team_token': creds['team_token'],
                            'phase': phase, 'round_id': row['id'], 'weights': weights}
                        try:
                            raw = dumps_json(payload).encode('utf-8')
                        except (ValueError, TypeError, RecursionError):
                            raise AutomationError('Strategy must return valid original JSON weights.') from None
                        result = self._decision(raw, payload)
                if once:
                    return result
            elif once:
                return {'status': 'WAITING_FOR_ROUND', 'phase': phase, 'next_deadline': schedule.get('next_deadline')}
            elapsed = time.monotonic() - started
            if elapsed >= max_wait_seconds:
                return {'status': 'WATCH_PAUSED', 'phase': phase, 'note': 'Resume watch with the same checkpoint.'}
            wait = min(poll_seconds, max_wait_seconds - elapsed, 60)
            if self.clock:
                self.clock.sleep(wait)
            else:
                time.sleep(wait)
