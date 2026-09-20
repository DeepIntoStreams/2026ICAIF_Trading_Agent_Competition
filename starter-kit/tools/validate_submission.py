"""Check a single local upload; this command never submits or authenticates it."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kit.contracts import SubmissionError, validate_submission  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--allow-placeholders', action='store_true', help='EXAMPLE format checks only; not live validation')
    parser.add_argument('--submitted-at', help='Hypothetical platform upload time including timezone')
    parser.add_argument('--schedule', type=Path)
    args = parser.parse_args(argv)
    try:
        validate_submission(args.file, allow_placeholders=args.allow_placeholders,
                            submitted_at=args.submitted_at, schedule_path=args.schedule)
    except SubmissionError as error:
        print('Invalid submission: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
        print('Validation failed: check the local input and schedule configuration.', file=sys.stderr)
        return 1
    print('EXAMPLE format checks passed; placeholders remain unsuitable for live submission.' if args.allow_placeholders
          else 'Local checks passed. Server authentication, actual upload time, and round availability remain authoritative.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
