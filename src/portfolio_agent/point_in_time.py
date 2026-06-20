"""Point-in-time fundamental selection and derived ratio computation."""

from __future__ import annotations

import pandas as pd


def latest_available_fundamentals(
    fundamentals: pd.DataFrame,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    """Return the latest report version available by cutoff for each ticker.

    Multiple versions of a period are allowed; later restatements are invisible
    before their own available_at timestamps.
    """
    required = {"ticker", "period_end", "available_at"}
    missing = required - set(fundamentals.columns)
    if missing:
        raise ValueError(f"Missing fundamental columns: {sorted(missing)}")

    frame = fundamentals.copy()
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
    cutoff = pd.Timestamp(cutoff)
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    frame = frame[frame["available_at"] <= cutoff]
    if frame.empty:
        return frame

    frame = frame.sort_values(["ticker", "period_end", "available_at"])
    return frame.groupby("ticker", as_index=False).tail(1).reset_index(drop=True)


def derive_fundamental_ratios(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert raw amounts into lower-identifiability ratios and growth fields."""
    result = frame[["ticker", "period_end", "available_at"]].copy()

    revenue = pd.to_numeric(frame.get("revenue"), errors="coerce").replace(0, pd.NA)
    assets = pd.to_numeric(frame.get("total_assets"), errors="coerce").replace(0, pd.NA)
    equity = pd.to_numeric(frame.get("stockholders_equity"), errors="coerce").replace(0, pd.NA)
    net_income = pd.to_numeric(frame.get("net_income"), errors="coerce")
    operating_income = pd.to_numeric(frame.get("operating_income"), errors="coerce")
    ocf = pd.to_numeric(frame.get("operating_cash_flow"), errors="coerce")
    fcf = pd.to_numeric(frame.get("free_cash_flow"), errors="coerce")
    debt = pd.to_numeric(frame.get("total_debt"), errors="coerce")

    result["net_margin"] = net_income / revenue
    result["operating_margin"] = operating_income / revenue
    result["fcf_margin"] = fcf / revenue
    result["debt_to_assets"] = debt / assets
    result["roe"] = net_income / equity
    result["cash_conversion"] = ocf / net_income.replace(0, pd.NA)

    return result


def compute_yoy_growth(
    fundamentals: pd.DataFrame,
    cutoff: pd.Timestamp,
) -> dict[str, float | None]:
    """Compute year-over-year revenue growth for each ticker.

    Compares the latest available quarter to the same-quarter one year ago.
    """
    latest = latest_available_fundamentals(fundamentals, cutoff)
    if latest.empty or "revenue" not in latest.columns:
        return {}

    result: dict[str, float | None] = {}
    for _, row in latest.iterrows():
        ticker = row["ticker"]
        current_revenue = row.get("revenue")
        current_period = row.get("period_end")

        if pd.isna(current_revenue) or pd.isna(current_period) or current_revenue == 0:
            result[ticker] = None
            continue

        prior_end = pd.Timestamp(current_period) - pd.DateOffset(years=1)
        prior_cutoff = pd.Timestamp(cutoff) - pd.DateOffset(years=1)
        if prior_cutoff.tzinfo is None:
            prior_cutoff = prior_cutoff.tz_localize("UTC")

        ticker_data = fundamentals[fundamentals["ticker"] == ticker].copy()
        ticker_data["available_at"] = pd.to_datetime(ticker_data["available_at"], utc=True)
        ticker_data["period_end"] = pd.to_datetime(ticker_data["period_end"])
        prior_visible = ticker_data[ticker_data["available_at"] <= prior_cutoff]

        if prior_visible.empty:
            result[ticker] = None
            continue

        period_diffs = (prior_visible["period_end"] - prior_end).abs()
        min_diff = period_diffs.min()
        closest_rows = prior_visible[period_diffs == min_diff]
        if "available_at" in closest_rows.columns:
            closest_rows = closest_rows.sort_values("available_at")
        prior_revenue = closest_rows.iloc[-1]["revenue"]

        if pd.isna(prior_revenue) or prior_revenue == 0:
            result[ticker] = None
        else:
            result[ticker] = float(current_revenue / prior_revenue - 1.0)

    return result
