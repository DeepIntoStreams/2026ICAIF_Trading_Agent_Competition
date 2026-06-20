"""Train a PPO portfolio agent on the public RL training corpus.

Usage:
    python scripts/train_ppo.py --data_root rl_training_data --output models/ppo_checkpoint.pt

The training data must be completely disjoint from the evaluation universe.
No evaluation data is loaded or referenced.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portfolio_agent.agents.ppo_portfolio import (
    FEATURE_DIM,
    FUNDAMENTAL_FEATURES,
    MARKET_FEATURES,
    PortfolioPolicy,
)


# ---------------------------------------------------------------------------
# Training environment
# ---------------------------------------------------------------------------

FEATURE_COLS = [
    "return_1d", "return_5d", "return_20d", "momentum_60d",
    "volatility_20d", "sma_distance_50", "volume_ratio_20",
]


class PortfolioEnv:
    """Simulates portfolio allocation on public training data.

    Each episode samples num_assets symbols and episode_len consecutive days.
    The env follows the same execution model as the evaluator:
      - Observation from close(t)
      - Execution at open(t+1)
      - Valuation at close(t+1)
    """

    def __init__(
        self,
        data: pd.DataFrame,
        num_assets: int = 30,
        episode_len: int = 126,
        initial_cash: float = 1_000_000.0,
        fee_rate: float = 0.001,
        max_asset_weight: float = 0.30,
    ):
        self.data = data.copy()
        self.num_assets = num_assets
        self.episode_len = episode_len
        self.initial_cash = initial_cash
        self.fee_rate = fee_rate
        self.max_asset_weight = max_asset_weight

        self.symbols = sorted(data["symbol"].unique().tolist())
        self.dates = sorted(data["date"].unique().tolist())

        self._build_lookup()

    def _build_lookup(self) -> None:
        """Pre-index data for fast episode sampling."""
        self.sym_dates: dict[str, list] = {}
        self.sym_data: dict[str, pd.DataFrame] = {}
        for sym, grp in self.data.groupby("symbol"):
            grp = grp.sort_values("date").reset_index(drop=True)
            self.sym_dates[sym] = grp["date"].tolist()
            self.sym_data[sym] = grp

    def sample_episode(self) -> tuple[list[str], list, dict[str, pd.DataFrame]]:
        """Sample assets and a contiguous date window for one episode."""
        if len(self.symbols) < self.num_assets:
            chosen = self.symbols[:]
        else:
            chosen = random.sample(self.symbols, self.num_assets)

        common_dates: set | None = None
        for sym in chosen:
            sd = set(self.sym_dates[sym])
            common_dates = sd if common_dates is None else common_dates & sd

        if common_dates is None or len(common_dates) < self.episode_len + 1:
            return self.sample_episode()

        sorted_common = sorted(common_dates)
        max_start = len(sorted_common) - self.episode_len - 1
        if max_start < 0:
            return self.sample_episode()
        start_idx = random.randint(0, max_start)
        window_dates = sorted_common[start_idx: start_idx + self.episode_len + 1]

        episode_data: dict[str, pd.DataFrame] = {}
        for sym in chosen:
            df = self.sym_data[sym]
            mask = df["date"].isin(set(window_dates))
            episode_data[sym] = df[mask].sort_values("date").reset_index(drop=True)

        return chosen, window_dates, episode_data

    def run_episode(
        self,
        policy: PortfolioPolicy,
        device: torch.device,
    ) -> dict[str, list]:
        """Run one episode and collect trajectory data for PPO."""
        chosen, window_dates, episode_data = self.sample_episode()
        N = len(chosen)

        cash = self.initial_cash
        shares = np.zeros(N)
        nav = self.initial_cash
        max_nav = nav

        log_probs_list: list[torch.Tensor] = []
        values_list: list[torch.Tensor] = []
        rewards_list: list[float] = []
        features_list: list[torch.Tensor] = []

        for t in range(self.episode_len):
            weights = np.zeros(N)
            if nav > 0:
                for i, sym in enumerate(chosen):
                    df = episode_data[sym]
                    if t < len(df):
                        cl = df.iloc[t]["close"]
                        if pd.notna(cl) and cl > 0:
                            weights[i] = shares[i] * cl / nav

            feat_rows: list[list[float]] = []
            for i, sym in enumerate(chosen):
                df = episode_data[sym]
                row: list[float] = []
                for col in FEATURE_COLS:
                    if t < len(df) and col in df.columns:
                        val = df.iloc[t][col]
                        row.append(0.0 if pd.isna(val) or not math.isfinite(val) else float(val))
                    else:
                        row.append(0.0)
                for _ in FUNDAMENTAL_FEATURES:
                    row.append(0.0)
                row.append(weights[i])
                feat_rows.append(row)

            feat_tensor = torch.tensor([feat_rows], dtype=torch.float32, device=device)
            features_list.append(feat_tensor)

            log_probs, value = policy(feat_tensor)
            values_list.append(value.squeeze())

            probs = log_probs.exp().squeeze(0)  # [N+1]
            dist = torch.distributions.Categorical(probs)
            sampled = dist.sample()

            target_weights = probs[:-1].detach().cpu().numpy()
            target_weights = np.clip(target_weights, 0, self.max_asset_weight)
            tw_sum = target_weights.sum()
            if tw_sum > 1.0:
                target_weights /= tw_sum

            lp = log_probs.squeeze(0)
            action_log_prob = (lp[:-1] * torch.tensor(target_weights, device=device)).sum()
            action_log_prob = action_log_prob + lp[-1] * (1.0 - tw_sum)
            log_probs_list.append(action_log_prob)

            if t + 1 >= len(window_dates):
                rewards_list.append(0.0)
                break

            nav_open = cash
            opens: list[float] = []
            for i, sym in enumerate(chosen):
                df = episode_data[sym]
                if t + 1 < len(df):
                    op = df.iloc[t + 1]["open"]
                    if pd.notna(op) and op > 0:
                        nav_open += shares[i] * op
                        opens.append(op)
                    else:
                        opens.append(0.0)
                else:
                    opens.append(0.0)

            if nav_open <= 0:
                rewards_list.append(0.0)
                continue

            for i in range(N):
                if opens[i] <= 0:
                    continue
                target_val = target_weights[i] * nav_open
                target_sh = target_val / opens[i]
                delta = target_sh - shares[i]
                if delta < 0:
                    trade_val = abs(delta) * opens[i]
                    fee = trade_val * self.fee_rate
                    cash += trade_val - fee
                    shares[i] = target_sh

            total_buy = 0.0
            buy_list: list[tuple[int, float]] = []
            for i in range(N):
                if opens[i] <= 0:
                    continue
                target_val = target_weights[i] * nav_open
                target_sh = target_val / opens[i]
                delta = target_sh - shares[i]
                if delta > 0:
                    buy_list.append((i, delta))
                    total_buy += delta * opens[i] * (1 + self.fee_rate)

            scale = 1.0
            if total_buy > cash and total_buy > 0:
                scale = cash / total_buy

            for i, delta in buy_list:
                actual = delta * scale
                trade_val = actual * opens[i]
                fee = trade_val * self.fee_rate
                cash -= trade_val + fee
                shares[i] += actual

            if cash < -1e-6:
                cash = 0.0

            new_nav = cash
            for i, sym in enumerate(chosen):
                df = episode_data[sym]
                if t + 1 < len(df):
                    cl = df.iloc[t + 1]["close"]
                    if pd.notna(cl) and cl > 0:
                        new_nav += shares[i] * cl

            if new_nav <= 0:
                new_nav = 1e-6

            reward = math.log(new_nav / nav) if nav > 0 else 0.0
            max_nav = max(max_nav, new_nav)
            dd = new_nav / max_nav - 1.0

            lambda_dd = 0.5
            lambda_v = 0.1
            reward -= lambda_dd * max(0.0, -dd - 0.10)

            rewards_list.append(reward)
            nav = new_nav

        return {
            "log_probs": log_probs_list,
            "values": values_list,
            "rewards": rewards_list,
            "features": features_list,
        }


# ---------------------------------------------------------------------------
# PPO update
# ---------------------------------------------------------------------------

def compute_gae(
    rewards: list[float],
    values: list[torch.Tensor],
    gamma: float = 0.99,
    lam: float = 0.95,
) -> tuple[torch.Tensor, torch.Tensor]:
    T = len(rewards)
    advantages = torch.zeros(T)
    returns = torch.zeros(T)
    gae = 0.0

    for t in reversed(range(T)):
        if t == T - 1:
            next_val = 0.0
        else:
            next_val = values[t + 1].detach().item()
        cur_val = values[t].detach().item()
        delta = rewards[t] + gamma * next_val - cur_val
        gae = delta + gamma * lam * gae
        advantages[t] = gae
        returns[t] = gae + cur_val

    return advantages, returns


def ppo_update(
    policy: PortfolioPolicy,
    optimizer: optim.Optimizer,
    trajectories: list[dict[str, list]],
    clip_eps: float = 0.2,
    value_coef: float = 0.5,
    entropy_coef: float = 0.01,
    epochs: int = 4,
) -> dict[str, float]:
    all_old_lp: list[torch.Tensor] = []
    all_adv: list[torch.Tensor] = []
    all_ret: list[torch.Tensor] = []
    all_feat: list[torch.Tensor] = []

    for traj in trajectories:
        lps = traj["log_probs"]
        vals = traj["values"]
        rews = traj["rewards"]
        feats = traj["features"]

        T = min(len(lps), len(vals), len(rews), len(feats))
        if T == 0:
            continue

        advantages, returns = compute_gae(rews[:T], vals[:T])
        all_old_lp.extend([lp.detach() for lp in lps[:T]])
        all_adv.append(advantages)
        all_ret.append(returns)
        all_feat.extend(feats[:T])

    if not all_old_lp:
        return {"policy_loss": 0.0, "value_loss": 0.0}

    device = all_old_lp[0].device
    old_lp = torch.stack(all_old_lp)
    adv = torch.cat(all_adv).to(device)
    ret = torch.cat(all_ret).to(device)

    adv_std = adv.std()
    if adv_std > 1e-8:
        adv = (adv - adv.mean()) / adv_std

    total_policy_loss = 0.0
    total_value_loss = 0.0

    for _ in range(epochs):
        new_lps: list[torch.Tensor] = []
        new_vals: list[torch.Tensor] = []

        for feat in all_feat:
            log_p, val = policy(feat)
            probs = log_p.exp().squeeze(0)
            target_w = probs[:-1].detach()
            lp_sum = (log_p.squeeze(0)[:-1] * target_w).sum()
            lp_sum = lp_sum + log_p.squeeze(0)[-1] * (1.0 - target_w.sum())
            new_lps.append(lp_sum)
            new_vals.append(val.squeeze())

        new_lp = torch.stack(new_lps)
        new_val = torch.stack(new_vals)

        ratio = (new_lp - old_lp).exp()
        surr1 = ratio * adv
        surr2 = ratio.clamp(1.0 - clip_eps, 1.0 + clip_eps) * adv
        policy_loss = -torch.min(surr1, surr2).mean()

        value_loss = nn.functional.mse_loss(new_val, ret)

        loss = policy_loss + value_coef * value_loss

        optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(policy.parameters(), max_norm=0.5)
        optimizer.step()

        total_policy_loss += policy_loss.item()
        total_value_loss += value_loss.item()

    return {
        "policy_loss": total_policy_loss / epochs,
        "value_loss": total_value_loss / epochs,
    }


# ---------------------------------------------------------------------------
# Main training loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Train PPO portfolio agent")
    parser.add_argument("--data_root", type=str, default="rl_training_data")
    parser.add_argument("--output", type=str, default="models/ppo_checkpoint.pt")
    parser.add_argument("--num_iterations", type=int, default=200)
    parser.add_argument("--episodes_per_iter", type=int, default=8)
    parser.add_argument("--episode_len", type=int, default=126)
    parser.add_argument("--num_assets", type=int, default=30)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--embed_dim", type=int, default=64)
    parser.add_argument("--n_heads", type=int, default=4)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    print(f"Loading training data from {args.data_root}")
    train_df = pd.read_csv(Path(args.data_root) / "train.csv", parse_dates=["date"])

    for col in FEATURE_COLS:
        if col not in train_df.columns and col == "sma_distance_50":
            if "sma_distance_50" not in train_df.columns and "sma_50" in train_df.columns:
                train_df["sma_distance_50"] = train_df["close"] / train_df["sma_50"] - 1

    with open(Path(args.data_root) / "metadata.json", encoding="utf-8") as f:
        metadata = json.load(f)

    print(f"Training symbols: {metadata['num_symbols']}")
    print(f"Training rows: {metadata['train_rows']}")
    print(f"Episode config: {args.num_assets} assets x {args.episode_len} days")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    policy = PortfolioPolicy(
        feature_dim=FEATURE_DIM,
        embed_dim=args.embed_dim,
        n_heads=args.n_heads,
    ).to(device)

    optimizer = optim.Adam(policy.parameters(), lr=args.lr)

    env = PortfolioEnv(
        data=train_df,
        num_assets=args.num_assets,
        episode_len=args.episode_len,
    )

    val_df = pd.read_csv(Path(args.data_root) / "validation.csv", parse_dates=["date"])
    for col in FEATURE_COLS:
        if col not in val_df.columns and col == "sma_distance_50":
            if "sma_50" in val_df.columns:
                val_df["sma_distance_50"] = val_df["close"] / val_df["sma_50"] - 1
    val_env = PortfolioEnv(
        data=val_df,
        num_assets=args.num_assets,
        episode_len=min(args.episode_len, 63),
    )

    best_val_reward = -float("inf")

    print(f"\nStarting PPO training for {args.num_iterations} iterations\n")

    for iteration in range(1, args.num_iterations + 1):
        policy.train()
        trajectories: list[dict[str, list]] = []
        train_rewards: list[float] = []

        for _ in range(args.episodes_per_iter):
            traj = env.run_episode(policy, device)
            trajectories.append(traj)
            train_rewards.append(sum(traj["rewards"]))

        losses = ppo_update(policy, optimizer, trajectories)
        mean_train_reward = np.mean(train_rewards)

        if iteration % 10 == 0 or iteration == 1:
            policy.eval()
            val_rewards: list[float] = []
            for _ in range(4):
                traj = val_env.run_episode(policy, device)
                val_rewards.append(sum(traj["rewards"]))
            mean_val_reward = np.mean(val_rewards)

            print(
                f"Iter {iteration:4d} | "
                f"train_reward={mean_train_reward:+.4f} | "
                f"val_reward={mean_val_reward:+.4f} | "
                f"pi_loss={losses['policy_loss']:.4f} | "
                f"v_loss={losses['value_loss']:.4f}"
            )

            if mean_val_reward > best_val_reward:
                best_val_reward = mean_val_reward
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                torch.save(
                    {
                        "policy_state_dict": policy.state_dict(),
                        "iteration": iteration,
                        "val_reward": mean_val_reward,
                        "feature_dim": FEATURE_DIM,
                        "embed_dim": args.embed_dim,
                        "n_heads": args.n_heads,
                        "seed": args.seed,
                    },
                    output_path,
                )
                print(f"  -> Saved best checkpoint (val_reward={mean_val_reward:+.4f})")
        else:
            if iteration % 5 == 0:
                print(
                    f"Iter {iteration:4d} | "
                    f"train_reward={mean_train_reward:+.4f} | "
                    f"pi_loss={losses['policy_loss']:.4f} | "
                    f"v_loss={losses['value_loss']:.4f}"
                )

    print(f"\nTraining complete. Best validation reward: {best_val_reward:+.4f}")
    print(f"Checkpoint saved to: {args.output}")


if __name__ == "__main__":
    main()
