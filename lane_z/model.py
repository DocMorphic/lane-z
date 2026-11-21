"""Causal local and cross-market features and train-only ridge regression."""

from dataclasses import dataclass

import numpy as np

from .data import BPS, US_PER_MS, Book


GRID_MS = 250
MAX_AGE_MS = 2_000
LOCAL_FEATURES = (
    "ret_250ms", "ret_1s", "ret_5s", "imbalance", "ofi_1s", "spread", "volatility_5s",
)
CROSS_FEATURES = (
    "index_ret_250ms", "index_ret_1s", "index_ret_5s", "index_ofi_1s",
    "peer_ret_250ms", "peer_ret_1s", "peer_ret_5s", "peer_ofi_1s",
    "index_target_gap_1s", "peer_target_gap_5s", "aligned_index_peer_1s",
    "large_target_dislocation_1s", "large_leader_impulse_1s",
)


def delta_bps(now, earlier):
    with np.errstate(invalid="ignore", divide="ignore"):
        return BPS * np.log(now / earlier)


@dataclass(frozen=True)
class Features:
    local: np.ndarray
    cross: np.ndarray
    mid: np.ndarray
    quote: np.ndarray


def local_features(book: Book, grid: np.ndarray):
    quotes, valid = book.asof(grid, MAX_AGE_MS * US_PER_MS)
    mid = np.where(valid, (quotes["ask"] + quotes["bid"]) / 2, np.nan)
    bid_size = quotes["bid_size"]
    ask_size = quotes["ask_size"]
    with np.errstate(invalid="ignore", divide="ignore"):
        imbalance = np.where(valid, (bid_size - ask_size) / (bid_size + ask_size), np.nan)
        spread = BPS * (quotes["ask"] - quotes["bid"]) / mid

    returns = {}
    for steps in (1, 4, 20):
        change = np.full(len(grid), np.nan)
        change[steps:] = delta_bps(mid[steps:], mid[:-steps])
        returns[steps] = change

    # Cont-style OFI on sampled best quotes. Repeated as-of quotes contribute zero.
    bid = quotes["bid"]
    ask = quotes["ask"]
    bid_flow = np.where(bid[1:] >= bid[:-1], bid_size[1:], 0.0)
    bid_flow -= np.where(bid[1:] <= bid[:-1], bid_size[:-1], 0.0)
    ask_flow = np.where(ask[1:] <= ask[:-1], ask_size[1:], 0.0)
    ask_flow -= np.where(ask[1:] >= ask[:-1], ask_size[:-1], 0.0)
    flow = np.full(len(grid), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        scaled = 2 * (bid_flow - ask_flow) / (bid_size[1:] + ask_size[1:] + bid_size[:-1] + ask_size[:-1])
    flow[1:] = np.where(valid[1:] & valid[:-1], scaled, np.nan)
    ofi_1s = np.full(len(grid), np.nan)
    ofi_1s[3:] = np.lib.stride_tricks.sliding_window_view(flow, 4).sum(axis=1)
    vol = np.full(len(grid), np.nan)
    vol[19:] = np.lib.stride_tricks.sliding_window_view(returns[1], 20).std(axis=1)
    matrix = np.column_stack([returns[1], returns[4], returns[20], imbalance, ofi_1s, spread, vol])
    return matrix, mid, quotes


def features(local, index, peers) -> Features:
    own, mid, quote = local
    leader = index[0]
    peer = (peers[0][0] + peers[1][0]) / 2
    index_gap = leader[:, 1] - own[:, 1]
    peer_gap = peer[:, 2] - own[:, 2]
    aligned = np.where(
        leader[:, 1] * peer[:, 1] > 0,
        np.sign(leader[:, 1]) * np.minimum(abs(leader[:, 1]), abs(peer[:, 1])),
        0,
    )
    average_leader = (leader[:, 1] + peer[:, 1]) / 2
    dislocation = average_leader - own[:, 1]
    cross = np.column_stack([
        leader[:, [0, 1, 2, 4]], peer[:, [0, 1, 2, 4]], index_gap, peer_gap, aligned,
        np.sign(dislocation) * np.maximum(abs(dislocation) - 2, 0),
        np.sign(average_leader) * np.maximum(abs(average_leader) - 2, 0),
    ])
    return Features(own, np.column_stack([own, cross]), mid, quote)


@dataclass(frozen=True)
class Ridge:
    center: np.ndarray
    scale: np.ndarray
    weights: np.ndarray
    intercept: float

    @classmethod
    def fit(cls, matrices, targets, regularization=0.1):
        x = np.concatenate(matrices)
        y = np.concatenate(targets)
        valid = np.isfinite(x).all(axis=1) & np.isfinite(y)
        if not np.any(valid):
            raise ValueError("no finite training examples")
        x, y = x[valid], y[valid]
        center = x.mean(axis=0)
        scale = x.std(axis=0)
        scale[scale < 1e-12] = 1
        standardized = np.clip((x - center) / scale, -8, 8)
        design = np.column_stack([np.ones(len(x)), standardized])
        penalty = np.eye(design.shape[1]) * regularization * len(x)
        penalty[0, 0] = 0
        weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        return cls(center, scale, weights[1:], float(weights[0]))

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        return self.intercept + np.clip((matrix - self.center) / self.scale, -8, 8) @ self.weights

    def serializable(self, columns) -> dict:
        return {
            "intercept_bps": self.intercept,
            "standardized_weights_bps": dict(zip(columns, map(float, self.weights))),
            "training_feature_means": dict(zip(columns, map(float, self.center))),
            "training_feature_std": dict(zip(columns, map(float, self.scale))),
        }
