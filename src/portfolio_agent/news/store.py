"""Immutable JSONL storage helpers for normalized news records."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import NewsRecord


class NewsStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.normalized_path = self.root / "normalized.jsonl"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def write_raw(self, provider: str, payloads: Iterable[dict]) -> Path:
        path = self.raw_dir / f"{provider}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            for payload in payloads:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        return path

    def write_normalized(self, records: Iterable[NewsRecord]) -> None:
        existing = self.load_all()
        by_key: dict[tuple[str, str], NewsRecord] = {
            (record.provider, record.provider_news_id): record
            for record in existing
        }
        hash_to_key = {
            record.content_hash: (record.provider, record.provider_news_id)
            for record in existing
        }

        for record in records:
            key = (record.provider, record.provider_news_id)
            if key in by_key:
                _merge_record(by_key[key], record)
                hash_to_key[by_key[key].content_hash] = key
                continue
            if record.content_hash in hash_to_key:
                _merge_record(by_key[hash_to_key[record.content_hash]], record)
                continue
            by_key[key] = record
            hash_to_key[record.content_hash] = key

        ordered = sorted(
            by_key.values(),
            key=lambda record: (
                record.available_at_utc,
                record.provider,
                record.provider_news_id,
            ),
        )
        self.normalized_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.normalized_path, "w", encoding="utf-8") as f:
            for record in ordered:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def load_all(self) -> list[NewsRecord]:
        if not self.normalized_path.exists():
            return []

        records: list[NewsRecord] = []
        with open(self.normalized_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(NewsRecord.from_dict(json.loads(line)))
        return records

    def load_visible(
        self,
        tickers: Iterable[str],
        cutoff_utc: datetime,
    ) -> list[NewsRecord]:
        ticker_set = set(tickers)
        visible = [
            record
            for record in self.load_all()
            if record.available_at_utc <= cutoff_utc
            and ticker_set.intersection(record.tickers)
        ]
        return sorted(
            visible,
            key=lambda record: (
                record.available_at_utc,
                record.provider,
                record.provider_news_id,
            ),
        )

    def file_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                rel = str(path.relative_to(self.root))
                hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return hashes


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    return sorted({*left, *right})


def _merge_record(target: NewsRecord, incoming: NewsRecord) -> None:
    target.tickers = _merge_unique(target.tickers, incoming.tickers)
    target.company_names = _merge_unique(target.company_names, incoming.company_names)
    target.published_at_utc = min(target.published_at_utc, incoming.published_at_utc)
    target.fetched_at_utc = min(target.fetched_at_utc, incoming.fetched_at_utc)
    target.first_seen_at_utc = min(target.first_seen_at_utc, incoming.first_seen_at_utc)
    target.available_at_utc = min(target.available_at_utc, incoming.available_at_utc)
    if not target.raw_path and incoming.raw_path:
        target.raw_path = incoming.raw_path
