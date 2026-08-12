# Results summary - README next-steps #1, #4, #5 + 6-month backtest

(Numbering follows the README "Next steps" list: #1 evaluation horizon, #4 value of news,
#5 different LLMs / prior-knowledge bias. Steps #2, #3, #6 are covered in a separate summary.)

**Setup.** Eval universe = 30 tickers (6 sectors). Price data 2025-06-20 -> 2026-06-18.
News store = `data/news/backfill_20260605_20260618` (covers 2026-05-05 -> 06-18 only,
~6 weeks). Seed 1, LLM = `google/gemma-4-31B-it` @ temp 0.0 (deterministic, single seed).
PPO = policy trained on the RL corpus (`models/ppo_checkpoint.pt`, best val_reward
+0.0628 at iter 100); news enters PPO only as a deterministic post-policy tilt.

Two facts to keep in mind while reading: the 6-month runs are **news-off** (news covers
only the last ~6 weeks of that window), and the LLM is **single-seed / deterministic**
(one draw, not an expectation).

---

## Headline: 6-month backtest (126 trading days, 2025-12-17 -> 2026-06-18, news-off)

Real sample (n=126, **no** low-sample warning). Benchmark = naive equal-weight buy-&-hold.

| Agent | Cum. return | Sharpe | MDD | Turnover |
|---|---|---|---|---|
| Rule-based (hybrid) | +6.19% | 0.96 | 6.51% | 32.2 |
| PPO (trained) | **+10.27%** | **1.63** | 8.32% | **6.9** |
| LLM | **+12.51%** | 1.15 | 10.85% | 34.6 |
| **Buy & hold (equal-weight)** | **+13.52%** | - | - | ~0 |

**The key finding: no strategy beat naive equal-weight buy-and-hold (+13.52%).** The LLM
came closest (+12.5%) but with the worst drawdown and highest turnover. PPO is the most
*efficient* - 82% of the LLM's return at 1/5 the turnover and the best Sharpe.

### Why the benchmark is +13.52% - read the distribution, not the mean

| | Value |
|---|---|
| Equal-weight **mean** | +13.52% |
| **Median** asset | +6.88% |
| Best | INTC **+271.68%** |
| Worst | CRM -40.81% |
| Positive | 20 / 30 assets |

The mean is inflated almost entirely by **INTC +272%**. Drop that one name and the other
29 average ~ +4.6%. So the *typical* asset made ~6.9%, which the rule-based agent roughly
matched and PPO/LLM beat. The whole "who wins" story is sensitive to one outlier stock.

---

## Exp #1 - Decide a good evaluation horizon (horizon sweep, cumulative return %, with news)

Nested windows, all ending 2026-06-18 (h=20 contains h=14 contains h=10 contains h=5).
`ret%` = total over the window; Sharpe/Sortino in the source files are annualized (sqrt252),
so do **not** compare the two on short horizons.

| Trading days | Window | hybrid | ppo | llm |
|---|---|---|---|---|
| 5 | 06-12 -> 06-18 | +0.83 | -0.09 | +0.99 |
| 10 | 06-05 -> 06-18 | +1.47 | +1.27 | +1.69 |
| 14 | 06-01 -> 06-18 | -0.17 | -0.55 | -0.47 |
| 20 | 05-21 -> 06-18 | -1.12 | -0.00 | -3.91 |

**Ranking is unstable.** LLM leads at 5/10 days, but at 20 days it is worst (-3.91) and
PPO is best (~0). Returns flip sign at h=14 because the added earlier days were a down
stretch. The winner at any single horizon is decided by which few days you happen to include.

**Suggestion: don't pick one horizon - average across horizons 5-20.** Rather than commit
to a single evaluation length and inherit its luck, report each agent's metrics *averaged
over the 5/10/14/20-day settings*, which smooths out the window-specific noise:

| Agent | Mean ret% (5-20) | Stdev across horizons | Mean Sharpe |
|---|---|---|---|
| hybrid | +0.25 | 0.98 | 2.01 |
| ppo | +0.16 | 0.67 | 0.42 |
| llm | -0.43 | 2.16 | 1.55 |

The averaged view tells a steadier story than any single row: hybrid and PPO are roughly
flat-but-stable (low stdev), while the LLM has the highest across-horizon volatility (2.16)
- i.e. its edge is the least robust to how long you evaluate. We'd suggest the competition
adopt a **multi-horizon average** (and report the stdev as a stability score) rather than a
single fixed horizon. Methodological note: these four windows are *nested* (all end
2026-06-18), so the average is a robustness summary over evaluation lengths, not four
independent periods; a stricter version would use non-overlapping windows.

---

## Exp #4 - Quantify the value of news, 10-day window (2026-06-05 -> 06-18)

| Agent | Metric | With news | No news | delta |
|---|---|---|---|---|
| hybrid | ret% | 1.47 | 1.28 | +0.19 |
| hybrid | Sharpe | 3.57 | 2.54 | +1.03 |
| hybrid | MDD% | 1.15 | 1.97 | -0.82 (better) |
| hybrid | turnover | 5.51 | 3.11 | +2.40 |
| ppo | ret% | 1.27 | 1.32 | -0.05 |
| ppo | Sharpe | 2.54 | 2.58 | -0.04 |
| ppo | MDD% | 1.20 | 1.24 | -0.04 (better) |
| ppo | turnover | 2.09 | 1.34 | +0.75 |
| llm | ret% | 1.69 | 1.78 | -0.09 |
| llm | Sharpe | 2.56 | 2.79 | -0.23 |
| llm | MDD% | 2.47 | 2.14 | +0.33 (worse) |
| llm | turnover | 3.35 | 2.96 | +0.39 |

At 10 days: news helps **hybrid** (by design, via its 0.20 sentiment weight, at 2x turnover),
is ~neutral for **PPO** (news-blind policy), and slightly *hurts* the **LLM**.

## Exp #4b - Value of news, full news-covered window (2026-05-05 -> 06-18, 32 days)

Biggest clean news sample available (n=32, **no** low-sample warning).

| Agent | With news | No news | delta (news effect) |
|---|---|---|---|
| hybrid | +0.85% | +0.35% | +0.49 |
| ppo | +1.31% | +1.56% | -0.25 |
| llm | -1.01% | -7.27% | **+6.26** |

**The news effect on the LLM flips sign between windows** - slightly negative at 10 days,
strongly positive here (+6.3pp, cutting a -7.3% loss to -1.0%). This instability is the
real finding: one window is not evidence. Diagnosis of *why* the LLM often fails to gain
from news (from inspecting `news_seen.jsonl`): the feed is ~80% Yahoo aggregator headlines,
many market-wide ("Which S&P 500 stocks are moving Friday?"), and ticker mapping is
unreliable (a Northrop headline tagged BA; a SpaceX headline tagged T; an Ackman-on-MSFT
headline tagged CRM). Low signal + mis-tagging + strong model priors -> news rarely moves
the LLM in a short window. Points straight at README step #2 (better sources, ticker
mapping).

