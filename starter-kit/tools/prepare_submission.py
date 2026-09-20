"""Insert local credentials into a template and create one upload without overwriting."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kit.config import load_environment  # noqa: E402
from kit.contracts import (  # noqa: E402
    FILENAMES,
    SubmissionError,
    dumps_json,
    is_placeholder,
    load_json,
    validate_payload,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('template', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        load_environment()
        payload = load_json(args.template)
        if not isinstance(payload, dict) or payload.get('submission_type') not in FILENAMES:
            raise SubmissionError('Template must contain a supported submission_type.')
        if args.template.name != FILENAMES[payload['submission_type']]:
            raise SubmissionError('Template must use the required upload filename.')
        if payload['submission_type'] != 'register':
            for field, variable in [('team_id', 'TEAM_ID'), ('team_token', 'TEAM_TOKEN')]:
                if is_placeholder(payload.get(field)):
                    value = os.environ.get(variable)
                    if not value or is_placeholder(value):
                        raise SubmissionError(f'Set a real {variable} in your private environment or local .env.')
                    payload[field] = value
        validate_payload(payload, args.output.name)
        encoded = dumps_json(payload) + '\n'
        # Exclusive creation preserves any existing upload, including a previous token.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(encoded)
    except SubmissionError as error:
        print('Preparation failed: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
        print('Preparation failed: check template/configuration and use an unused required filename in a writable directory.', file=sys.stderr)
        return 1
    print('Submission prepared. Keep its credentials private and upload only this JSON file.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
