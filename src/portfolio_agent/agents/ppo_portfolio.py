"""PPO-trained portfolio agent.

Architecture per the technical report:
  features[N,F] -> shared MLP -> embeddings[N,D]
  embeddings -> self-attention -> asset logits[N]
  concat(asset logits, cash logit) -> softmax -> cap projection

The policy is independent of ticker names and asset ordering because
every asset passes through the same shared encoder.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .base import BaseAgent
from portfolio_agent.news.sentiment import NEGATIVE_WORDS, POSITIVE_WORDS
from portfolio_agent.risk import sanitize_target_weights

logger = logging.getLogger(__name__)

MARKET_FEATURES = [
    "return_1d",
    "return_5d",
    "return_20d",
    "momentum_60d",
    "volatility_20d",
    "sma_distance_50",
    "volume_ratio_20",
]

FUNDAMENTAL_FEATURES = [
    "net_margin",
    "fcf_margin",
    "debt_to_assets",
    "roe",
]

FRESHNESS_FEATURES = [
    "report_age_days",
]

# 7 market + 4 fundamental + 1 freshness + 1 current weight = 13
FEATURE_DIM = len(MARKET_FEATURES) + len(FUNDAMENTAL_FEATURES) + len(FRESHNESS_FEATURES) + 1


class PortfolioPolicy(nn.Module):
    """Shared-encoder + self-attention policy for portfolio allocation."""

    def __init__(
        self,
        feature_dim: int = FEATURE_DIM,
        embed_dim: int = 64,
        n_heads: int = 4,
    ):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(feature_dim, 128),
            nn.ReLU(),
            nn.Linear(128, embed_dim),
            nn.ReLU(),
        )

        self.attention = nn.MultiheadAttention(
            embed_dim=embed_dim,
            num_heads=n_heads,
            batch_first=True,
        )
        self.attn_norm = nn.LayerNorm(embed_dim)

        self.policy_head = nn.Linear(embed_dim, 1)
        self.cash_logit = nn.Parameter(torch.zeros(1))

        self.value_head = nn.Sequential(
            nn.Linear(embed_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )

    def forward(
        self,
        features: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            features: [batch, num_assets, feature_dim]
        Returns:
            log_probs: [batch, num_assets + 1] (assets + cash)
            value: [batch, 1]
        """
        embeddings = self.encoder(features)                  # [B, N, D]
        attended, _ = self.attention(embeddings, embeddings, embeddings)
        attended = self.attn_norm(attended + embeddings)      # [B, N, D]

        asset_logits = self.policy_head(attended).squeeze(-1) # [B, N]
        B = asset_logits.shape[0]
        cash = self.cash_logit.expand(B, 1)                   # [B, 1]
        logits = torch.cat([asset_logits, cash], dim=-1)       # [B, N+1]
        log_probs = F.log_softmax(logits, dim=-1)

        pooled = attended.mean(dim=1)                          # [B, D]
        value = self.value_head(pooled)                        # [B, 1]

        return log_probs, value

    def get_weights(
        self,
        features: torch.Tensor,
        max_weight: float = 0.30,
    ) -> torch.Tensor:
        """Deterministic weight extraction with cap projection."""
        with torch.no_grad():
            log_probs, _ = self.forward(features)
            probs = log_probs.exp()                     # [B, N+1]
            asset_probs = probs[:, :-1]                 # [B, N]
            asset_probs = asset_probs.clamp(max=max_weight)
            total = asset_probs.sum(dim=-1, keepdim=True)
            over = (total > 1.0).float()
            asset_probs = asset_probs * (1.0 - over) + (
                asset_probs / total.clamp(min=1e-8)
            ) * over
            return asset_probs


def observation_to_tensor(
    observation: dict[str, Any],
    asset_order: list[str] | None = None,
) -> tuple[torch.Tensor, list[str]]:
    """Convert agent-safe observation to a feature tensor.

    Returns:
        features: [1, N, F] tensor
        asset_ids: ordered list of asset IDs matching dim 1
    """
    market = observation.get("market_features", {})
    fund = observation.get("fundamental_features", {})
    portfolio = observation.get("portfolio", {})
    weights = portfolio.get("weights", {})

    if asset_order is None:
        asset_order = sorted(market.keys())

    rows: list[list[float]] = []
    for aid in asset_order:
        mf = market.get(aid, {})
        ff = fund.get(aid, {})
        row: list[float] = []

        for key in MARKET_FEATURES:
            val = mf.get(key)
            row.append(0.0 if val is None or not math.isfinite(val) else val)

        for key in FUNDAMENTAL_FEATURES:
            val = ff.get(key)
            row.append(0.0 if val is None or not math.isfinite(val) else val)

        for key in FRESHNESS_FEATURES:
            val = ff.get(key)
            scaled = 0.0
            if val is not None and math.isfinite(val):
                scaled = val / 365.0
            row.append(scaled)

        w = weights.get(aid, 0.0)
        row.append(w if math.isfinite(w) else 0.0)

        rows.append(row)

    tensor = torch.tensor([rows], dtype=torch.float32)
    return tensor, asset_order


