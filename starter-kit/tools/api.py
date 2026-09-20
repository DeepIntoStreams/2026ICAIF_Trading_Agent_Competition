"""Read the competition schedule and your team's results."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kit.api import APIError, check_compatibility, request_json
from kit.config import load_config, load_environment


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read competition schedule and private team results')
    parser.add_argument('action', choices=['schedule', 'portfolio', 'decisions', 'round', 'metrics', 'check'])
    parser.add_argument('--phase', choices=['validation', 'official'], default='validation')
    parser.add_argument('--round-id')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--base-url', help='Published participant API origin, not the Codabench competition page')
    parser.add_argument('--output', type=Path, help='Save JSON to a new private file instead of stdout')
    args = parser.parse_args(argv)
    try:
        load_environment()
        base = args.base_url or os.environ.get('TRADING_API_BASE_URL') or load_config()['services']['api_base_url']
        if not base:
            raise APIError('Set TRADING_API_BASE_URL in .env after the organizer publishes the participant API URL')
        token = None
        query = None
        if args.action in ('schedule', 'check'):
            path = '/api/v1/schedule'
        else:
            token = os.environ.get('TEAM_TOKEN', '')
            if not token.strip() or token.startswith(('DEMO_', 'REPLACE_')):
                raise APIError('Set your private TEAM_TOKEN in .env before requesting team results')
            query = {'phase': args.phase}
            path = '/api/v1/me/' + args.action
            if args.action == 'round':
                if not args.round_id or not re.fullmatch(r'(validation|official)-\d{4}-\d{2}-\d{2}-r[1-7]', args.round_id):
                    raise APIError('Provide --round-id phase-YYYY-MM-DD-rN')
                path, query = '/api/v1/me/rounds/' + args.round_id, None
            elif args.action == 'decisions':
                if args.offset < 0 or not 1 <= args.limit <= 200:
                    raise APIError('Offset must be nonnegative and limit must be between 1 and 200')
                query.update(offset=args.offset, limit=args.limit)
        result = request_json(base, path, token=token, query=query)
        if args.action == 'check':
            result = check_compatibility(result)
        encoded = json.dumps(result, indent=2, allow_nan=False) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write(encoded)
            print('API response saved to the requested file.')
        else:
            print(encoded, end='')
        return 2 if args.action == 'check' and not result['compatible'] else 0
    except FileExistsError:
        print('Error: output already exists; choose a new filename.', file=sys.stderr)
    except (KeyError, TypeError):
        print('Error: local service configuration or API response has an invalid structure.', file=sys.stderr)
    except (APIError, ValueError) as error:
        print('Error: ' + str(error), file=sys.stderr)
    except OSError:
        print('Error: could not read configuration or write the result.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
