"""Read-only HTTP client for the published participant API."""

import json
import math
import re
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from kit.config import load_schedule, load_symbols


class APIError(ValueError):
    """A participant-facing error that contains no credentials or response body."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(base_url, path, *, token=None, query=None):
    try:
        parsed = urlsplit(base_url)
        parsed.port
    except (ValueError, TypeError):
        raise APIError('The API base URL is invalid') from None
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and local))):
        raise APIError('Use the published HTTPS API base URL; HTTP is supported only on local loopback')
    if not path.startswith('/api/v1/') or '?' in path or '#' in path or '..' in path:
        raise APIError('Only participant API paths are supported')
    if token is not None and (not token.strip() or '\r' in token or '\n' in token):
        raise APIError('TEAM_TOKEN is missing or malformed')
    url = base_url.rstrip('/') + path
    if query:
        url += '?' + urlencode(query)
    headers = {'Accept': 'application/json', 'User-Agent': 'ICAIF-Participant-Kit/1.0.0'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers, method='GET'), timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise APIError('API response is too large; reduce the requested page size')
    except HTTPError as error:
        request_id = error.headers.get('X-Request-ID', '')
        suffix = f' Request ID: {request_id}.' if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id) else ''
        guidance = {401: 'Check the team token.', 403: 'Access is not allowed.', 404: 'Check the API base URL or round.',
                    409: 'This result is not available yet; check phase completion.',
                    429: 'Too many requests; wait before retrying.'}.get(error.code, 'Contact the organizer if this persists.')
        if 300 <= error.code < 400:
            guidance = 'Redirect refused; use the final published API URL.'
        error.close()
        raise APIError(f'HTTP {error.code}. {guidance}{suffix}') from None
    except (URLError, OSError, ValueError) as error:
        if isinstance(error, APIError):
            raise
        raise APIError('Could not reach the API; check its published URL and network connection') from None

    def nonfinite(_):
        raise ValueError('Non-finite response value')

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Non-finite response value')
        return result

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate response key')
            result[key] = value
        return result

    try:
        payload = json.loads(raw, parse_constant=nonfinite, parse_float=finite_float, object_pairs_hook=unique_pairs)
        if not isinstance(payload, dict):
            raise ValueError('Expected object')
        return payload
    except (ValueError, UnicodeError):
        raise APIError('The service did not return a valid JSON object; check the API base URL') from None


def check_compatibility(payload):
    """Compare public configuration only; this does not authenticate a team."""
    provided = payload.get('symbols')
    checks = {'universe_matches': isinstance(provided, list)
              and all(isinstance(symbol, str) for symbol in provided)
              and sorted(provided) == sorted(load_symbols()),
              'universe_confirmed': payload.get('universe_confirmed') is True,
              'calendar_matches': False}
    try:
        def calendar(document):
            result = {}
            for row in document['rounds']:
                if row['id'] in result:
                    raise ValueError('Duplicate round')
                times = tuple(datetime.fromisoformat(row[k].replace('Z', '+00:00'))
                              for k in ('opens_at', 'deadline', 'execution_time', 'close_time'))
                if any(t.utcoffset() is None for t in times):
                    raise ValueError('Naive calendar')
                result[row['id']] = (*times, row['status'] == 'CANCELLED')
            return result
        checks['calendar_matches'] = calendar(payload) == calendar(load_schedule())
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return {'compatible': all(checks.values()), 'checks': checks,
            'note': 'Use the latest organizer schedule if the calendar differs. This checks public configuration only.'}