class PPOPortfolioAgent(BaseAgent):
    """PPO-trained agent that loads a frozen checkpoint for evaluation."""

    def __init__(
        self,
        checkpoint_path: str | Path | None = None,
        max_asset_weight: float = 0.30,
        feature_dim: int = FEATURE_DIM,
        embed_dim: int = 64,
        n_heads: int = 4,
    ):
        self.max_asset_weight = max_asset_weight
        self.policy = PortfolioPolicy(
            feature_dim=feature_dim,
            embed_dim=embed_dim,
            n_heads=n_heads,
        )
        self.policy.eval()

        if checkpoint_path is not None:
            ckpt_path = Path(checkpoint_path)
            if ckpt_path.exists():
                state = torch.load(ckpt_path, map_location="cpu", weights_only=False)
                try:
                    if "policy_state_dict" in state:
                        self.policy.load_state_dict(state["policy_state_dict"])
                    else:
                        self.policy.load_state_dict(state)
                    self.policy.eval()
                except RuntimeError as e:
                    ckpt_dim = state.get("feature_dim", "unknown")
                    logger.error(
                        "Failed to load checkpoint %s (feature_dim=%s, "
                        "expected=%d): %s — using random weights",
                        ckpt_path, ckpt_dim, feature_dim, e,
                    )
            else:
                logger.warning(
                    "PPO checkpoint not found at %s — using random weights", ckpt_path
                )

        self._asset_order: list[str] | None = None

    def reset(self) -> None:
        self._asset_order = None

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        features, asset_order = observation_to_tensor(
            observation, self._asset_order
        )

        if self._asset_order is None:
            self._asset_order = asset_order

        weights = self.policy.get_weights(features, self.max_asset_weight)
        w_np = weights.squeeze(0).numpy()

        result: dict[str, float] = {}
        for i, aid in enumerate(asset_order):
            if w_np[i] > 1e-6:
                result[aid] = float(w_np[i])

        return result


def _news_stats_from_observation(
    observation: dict[str, Any],
    asset_order: list[str],
) -> tuple[dict[str, float], dict[str, int]]:
    sentiment = {asset_id: 0.0 for asset_id in asset_order}
    counts = {asset_id: 0 for asset_id in asset_order}

    for item in observation.get("news", []):
        ticker = item.get("ticker")
        tickers = item.get("tickers") or ([ticker] if ticker else [])
        text = f"{item.get('headline', '')} {item.get('summary', '')}".lower()
        words = [part.strip(".,;:!?()[]{}\"'").lower() for part in text.split()]
        score = (
            sum(1 for word in words if word in POSITIVE_WORDS)
            - sum(1 for word in words if word in NEGATIVE_WORDS)
        )
        for asset_id in tickers:
            if asset_id in sentiment:
                sentiment[asset_id] += score
                counts[asset_id] += 1

    for asset_id in asset_order:
        if counts[asset_id] > 0:
            sentiment[asset_id] /= counts[asset_id]
    return sentiment, counts


class NewsTiltedPPOAgent(PPOPortfolioAgent):
    """PPO allocation wrapper with deterministic news tilt."""

    def __init__(
        self,
        news_beta: float = 0.30,
        news_count_gamma: float = 0.05,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.news_beta = news_beta
        self.news_count_gamma = news_count_gamma
        self.last_pre_tilt_weights: dict[str, float] = {}
        self.last_post_tilt_weights: dict[str, float] = {}

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        base_weights = super().decide(observation)
        asset_order = self._asset_order or sorted(
            observation.get("market_features", {}).keys()
        )
        if not asset_order:
            return {}

        sentiment, counts = _news_stats_from_observation(observation, asset_order)
        epsilon = 1e-8
        logits = []
        for asset_id in asset_order:
            base_weight = base_weights.get(asset_id, 0.0)
            logits.append(
                math.log(base_weight + epsilon)
                + self.news_beta * sentiment[asset_id]
                + self.news_count_gamma * math.log(1 + counts[asset_id])
            )

        max_logit = max(logits)
        exps = np.exp(np.array(logits) - max_logit)
        total = float(np.sum(exps))
        tilted = {
            asset_id: float(exps[index] / total)
            for index, asset_id in enumerate(asset_order)
            if total > 0
        }
        sanitized, _ = sanitize_target_weights(
            tilted,
            allowed_assets=set(asset_order),
            max_asset_weight=self.max_asset_weight,
            max_gross_exposure=1.0,
        )
        self.last_pre_tilt_weights = dict(base_weights)
        self.last_post_tilt_weights = dict(sanitized)
        return sanitized
