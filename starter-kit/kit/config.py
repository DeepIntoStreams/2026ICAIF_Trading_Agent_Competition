"""Local configuration and environment values, with no shell evaluation."""

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_environment(path=None):
    """Read simple NAME=value entries; process environment values take precedence."""
    source = Path(path) if path is not None else ROOT / '.env'
    if not source.exists():
        return
    for line in source.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, separator, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError('Local .env must contain NAME=value entries; no shell commands')
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError('Local .env contains an unmatched quote')
            value = value[1:-1]
        os.environ.setdefault(name, value)


def load_config():
    return json.loads((ROOT / 'competition.json').read_text(encoding='utf-8'))


def load_symbols():
    data = json.loads((ROOT / 'universe.json').read_text(encoding='utf-8'))
    symbols = [symbol for group in data['sectors'].values() for symbol in group]
    if len(symbols) != 30 or len(set(symbols)) != 30:
        raise ValueError('The competition universe must contain 30 unique symbols')
    return symbols


def load_schedule(path=None):
    return json.loads((Path(path) if path is not None else ROOT / 'schedule.json').read_text(encoding='utf-8'))
