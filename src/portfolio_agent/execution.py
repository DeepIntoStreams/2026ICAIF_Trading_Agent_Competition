"""Close-price target-weight execution with fees and optional slippage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PortfolioState:
    cash: float
    shares: dict[str, float]

    def nav(self, prices: dict[str, float]) -> float:
        value = float(self.cash)
        for ticker, shares in self.shares.items():
            price = prices.get(ticker)
            if price is not None and price > 0:
                value += shares * price
        return value


@dataclass
class Trade:
    ticker: str
    direction: str
    shares_before: float
    shares_after: float
    shares_delta: float
    price: float
    trade_value: float
    fee: float
    buy_scale: float = 1.0


class ExecutionEngine:
    def __init__(self, fee_rate: float = 0.001, slippage_bps: float = 0.0):
        self.fee_rate = float(fee_rate)
        self.slippage_bps = float(slippage_bps)

    def _fill_price(self, close_price: float, direction: str) -> float:
        slip = self.slippage_bps / 10_000.0
        if direction == "buy":
            return close_price * (1.0 + slip)
        if direction == "sell":
            return close_price * (1.0 - slip)
        raise ValueError(f"Unknown trade direction: {direction}")

    def execute_close(
        self,
        state: PortfolioState,
        target_weights: dict[str, float],
        close_prices: dict[str, float],
    ) -> tuple[PortfolioState, list[Trade]]:
        cash = float(state.cash)
        shares = dict(state.shares)
        for ticker in close_prices:
            shares.setdefault(ticker, 0.0)

        nav_close = PortfolioState(cash=cash, shares=shares).nav(close_prices)
        trades: list[Trade] = []
        buy_orders: list[tuple[str, float, float]] = []

        for ticker, close_price in close_prices.items():
            if close_price <= 0:
                continue
            target_weight = float(target_weights.get(ticker, 0.0))
            target_value = target_weight * nav_close
            target_shares = target_value / close_price
            current_shares = shares.get(ticker, 0.0)
            delta = target_shares - current_shares
            if delta < -1e-12:
                fill_price = self._fill_price(close_price, "sell")
                trade_value = abs(delta) * fill_price
                fee = trade_value * self.fee_rate
                cash += trade_value - fee
                shares[ticker] = target_shares
                trades.append(
                    Trade(
                        ticker=ticker,
                        direction="sell",
                        shares_before=current_shares,
                        shares_after=target_shares,
                        shares_delta=delta,
                        price=fill_price,
                        trade_value=trade_value,
                        fee=fee,
                    )
                )
            elif delta > 1e-12:
                buy_orders.append((ticker, delta, close_price))

        total_buy_cost = 0.0
        for _, delta, close_price in buy_orders:
            fill_price = self._fill_price(close_price, "buy")
            total_buy_cost += delta * fill_price * (1.0 + self.fee_rate)

        buy_scale = 1.0
        if total_buy_cost > cash and total_buy_cost > 0:
            buy_scale = max(cash, 0.0) / total_buy_cost

        for ticker, delta, close_price in buy_orders:
            current_shares = shares.get(ticker, 0.0)
            actual_delta = delta * buy_scale
            fill_price = self._fill_price(close_price, "buy")
            trade_value = actual_delta * fill_price
            fee = trade_value * self.fee_rate
            cash -= trade_value + fee
            shares[ticker] = current_shares + actual_delta
            trades.append(
                Trade(
                    ticker=ticker,
                    direction="buy",
                    shares_before=current_shares,
                    shares_after=shares[ticker],
                    shares_delta=actual_delta,
                    price=fill_price,
                    trade_value=trade_value,
                    fee=fee,
                    buy_scale=buy_scale,
                )
            )

        if cash < 0 and cash > -1e-7:
            cash = 0.0

        return PortfolioState(cash=cash, shares=shares), trades
