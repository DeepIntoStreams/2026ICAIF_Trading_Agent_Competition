"""Load private evaluation data and public RL training data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Evaluation data (stock_data_1y)
# ---------------------------------------------------------------------------

def load_evaluation_universe(data_root: str | Path) -> dict[str, list[str]]:
    path = Path(data_root) / "universe.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def flatten_universe(sectors: dict[str, list[str]]) -> list[str]:
    return [t for group in sectors.values() for t in group]


def load_price_data(
    data_root: str | Path,
    tickers: list[str],
) -> dict[str, pd.DataFrame]:
    prices_dir = Path(data_root) / "prices_daily"
    result: dict[str, pd.DataFrame] = {}

    for ticker in tickers:
        filepath = prices_dir / f"{ticker}.csv"
        if not filepath.exists():
            continue

        df = pd.read_csv(filepath, parse_dates=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        df = df.rename(columns={"Date": "date"})

        adj_factor = df["adj_close"] / df["close"]
        df["adj_open"] = df["open"] * adj_factor
        df["adj_high"] = df["high"] * adj_factor
        df["adj_low"] = df["low"] * adj_factor

        valid = (df["adj_close"] > 0) & (df["adj_open"] > 0) & (df["volume"] >= 0)
        df = df[valid].reset_index(drop=True)

        hi = np.maximum(df["adj_open"].values, df["adj_close"].values)
        lo = np.minimum(df["adj_open"].values, df["adj_close"].values)
        df["adj_high"] = np.maximum(df["adj_high"].values, hi)
        df["adj_low"] = np.minimum(df["adj_low"].values, lo)

        result[ticker] = df

    return result


def align_trading_dates(
    prices: dict[str, pd.DataFrame],
) -> tuple[list[pd.Timestamp], dict[str, pd.DataFrame]]:
    """Build a common sorted calendar; forward-fill short gaps per ticker."""
    all_dates: set[pd.Timestamp] = set()
    for df in prices.values():
        all_dates.update(df["date"].tolist())
    calendar = sorted(all_dates)

    aligned: dict[str, pd.DataFrame] = {}
    for ticker, df in prices.items():
        df = df.set_index("date").reindex(calendar)
        price_cols = ["adj_open", "adj_high", "adj_low", "adj_close"]
        df[price_cols] = df[price_cols].ffill(limit=3)
        df["volume"] = df["volume"].fillna(0)
        df = df.dropna(subset=["adj_close"])
        aligned[ticker] = df.reset_index().rename(columns={"index": "date"})

    return calendar, aligned


# ---------------------------------------------------------------------------
# Fundamental data (stock_data_1y/fundamentals_quarterly)
# ---------------------------------------------------------------------------

_INCOME_COLS = {
    "Total Revenue": "revenue",
    "Net Income Common Stockholders": "net_income",
    "Net Income": "net_income_alt",
    "Operating Income": "operating_income",
    "Total Operating Income As Reported": "operating_income_alt",
}

_BALANCE_COLS = {
    "Total Assets": "total_assets",
    "Total Debt": "total_debt",
    "Stockholders Equity": "stockholders_equity",
    "Common Stock Equity": "common_equity",
}

_CASHFLOW_COLS = {
    "Free Cash Flow": "free_cash_flow",
    "Operating Cash Flow": "operating_cash_flow",
}

AVAILABLE_AT_OFFSET_DAYS = 45


def _rename_and_select(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    rename = {}
    for original, target in mapping.items():
        if original in df.columns:
            rename[original] = target
    return df.rename(columns=rename)[[c for c in rename.values() if c in df.rename(columns=rename).columns]]


def _load_statement(path: Path, col_map: dict[str, str]) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["period_end"])
    present = {orig: tgt for orig, tgt in col_map.items() if orig in df.columns}
    if not present:
        return None
    out = df[["period_end"]].copy()
    for orig, tgt in present.items():
        out[tgt] = pd.to_numeric(df[orig], errors="coerce")
    return out


def load_fundamentals(
    data_root: str | Path,
    tickers: list[str],
) -> pd.DataFrame:
    fund_dir = Path(data_root) / "fundamentals_quarterly"
    frames: list[pd.DataFrame] = []

    for ticker in tickers:
        ticker_dir = fund_dir / ticker
        if not ticker_dir.exists():
            continue

        inc = _load_statement(ticker_dir / "income_statement.csv", _INCOME_COLS)
        bal = _load_statement(ticker_dir / "balance_sheet.csv", _BALANCE_COLS)
        cf = _load_statement(ticker_dir / "cash_flow.csv", _CASHFLOW_COLS)

        merged = None
        for part in [inc, bal, cf]:
            if part is None:
                continue
            if merged is None:
                merged = part
            else:
                merged = merged.merge(part, on="period_end", how="outer")

        if merged is None or merged.empty:
            continue

        merged["ticker"] = ticker
        merged["available_at"] = merged["period_end"] + pd.Timedelta(days=AVAILABLE_AT_OFFSET_DAYS)
        merged["estimated_availability"] = True

        if "net_income" not in merged.columns and "net_income_alt" in merged.columns:
            merged["net_income"] = merged["net_income_alt"]
        if "operating_income" not in merged.columns and "operating_income_alt" in merged.columns:
            merged["operating_income"] = merged["operating_income_alt"]
        if "stockholders_equity" not in merged.columns and "common_equity" in merged.columns:
            merged["stockholders_equity"] = merged["common_equity"]

        for col in ["net_income_alt", "operating_income_alt", "common_equity"]:
            if col in merged.columns:
                merged = merged.drop(columns=[col])

        frames.append(merged)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# RL training data
# ---------------------------------------------------------------------------

def load_rl_training_data(
    data_root: str | Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    root = Path(data_root)
    train = pd.read_csv(root / "train.csv", parse_dates=["date"])
    validation = pd.read_csv(root / "validation.csv", parse_dates=["date"])
    with open(root / "metadata.json", encoding="utf-8") as f:
        metadata = json.load(f)
    return train, validation, metadata
