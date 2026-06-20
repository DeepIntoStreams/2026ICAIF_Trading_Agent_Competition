from pathlib import Path
from datetime import datetime
import json

import pandas as pd
import yfinance as yf


# Deliberately different from the 30-stock evaluation universe.
TRAINING_UNIVERSE = {
    "technology": [
        "AMD", "AVGO", "ORCL", "IBM", "QCOM", "TXN", "AMAT", "MU", "ADI", "NOW"
    ],
    "finance": [
        "C", "WFC", "MS", "AXP", "MA", "BLK", "SCHW", "COF", "USB", "PNC"
    ],
    "healthcare": [
        "MRK", "ABBV", "ABT", "BMY", "GILD", "AMGN", "CVS", "CI", "MDT", "ISRG"
    ],
    "consumer": [
        "COST", "HD", "LOW", "MCD", "SBUX", "PEP", "PG", "CL", "TGT", "GM"
    ],
    "industrial_energy": [
        "DE", "HON", "RTX", "LMT", "UPS", "FDX", "COP", "SLB", "EOG", "MPC"
    ],
    "utilities_real_assets": [
        "DUK", "SO", "AEP", "EXC", "AMT", "PLD", "FCX", "NUE", "SHW", "OXY"
    ],
}

EVALUATION_UNIVERSE = {
    "AAPL", "MSFT", "NVDA", "INTC", "CRM",
    "JPM", "BAC", "GS", "V", "PYPL",
    "LLY", "JNJ", "UNH", "PFE", "TMO",
    "AMZN", "TSLA", "WMT", "NKE", "KO",
    "CAT", "GE", "BA", "XOM", "CVX",
    "GOOGL", "META", "DIS", "T", "NEE",
}

OUTPUT_DIR = Path("rl_training_data")
YEARS_OF_HISTORY = 3
VALIDATION_TRADING_DAYS = 252
EMBARGO_CALENDAR_DAYS = 120
MIN_HISTORY_RATIO = 0.80


def choose_dates() -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    # The evaluation interval is assumed to be the most recent complete year.
    evaluation_start = pd.Timestamp.today().normalize() - pd.DateOffset(years=1)
    download_end = evaluation_start - pd.Timedelta(days=EMBARGO_CALENDAR_DAYS)
    download_start = download_end - pd.DateOffset(years=YEARS_OF_HISTORY)
    return download_start, download_end, evaluation_start


def to_long_frame(
    downloaded: pd.DataFrame,
    symbols: list[str],
    sector_by_symbol: dict[str, str],
) -> pd.DataFrame:
    frames = []

    for symbol in symbols:
        try:
            frame = downloaded[symbol].dropna(how="all").copy()
        except KeyError:
            print(f"[warning] No downloaded columns for {symbol}")
            continue

        frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns):
            print(f"[warning] Missing OHLCV fields for {symbol}")
            continue

        frame = frame.reset_index()
        frame = frame.rename(columns={frame.columns[0]: "date"})
        frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)
        frame.insert(1, "symbol", symbol)
        frame.insert(2, "sector_group", sector_by_symbol[symbol])
        frames.append(frame[[
            "date", "symbol", "sector_group", "open", "high", "low", "close", "volume"
        ]])

    if not frames:
        raise RuntimeError("Yahoo returned no usable data.")

    return pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])


def filter_incomplete_symbols(data: pd.DataFrame) -> pd.DataFrame:
    total_dates = data["date"].nunique()
    counts = data.groupby("symbol")["date"].nunique()
    keep = counts[counts >= total_dates * MIN_HISTORY_RATIO].index
    removed = sorted(set(data["symbol"]) - set(keep))
    if removed:
        print(f"[warning] Removed incomplete symbols: {removed}")
    return data[data["symbol"].isin(keep)].copy()


def add_basic_features(data: pd.DataFrame) -> pd.DataFrame:
    data = data.sort_values(["symbol", "date"]).copy()
    grouped = data.groupby("symbol", group_keys=False)

    data["return_1d"] = grouped["close"].pct_change()
    data["return_5d"] = grouped["close"].pct_change(5)
    data["return_20d"] = grouped["close"].pct_change(20)
    data["momentum_60d"] = grouped["close"].pct_change(60)
    data["volatility_20d"] = grouped["return_1d"].transform(
        lambda series: series.rolling(20, min_periods=20).std()
    )
    data["sma_50"] = grouped["close"].transform(
        lambda series: series.rolling(50, min_periods=50).mean()
    )
    data["sma_distance_50"] = data["close"] / data["sma_50"] - 1
    data["volume_ma_20"] = grouped["volume"].transform(
        lambda series: series.rolling(20, min_periods=20).mean()
    )
    data["volume_ratio_20"] = data["volume"] / data["volume_ma_20"]
    return data


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    sector_by_symbol = {
        symbol: sector
        for sector, symbols in TRAINING_UNIVERSE.items()
        for symbol in symbols
    }
    symbols = list(sector_by_symbol)

    overlap = set(symbols) & EVALUATION_UNIVERSE
    if overlap:
        raise ValueError(f"Training/evaluation overlap: {sorted(overlap)}")

    start, end, evaluation_start = choose_dates()
    print(f"Downloading {len(symbols)} stocks: {start.date()} to {end.date()}")

    downloaded = yf.download(
        symbols,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1d",
        auto_adjust=True,
        actions=False,
        group_by="ticker",
        threads=True,
        progress=True,
    )

    data = to_long_frame(downloaded, symbols, sector_by_symbol)
    data = filter_incomplete_symbols(data)
    data = add_basic_features(data)

    dates = sorted(data["date"].drop_duplicates())
    if len(dates) <= VALIDATION_TRADING_DAYS + 126:
        raise RuntimeError("Not enough trading days for train/validation split.")

    validation_start = dates[-VALIDATION_TRADING_DAYS]
    train = data[data["date"] < validation_start].copy()
    validation = data[data["date"] >= validation_start].copy()

    data.to_csv(OUTPUT_DIR / "all_market.csv", index=False)
    train.to_csv(OUTPUT_DIR / "train.csv", index=False)
    validation.to_csv(OUTPUT_DIR / "validation.csv", index=False)

    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "download_start": start.date().isoformat(),
        "download_end": end.date().isoformat(),
        "evaluation_start_assumption": evaluation_start.date().isoformat(),
        "validation_start": pd.Timestamp(validation_start).date().isoformat(),
        "embargo_calendar_days": EMBARGO_CALENDAR_DAYS,
        "symbols": sorted(data["symbol"].unique().tolist()),
        "num_symbols": int(data["symbol"].nunique()),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "recommended_training_episode": {
            "num_assets": 30,
            "num_trading_days": 126,
        },
    }
    with (OUTPUT_DIR / "metadata.json").open("w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)

    print(f"Train:      {len(train):,} rows")
    print(f"Validation: {len(validation):,} rows")
    print(f"Saved to:   {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
