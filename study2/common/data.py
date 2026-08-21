from __future__ import annotations

import csv
from pathlib import Path

import numpy as np


def load_frozen(path: Path, owner: str, partition: str):
    with path.open(newline="", encoding="utf-8") as f:
        rows = [
            r
            for r in csv.DictReader(f)
            if r["owner"] == owner and r["partition"] == partition
        ]

    if not rows:
        raise ValueError(f"no records for {owner}/{partition}")

    xnames = sorted(
        k for k in rows[0] if k.startswith("x_")
    )

    if len(xnames) != 36:
        raise ValueError("Study-II feature schema must be exactly 36")

    X = np.asarray(
        [
            [float(r[k]) for k in xnames]
            for r in rows
        ],
        dtype=np.float32,
    )

    y = np.asarray(
        [int(r["activity"]) for r in rows],
        dtype=np.int64,
    )

    classes = sorted(set(y.tolist()))
    mapping = {
        v: i
        for i, v in enumerate(
            (1, 2, 3, 4, 5, 6, 7, 12, 13, 16, 17, 24)
        )
    }

    y = np.asarray(
        [mapping[int(v)] for v in y],
        dtype=np.int64,
    )

    subject = np.asarray(
        [int(r["subject"][1:]) - 1 for r in rows],
        dtype=np.int64,
    )

    ids = np.asarray(
        [r["record_id"] for r in rows]
    )

    if not np.isfinite(X).all():
        raise ValueError("nonfinite features")

    return X, y, subject, ids