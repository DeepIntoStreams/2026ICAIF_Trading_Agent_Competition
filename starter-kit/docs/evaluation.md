# Evaluation and ranking

Official evaluation uses completed decision periods. Order every non-cancelled round chronologically as `k = 1, ..., K`:

- `B_k` is NAV immediately before round `k` executes.
- `E_k` is NAV at that period's endpoint, immediately before the next included rebalance.
- `B_1 = 1,000,000` and consecutive boundaries satisfy `E_k = B_(k+1)`.
- The final `E_K` is the phase's official closing valuation.

The current round's transaction fee is reflected in its period return. A daily close inside an overnight Round 7 is a drawdown observation, not another return. A period with no selected valid decision retains market movement and has zero traded notional; later duplicate receipts are not separate periods. The standard Official schedule has `K = 105`; cancelled rounds are excluded from `K`.

## Metrics

For each completed period:

$$
r_k=\frac{E_k}{B_k}-1
$$

**Cumulative Return** (higher is better):

$$
M1=\frac{E_K}{B_1}-1
$$

**Sharpe Ratio** (higher is better) uses sample standard deviation and a zero risk-free rate:

$$
\bar r=\frac{1}{K}\sum_{k=1}^{K}r_k,\qquad
s_r=\sqrt{\frac{1}{K-1}\sum_{k=1}^{K}(r_k-\bar r)^2},\qquad
M2=\sqrt{1764}\frac{\bar r}{s_r}
$$

The annualization factor remains `sqrt(252 x 7) = 42` when rounds are cancelled. Sharpe is zero for one completed period or zero volatility; zero completed periods produce no metrics.

**Maximum Drawdown** (lower is better) uses one chronological sequence containing initial NAV, every completed period endpoint, and every official daily close. Points sharing a timestamp are deduplicated, so the final close appears once. Do not add the next trade's post-fee NAV as another boundary. For sequence `V_0, ..., V_L`, with `V_0 = B_1`:

$$
P_m=\max_{0\le j\le m}V_j,\qquad
M3=\max_{1\le m\le L}\left(\frac{P_m-V_m}{P_m}\right)
$$

**Turnover** (lower is better) uses each round's total buy-plus-sell notional `N_k`, including the initial allocation:

$$
M4=\frac{1}{K}\sum_{k=1}^{K}\frac{N_k}{B_k}
$$

## Ranking and provisional results

Teams receive a rank for each metric; equal metric values receive the average of their occupied ranks. The Overall Rank Score is the mean of the four ranks, and lower is better. Equal Overall Rank Scores are resolved by higher Cumulative Return, higher Sharpe, lower Maximum Drawdown, then lower Turnover. Exact four-metric ties share competition positions, such as `1, 1, 3`; team ID orders tied display rows only.

Validation uses the same formulas over completed period endpoints only. Its `as_of` is the last included endpoint; `latest_valuation` can be newer without closing an unfinished period. Use the private metrics API to inspect Validation results, which are provisional and do not contribute to Official ranking. Official rank eligibility also requires final materials and organizer approval.

Run the offline calculator on the synthetic example or a saved private metrics response:

```console
python tools/evaluate.py examples/evaluation.json
python tools/evaluate.py output/validation-metrics.json
```

The calculator is for reconciliation. It does not reproduce the backend ledger, determine eligibility, or create an official rank.
