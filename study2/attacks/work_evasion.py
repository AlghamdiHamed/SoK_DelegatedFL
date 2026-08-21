"""Exact prespecified valid-key work deviations."""

from __future__ import annotations

import numpy as np


def partial_indices(batch_size: int, fraction: float):
    if fraction not in (0.25, 0.5, 0.75):
        raise ValueError("unregistered fraction")

    return np.arange(
        max(
            1,
            int(np.floor(batch_size * fraction)),
        ),
        dtype=np.int64,
    )


def substitution_permutation(batch_size: int, seed: int):
    if batch_size < 2:
        raise ValueError("substitution requires >=2 samples")

    p = np.random.default_rng(seed).permutation(batch_size)

    if np.all(p == np.arange(batch_size)):
        p = np.roll(p, 1)

    return p


def l2_cosine(
    honest: np.ndarray,
    modified: np.ndarray,
):
    a = honest.astype(np.float64).ravel()
    b = modified.astype(np.float64).ravel()

    d = float(np.linalg.norm(a - b))
    den = np.linalg.norm(a) * np.linalg.norm(b)

    return (
        d,
        float(np.dot(a, b) / den)
        if den
        else float("nan"),
    )