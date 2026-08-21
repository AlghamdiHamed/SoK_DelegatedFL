from __future__ import annotations

import math

import numpy as np
from scipy.stats import t


def seed_summary(values):
    x = np.asarray(values, dtype=float)
    n = len(x)

    if n < 2:
        raise ValueError("between-seed summary needs >=2 seeds")

    mean = float(x.mean())
    sd = float(x.std(ddof=1))
    half = float(t.ppf(0.975, n - 1) * sd / math.sqrt(n))

    return {
        "n": n,
        "mean": mean,
        "sample_sd": sd,
        "ci95_low": mean - half,
        "ci95_high": mean + half,
    }


def paired_bootstrap(a, b, seed=271828, draws=50000):
    a = np.asarray(a, float)
    b = np.asarray(b, float)

    if a.shape != b.shape or a.ndim != 1 or len(a) < 2:
        raise ValueError("paired vectors required")

    d = a - b
    rng = np.random.default_rng(seed)
    means = np.empty(draws)

    for start in range(0, draws, 5000):
        n = min(5000, draws - start)
        means[start : start + n] = d[
            rng.integers(0, len(d), size=(n, len(d)))
        ].mean(axis=1)

    lo, hi = np.quantile(means, [0.025, 0.975])

    return {
        "paired_mean": float(d.mean()),
        "ci95_low": float(lo),
        "ci95_high": float(hi),
        "draws": draws,
    }


def systems_summary(values):
    x = np.asarray(values, float)

    if len(x) != 3:
        raise ValueError(
            "systems summary requires exactly 3 fresh-process repetitions"
        )

    return {
        "n": 3,
        "median": float(np.median(x)),
        "minimum": float(x.min()),
        "maximum": float(x.max()),
    }