---

## Exp #5 - Test different LLMs / prior-knowledge bias (identity ablation: real vs neutral aliases), 10 days

Identical window/seed/prompt/numbers; only asset identity differs (AAPL/"Apple Inc." vs
ASSET_07/"Company 07"). News off in both arms. `scripts/exp3_identity_ablation.py`.

| Metric | Real | Alias | delta |
|---|---|---|---|
| Cumulative return % | 1.78 | 1.74 | -0.04 |
| Daily win rate % | 60.0 | 60.0 | 0.00 |
| Sharpe | 2.79 | 2.84 | +0.05 |
| Sortino | 3.27 | 3.19 | -0.08 |
| Max drawdown % | 2.14 | 2.24 | +0.10 |
| VaR95 % | 1.12 | 1.15 | +0.03 |
| ES95 % | 1.49 | 1.35 | -0.14 |
| **Turnover** | **2.96** | **3.80** | **+0.84** |
| Violation rate % | 10.0 | 10.0 | 0.00 |

Decision-level divergence (from diffing `actions.jsonl`, not in the metrics files):
- Mean daily allocation L1 difference = **0.355** (~35% of the portfolio reshuffled).
- **0 of 10 days** produced identical holdings.
- Turnover +28% under aliasing.

**Interpretation.** Every *outcome* metric is nearly identical, but the *decisions* are not:
hiding identity reshuffles ~35% of the book and trades 28% more, yet lands on the same
return. So the LLM **does** trade on company identity (prior knowledge), and in this
window that prior was roughly performance-neutral. This is the publishable nuance - the
competition can allow names, but should report an identity-ablation number, because the
effect is large on decisions even when small on returns, and another market window could
make it matter (cf. the 6-month LLM edge coming largely from avoiding known losers).

