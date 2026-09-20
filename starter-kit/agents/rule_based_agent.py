"""Offline example: buy up to five positive five-interval momentum signals.

Inputs are participant-supplied completed close observations. This example does
not fetch data, call a model, submit a decision, or execute a trade.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT_ROOT))
from kit.config import load_environment, load_schedule, load_symbols  # noqa: E402


def read_json(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Input JSON contains duplicate keys')
            result[key] = value
        return result

    def invalid_number(_):
        raise ValueError('Input JSON contains a non-finite number')

    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_keys,
                          parse_float=Decimal, parse_constant=invalid_number)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Could not read a valid UTF-8 JSON input file') from error


def load_universe():
    return sorted(load_symbols())


def parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (AttributeError, ValueError, TypeError) as error:
        raise ValueError('Timestamps must be timezone-aware ISO 8601 strings') from error


def validate_round(phase, round_id, as_of, schedule=None):
    """Use exact published round windows, including amended or cancelled sessions."""
    match = re.fullmatch(r'(validation|official)-(\d{4}-\d{2}-\d{2})-r([1-7])', round_id)
    if not match or match[1] != phase:
        raise ValueError('round-id must match phase and use phase-YYYY-MM-DD-r1 through r7')
    try:
        day = date.fromisoformat(match[2])
    except ValueError as error:
        raise ValueError('round-id contains an invalid calendar date') from error
    schedule = load_schedule() if schedule is None else schedule
    rows = schedule.get('rounds') if isinstance(schedule, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Schedule must contain a list of round objects')
    matches = [row for row in rows if row.get('id') == round_id]
    if len(matches) != 1:
        raise ValueError('The requested round must occur exactly once in the published schedule')
    row = matches[0]
    if row.get('phase') != phase or row.get('day') != day.isoformat() or row.get('number') != int(match[3]):
        raise ValueError('Schedule round identity is inconsistent')
    if row.get('status') == 'CANCELLED':
        raise ValueError('The requested round is cancelled in the published schedule')
    opens_at, deadline = parse_timestamp(row.get('opens_at')), parse_timestamp(row.get('deadline'))
    execution, close = parse_timestamp(row.get('execution_time')), parse_timestamp(row.get('close_time'))
    if not opens_at < deadline <= execution < close:
        raise ValueError('Schedule window, execution, and close timestamps are inconsistent')
    if not opens_at <= as_of < deadline:
        raise ValueError('as-of must fall inside the round upload window, strictly before its ET deadline')


def momentum_weights(data, symbols, as_of):
    """Validate every record, then score the latest six eligible completed closes."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('as-of must be timezone aware')
    records = data.get('observations') if isinstance(data, dict) else None
    if not isinstance(records, dict) or set(records) - set(symbols):
        raise ValueError('observations must map only official universe symbols to record lists')
    scores = []
    with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
        for symbol in sorted(symbols):
            rows = records.get(symbol, [])
            if not isinstance(rows, list):
                raise ValueError('Each symbol must contain a list of observations')
            seen, eligible = set(), []
            for row in rows:
                if not isinstance(row, dict) or not {'timestamp', 'available_at', 'close'} <= row.keys():
                    raise ValueError('Each observation needs timestamp, available_at, and close')
                timestamp, available = parse_timestamp(row['timestamp']), parse_timestamp(row['available_at'])
                if timestamp in seen:
                    raise ValueError('A symbol cannot contain duplicate observation timestamps')
                seen.add(timestamp)
                if available < timestamp:
                    raise ValueError('available_at cannot precede its completed-close timestamp')
                raw = row['close']
                if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
                    raise ValueError('Close prices must be positive finite JSON numbers')
                close = Decimal(str(raw))
                if not close.is_finite() or close <= 0:
                    raise ValueError('Close prices must be positive finite JSON numbers')
                if timestamp <= as_of and available <= as_of:
                    eligible.append((timestamp, close))
            eligible.sort()
            if len(eligible) >= 6:
                recent = eligible[-6:]
                score = recent[-1][1] / recent[0][1] - 1
                if score > 0:
                    scores.append((symbol, score))
        selected = {symbol for symbol, _ in sorted(scores, key=lambda item: (-item[1], item[0]))[:5]}
    return {symbol: .20 if symbol in selected else 0 for symbol in sorted(symbols)}


def write_new_decision(path, payload):
    """Create a new output with private POSIX permissions; never replace a file."""
    encoded = json.dumps(payload, indent=2, allow_nan=False) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ValueError('Output already exists; choose a new output filename') from error
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(encoded)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline five-interval momentum example; no data fetching or submission.')
    parser.add_argument('--data', required=True, type=Path, help='Participant-supplied observation JSON')
    parser.add_argument('--as-of', required=True, help='Timezone-aware information cutoff inside the upload window')
    parser.add_argument('--phase', required=True, choices=('validation', 'official'))
    parser.add_argument('--round-id', required=True, help='For example validation-2026-10-08-r1')
    parser.add_argument('--output', required=True, type=Path, help='New decision JSON filename; never overwritten')
    parser.add_argument('--schedule', type=Path, help='Saved published schedule JSON; defaults to the bundled schedule')
    parser.add_argument('--demo', action='store_true', help='Use invalid demo credentials instead of environment credentials')
    args = parser.parse_args(argv)
    try:
        load_environment()
        as_of = parse_timestamp(args.as_of)
        validate_round(args.phase, args.round_id, as_of, load_schedule(args.schedule))
        weights = momentum_weights(read_json(args.data), load_universe(), as_of)
        team_id = 'DEMO_TEAM_ID' if args.demo else os.environ.get('TEAM_ID', '')
        token = 'DEMO_TEAM_TOKEN_NOT_VALID' if args.demo else os.environ.get('TEAM_TOKEN', '')
        if not team_id.strip() or not token.strip():
            raise ValueError('Set TEAM_ID and TEAM_TOKEN in the environment, or use --demo')
        payload = {'submission_type': 'decision', 'team_id': team_id, 'team_token': token,
                   'phase': args.phase, 'round_id': args.round_id, 'weights': weights}
        write_new_decision(args.output, payload)
    except ValueError as error:
        print('Error: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ArithmeticError):
        print('Error: could not calculate or write the decision', file=sys.stderr)
        return 1
    print('Decision JSON created. Validate it before a separate manual upload.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
