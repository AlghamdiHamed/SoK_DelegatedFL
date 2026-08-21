#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import balanced_accuracy_score, f1_score, log_loss

from study2.common.data import load_frozen
from study2.common.models import FullMLP, load_arrays
from study2.common.wire import decode_arrays


FIELDS = (
    "run_id,seed,configuration,round_id,test_balanced_accuracy,"
    "test_macro_f1,test_cross_entropy"
).split(",")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--records", type=Path, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--configuration", required=True)
    p.add_argument("--output", type=Path, required=True)

    a = p.parse_args()

    parts = [
        load_frozen(a.records, o, "utility_test")
        for o in ("O_1", "O_2", "O_3")
    ]

    X = np.concatenate([x[0] for x in parts])
    y = np.concatenate([x[1] for x in parts])

    with a.output.open(
        "x",
        newline="",
        encoding="utf-8",
    ) as f:
        w = csv.DictWriter(
            f,
            fieldnames=FIELDS,
        )
        w.writeheader()

        for r in range(1, 11):
            state = decode_arrays(
                (
                    a.run_dir
                    / f"global_model_round_{r:02d}.bin"
                ).read_bytes()
            )

            m = FullMLP()
            load_arrays(m, state)
            m.eval()

            with torch.no_grad():
                logits = m(torch.from_numpy(X))
                prob = torch.softmax(logits, 1).numpy()
                pred = prob.argmax(1)

            w.writerow(
                {
                    "run_id": a.run_id,
                    "seed": a.seed,
                    "configuration": a.configuration,
                    "round_id": r,
                    "test_balanced_accuracy": balanced_accuracy_score(
                        y,
                        pred,
                    ),
                    "test_macro_f1": f1_score(
                        y,
                        pred,
                        average="macro",
                        zero_division=0,
                    ),
                    "test_cross_entropy": log_loss(
                        y,
                        prob,
                        labels=list(range(12)),
                    ),
                }
            )


if __name__ == "__main__":
    main()