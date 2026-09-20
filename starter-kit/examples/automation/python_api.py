"""Read your current round and optionally submit an explicit local original file.

Run from the kit root:
  python examples/automation/python_api.py --profile <organizer-profile.json>
  python examples/automation/python_api.py --profile <organizer-profile.json> --decision private/decision.json

The decision file must be genuinely prepared for the current round with all
30 symbols and your own TEAM_ID/TEAM_TOKEN. This example invents no inputs.
"""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kit.config import load_environment
from kit.contracts import dumps_json
from kit.original_client import OriginalSession, load_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--decision')
    args = parser.parse_args()
    load_environment()
    with OriginalSession(profile=load_profile(args.profile), token=os.environ['CODABENCH_TOKEN'],
            checkpoint='.icaif/checkpoint.json', credentials='.icaif/credentials.json') as client:
        schedule = client.schedule()
        print(dumps_json({'current_time': schedule.get('current_time'), 'current_round': schedule.get('current_round')}))
        if args.decision:
            print(dumps_json(client.decision(args.decision)))


if __name__ == '__main__':
    main()
