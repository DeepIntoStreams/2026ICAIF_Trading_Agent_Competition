"""Evaluate one local completed portfolio trajectory, without fetching data."""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kit.evaluation import evaluate  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description='Recompute decision-period metrics from JSON or a saved metrics API response.')
    parser.add_argument('file', type=Path)
    args = parser.parse_args(argv)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON keys are not allowed')
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError('Non-finite JSON numbers are not allowed')

    try:
        payload = json.loads(args.file.read_text(encoding='utf-8'), parse_float=Decimal,
                             parse_constant=nonfinite, object_pairs_hook=pairs)
        result = evaluate(payload)
    except (OSError, UnicodeError, ValueError, ArithmeticError, RecursionError):
        print('Error: invalid or unreadable evaluation trajectory; check its values, ordering, and completed endpoints.', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
