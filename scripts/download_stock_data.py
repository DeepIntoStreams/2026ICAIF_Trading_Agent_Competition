from pathlib import Path
import json
import time

import pandas as pd
import yfinance as yf


SECTORS = {
    "technology": ["AAPL", "MSFT", "NVDA", "INTC", "CRM"],
    "finance": ["JPM", "BAC", "GS", "V", "PYPL"],
    "healthcare": ["LLY", "JNJ", "UNH", "PFE", "TMO"],
    "consumer": ["AMZN", "TSLA", "WMT", "NKE", "KO"],
    "industrial_energy": ["CAT", "GE", "BA", "XOM", "CVX"],
    "communication_utilities": ["GOOGL", "META", "DIS", "T", "NEE"],
}

INFO_FIELDS = [
    "longName",
    "sector",
    "industry",
    "marketCap",
    "enterpriseValue",
    "trailingPE",
    "forwardPE",
    "priceToBook",
    "trailingEps",
    "forwardEps",
    "dividendYield",
    "profitMargins",
    "returnOnEquity",
    "debtToEquity",
    "totalRevenue",
    "netIncomeToCommon",
    "totalCash",
    "totalDebt",
]


def save_statement(statement: pd.DataFrame, path: Path) -> None:
    if statement is None or statement.empty:
        return
    statement.T.rename_axis("period_end").to_csv(path)


def main() -> None:
    output = Path("stock_data_1y")
    price_dir = output / "prices_daily"
    fundamental_dir = output / "fundamentals_quarterly"
    price_dir.mkdir(parents=True, exist_ok=True)
    fundamental_dir.mkdir(parents=True, exist_ok=True)

    sector_by_symbol = {
        symbol: sector
        for sector, symbols in SECTORS.items()
        for symbol in symbols
    }
    symbols = list(sector_by_symbol)

    # Download adjusted and unadjusted OHLCV in one batch.
    prices = yf.download(
        symbols,
        period="1y",
        interval="1d",
        auto_adjust=False,
        actions=True,
        group_by="ticker",
        threads=True,
        progress=True,
    )

    performance = []
    for symbol in symbols:
        try:
            frame = prices[symbol].dropna(how="all").copy()
            frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
            frame.insert(0, "symbol", symbol)
            frame.insert(1, "sector_group", sector_by_symbol[symbol])
            frame.to_csv(price_dir / f"{symbol}.csv")

            adjusted_close = frame["adj_close"].dropna()
            performance.append(
                {
                    "symbol": symbol,
                    "sector_group": sector_by_symbol[symbol],
                    "start_date": adjusted_close.index[0],
                    "end_date": adjusted_close.index[-1],
                    "start_adj_close": adjusted_close.iloc[0],
                    "end_adj_close": adjusted_close.iloc[-1],
                    "return_1y": adjusted_close.iloc[-1] / adjusted_close.iloc[0] - 1,
                }
            )
        except (KeyError, AttributeError) as exc:
            print(f"[price warning] {symbol}: {exc}")

    performance_frame = pd.DataFrame(performance)
    performance_frame["performance_group"] = pd.qcut(
        performance_frame["return_1y"].rank(method="first"),
        q=3,
        labels=["down", "stable", "up"],
    )
    performance_frame.sort_values("return_1y").to_csv(
        output / "performance_summary.csv", index=False
    )

    summaries = []
    for index, symbol in enumerate(symbols, start=1):
        print(f"[{index:02d}/{len(symbols)}] fundamentals: {symbol}")
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            summary = {
                "symbol": symbol,
                "sector_group": sector_by_symbol[symbol],
                **{field: info.get(field) for field in INFO_FIELDS},
            }
            summaries.append(summary)

            company_dir = fundamental_dir / symbol
            company_dir.mkdir(exist_ok=True)
            save_statement(ticker.quarterly_income_stmt, company_dir / "income_statement.csv")
            save_statement(ticker.quarterly_balance_sheet, company_dir / "balance_sheet.csv")
            save_statement(ticker.quarterly_cashflow, company_dir / "cash_flow.csv")
        except Exception as exc:
            print(f"[fundamental warning] {symbol}: {exc}")

        # Reduces the chance of Yahoo rate limiting repeated metadata requests.
        time.sleep(0.5)

    pd.DataFrame(summaries).to_csv(output / "fundamental_summary.csv", index=False)
    with (output / "universe.json").open("w", encoding="utf-8") as file:
        json.dump(SECTORS, file, indent=2)

    print(f"Done. Files saved under: {output.resolve()}")


if __name__ == "__main__":
    main()
