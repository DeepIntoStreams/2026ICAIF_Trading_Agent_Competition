"""Use your own precomputed round weights; no market prices are invented.

Set ICAIF_WEIGHTS_FILE to a private JSON file with exactly:
{"round_id": "validation-2026-10-08-r1", "weights": {all 30 symbols: numbers}}
Refresh it from your legitimate data pipeline before each round.
The watch client supplies team credentials and exact decision.json envelope.
"""
import os
from pathlib import Path
from kit.original_client import strict_json


def strategy(observation):
    path = os.environ.get('ICAIF_WEIGHTS_FILE')
    if not path:
        raise ValueError('Configure your own prepared weights file.')
    value = strict_json(Path(path).read_bytes())
    if set(value) != {'round_id', 'weights'} or value['round_id'] != observation['round']['id']:
        raise ValueError('Your weights must explicitly target this round, not a stale round.')
    return value['weights']
