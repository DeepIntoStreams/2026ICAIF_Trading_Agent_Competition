"""Standalone submission checks; no service credentials or backend imports."""
import json
import math
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from urllib.parse import urlsplit

from kit.config import load_config, load_schedule, load_symbols

FILENAMES = {'register': 'register.json', 'decision': 'decision.json', 'final_submission': 'final_submission.json'}
FIELDS = {
    'register': {'submission_type', 'team_name', 'team_members', 'contact_email'},
    'decision': {'submission_type', 'team_id', 'team_token', 'phase', 'round_id', 'weights'},
    'final_submission': {'submission_type', 'team_id', 'team_token', 'team_members', 'materials_url', 'materials'},
}
MAX_BYTES = 120_000  # Reserve room below the backend's 128 KiB envelope limit.


class SubmissionError(ValueError):
    """An error containing field labels only, never submitted credential values."""


def is_placeholder(value):
    return isinstance(value, str) and re.search(r'\b(?:DEMO|REPLACE)(?:[_ -]|$)', value, re.IGNORECASE) is not None


def _structure(value, depth=0, allow_placeholders=False):
    if depth > 10:
        raise SubmissionError('JSON nesting is too deep.')
    if isinstance(value, str):
        if len(value) > 8192:
            raise SubmissionError('A JSON string exceeds 8192 characters.')
        if not allow_placeholders and is_placeholder(value):
            raise SubmissionError('Replace all DEMO/REPLACE placeholders before preparing a live submission.')
    elif isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise SubmissionError('JSON object keys must be strings.')
            _structure(key, depth + 1, allow_placeholders)
            _structure(child, depth + 1, allow_placeholders)
    elif isinstance(value, list):
        for child in value:
            _structure(child, depth + 1, allow_placeholders)
    elif isinstance(value, Decimal) and not value.is_finite():
        raise SubmissionError('JSON numbers must be finite.')
    elif isinstance(value, float) and not math.isfinite(value):
        raise SubmissionError('JSON numbers must be finite.')


def load_json(path):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise SubmissionError('JSON must not contain duplicate keys.')
            result[key] = value
        return result

    def constant(_):
        raise SubmissionError('JSON numbers must be finite.')

    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SubmissionError('Submission exceeds the local 120000-byte limit.')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_float=Decimal, parse_constant=constant)
        _structure(value, allow_placeholders=True)
        return value
    except SubmissionError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError, InvalidOperation):
        raise SubmissionError('Could not read a valid UTF-8 JSON file.') from None


def _text(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SubmissionError(f'{label} must be nonempty text of at most {maximum} characters.')
    return value.strip()


def _email(value):
    if not isinstance(value, str) or len(value) > 320 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise SubmissionError('Email fields must contain valid email addresses, at most 320 characters.')
    return value.lower()


def _members(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        raise SubmissionError('team_members must contain between 1 and 50 members.')
    emails = set()
    for member in value:
        if not isinstance(member, dict) or set(member) != {'name', 'email', 'institution'}:
            raise SubmissionError('Each member must contain exactly name, email, and institution.')
        _text(member['name'], 'Member name')
        _text(member['institution'], 'Member institution')
        email = _email(_text(member['email'], 'Member email', 320))
        if email in emails:
            raise SubmissionError('Member email addresses must be unique, ignoring case.')
        emails.add(email)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        raise SubmissionError('Times must be timezone-aware ISO 8601 timestamps.') from None


def _weights(weights):
    if not isinstance(weights, dict) or set(weights) != set(load_symbols()):
        raise SubmissionError('weights must contain exactly the 30 official symbols, including zero targets.')
    with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
        total = Decimal(0)
        for symbol in sorted(weights):
            value = weights[symbol]
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                raise SubmissionError('Every weight must be a JSON number, not a string or boolean.')
            number = value if isinstance(value, Decimal) else Decimal(str(value))
            if not number.is_finite() or not Decimal(0) <= number <= Decimal('.30'):
                raise SubmissionError('Every weight must be finite and between 0 and 0.30.')
            total += number
        if total > Decimal(1) + Decimal('1e-10'):
            raise SubmissionError('Total stock weight exceeds 1 plus the backend tolerance of 1e-10.')


def validate_payload(payload, filename, *, allow_placeholders=False, submitted_at=None, schedule_path=None):
    """Return the unchanged payload after local checks; no authentication is attempted."""
    if not isinstance(payload, dict) or not isinstance(payload.get('submission_type'), str):
        raise SubmissionError('Submission must be a JSON object with submission_type.')
    kind = payload['submission_type']
    if kind not in FILENAMES or Path(filename).name != FILENAMES[kind]:
        raise SubmissionError('Filename must match register.json, decision.json, or final_submission.json and its type.')
    if set(payload) != FIELDS[kind]:
        raise SubmissionError('Submission has missing or unknown fields; use the standard template.')
    _structure(payload, allow_placeholders=allow_placeholders)
    if kind == 'register':
        _text(payload['team_name'], 'team_name')
        _email(payload['contact_email'])
        _members(payload['team_members'])
    else:
        _text(payload['team_id'], 'team_id')
        _text(payload['team_token'], 'team_token')
    if kind == 'decision':
        _weights(payload['weights'])
        phase = payload['phase']
        _text(payload['round_id'], 'round_id', 80)
        schedule = load_schedule(schedule_path)
        row = next((row for row in schedule['rounds'] if row['id'] == payload['round_id']), None)
        if phase not in ('validation', 'official') or row is None or row['phase'] != phase or row['status'] == 'CANCELLED':
            raise SubmissionError('phase/round_id must name a non-cancelled round in the selected schedule.')
        if submitted_at is not None and not timestamp(row['opens_at']) <= timestamp(submitted_at) < timestamp(row['deadline']):
            raise SubmissionError('Upload time must be inside the round window, strictly before its deadline.')
    elif kind == 'final_submission':
        _members(payload['team_members'])
        link = _text(payload['materials_url'], 'materials_url', 4096)
        try:
            parsed = urlsplit(link)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None or any(c.isspace() for c in link):
                raise ValueError()
        except ValueError:
            raise SubmissionError('materials_url must be an HTTPS shared link without embedded credentials.') from None
        materials = payload['materials']
        if not isinstance(materials, list) or not 1 <= len(materials) <= 200:
            raise SubmissionError('materials must contain between 1 and 200 filenames.')
        for material in materials:
            _text(material, 'Material filename', 8192)
        if submitted_at is not None:
            schedule = load_schedule(schedule_path)
            official_closes = [timestamp(row['close_time']) for row in schedule['rounds'] if row['phase'] == 'official']
            if not official_closes or not max(official_closes) <= timestamp(submitted_at) <= timestamp(load_config()['final_deadline']):
                raise SubmissionError('Final upload time must be from the last official close through the final deadline.')
    return payload


def validate_submission(path, **options):
    return validate_payload(load_json(path), Path(path).name, **options)


def dumps_json(value):
    """Encode Decimal as exact JSON number text instead of rounding through float."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise SubmissionError('JSON numbers must be finite.')
        return str(value)
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':' + dumps_json(child) for key, child in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(dumps_json(child) for child in value) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
