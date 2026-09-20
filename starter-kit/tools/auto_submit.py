#!/usr/bin/env python3
"""Original ICAIF upload and read-only participant commands."""
import argparse
import importlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kit.config import load_environment
from kit.contracts import dumps_json
from kit.original_client import AutomationError, OriginalSession, load_profile, write_private_json


def parser():
    p = argparse.ArgumentParser(description='Original ICAIF: register, seven rounds per trading day, explicit Final materials, private GET queries.')
    p.add_argument('--profile', help='Organizer JSON profile with actual competition and native phase mapping (or ICAIF_PROFILE).')
    p.add_argument('--checkpoint', default='.icaif/checkpoint.json', help='Keep and reuse this private checkpoint for all commands.')
    p.add_argument('--credentials', default='.icaif/credentials.json', help='Private TEAM_ID/TEAM_TOKEN file, created at registration.')
    p.add_argument('--env', help='Optional original NAME=value environment file; never interpreted as shell.')
    p.add_argument('--output', help='Save the redacted result atomically with mode 0600 instead of printing it.')
    p.add_argument('--result-timeout', type=float, default=180)
    commands = p.add_subparsers(dest='command', required=True)
    for name, description in [('register', 'Upload original register.json once.'), ('decision', 'Upload original decision.json once in its round.'), ('final', 'Upload explicit real final_submission.json materials once.')]:
        command = commands.add_parser(name, help=description)
        command.add_argument('--file', required=True)
    commands.add_parser('schedule', help='GET organizer schedule; creates no submission.')
    for name in ('portfolio', 'decisions', 'metrics', 'ledger'):
        command = commands.add_parser(name, help='GET own private ' + name + '; creates no submission.')
        command.add_argument('--phase', choices=('validation', 'official'), default='validation' if name == 'metrics' else 'official')
        if name == 'decisions':
            command.add_argument('--offset', type=int, default=0)
            command.add_argument('--limit', type=int, default=200)
    command = commands.add_parser('round', help='GET own round decisions, execution, trades and snapshot.')
    command.add_argument('--round-id', required=True)
    command = commands.add_parser('fetch', help='Read an existing private receipt; never creates a submission.')
    command.add_argument('--submission-id', type=int, required=True)
    command = commands.add_parser('resolve', help='Match a pending operation to its original ID, exact filename and file hash.')
    command.add_argument('--operation', required=True)
    command.add_argument('--submission-id', type=int, required=True)
    commands.add_parser('import-credentials', help='Import organizer-issued TEAM_ID and TEAM_TOKEN from environment, without network.')
    command = commands.add_parser('watch', help='Run your local strategy once per original round; never submits Final.')
    command.add_argument('--strategy', required=True, help='Your module:callable taking the documented observation object.')
    command.add_argument('--phase', choices=('validation', 'official'), default='official')
    command.add_argument('--once', action='store_true')
    command.add_argument('--max-wait-seconds', type=float, default=3600)
    command.add_argument('--poll-seconds', type=float, default=30)
    return p


def load_strategy(name):
    try:
        module, separator, attribute = name.partition(':')
        if not separator or not module or not attribute:
            raise ValueError()
        strategy = getattr(importlib.import_module(module), attribute)
        if not callable(strategy):
            raise ValueError()
        return strategy
    except Exception:
        raise AutomationError('Could not load your local module:callable strategy. No submission was created.') from None


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        load_environment(args.env)
        token = os.environ.get('CODABENCH_TOKEN', '')
        if not token:
            raise AutomationError('Set your personal CODABENCH_TOKEN in the local environment.')
        profile_path = args.profile or os.environ.get('ICAIF_PROFILE')
        if not profile_path:
            raise AutomationError('Provide --profile with the actual organizer profile, or set ICAIF_PROFILE.')
        profile = load_profile(profile_path)
        with OriginalSession(profile=profile, token=token, checkpoint=args.checkpoint,
                credentials=args.credentials, result_timeout=args.result_timeout) as session:
            command = args.command
            if command == 'import-credentials':
                session.save_credentials(os.environ.get('TEAM_ID'), os.environ.get('TEAM_TOKEN'))
                result = {'status': 'CREDENTIALS_SAVED', 'path': str(Path(args.credentials).resolve())}
            elif command in ('register', 'decision', 'final'):
                result = getattr(session, command)(args.file)
            elif command == 'schedule':
                result = session.schedule()
            elif command in ('portfolio', 'metrics', 'ledger'):
                result = getattr(session, command)(args.phase)
            elif command == 'decisions':
                result = session.decisions(args.phase, offset=args.offset, limit=args.limit)
            elif command == 'round':
                result = session.round(args.round_id)
            elif command == 'fetch':
                result = session.fetch(args.submission_id)
            elif command == 'resolve':
                result = session.resolve(args.operation, args.submission_id)
            elif command == 'watch':
                result = session.watch(load_strategy(args.strategy), phase=args.phase, once=args.once,
                    max_wait_seconds=args.max_wait_seconds, poll_seconds=args.poll_seconds)
            if args.output:
                write_private_json(args.output, result)
                print('Private result saved.')
            else:
                print(dumps_json(result))
        return 0
    except (AutomationError, OSError, ValueError) as error:
        # Only our own explicitly safe errors may be rendered; underlying parser,
        # filesystem, strategy and HTTP exceptions could include private values.
        text = str(error) if isinstance(error, AutomationError) else 'Local configuration or file operation failed; inspect private files without sharing credentials.'
        print('Error: ' + text, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Stopped. Keep the checkpoint and resume the same operation; do not create a replacement upload.', file=sys.stderr)
        return 130
    except Exception:
        print('Error: Unexpected response or local strategy failure. Keep the checkpoint and original submission ID; no automatic replacement was created.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
