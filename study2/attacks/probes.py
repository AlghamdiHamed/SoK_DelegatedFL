#!/usr/bin/env python3
"""Prespecified auxiliary-only logistic privacy probes."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


CS = (0.01, 0.1, 1.0, 10.0)


def fit_evaluate(
    X_aux,
    y_aux,
    X_target,
    y_target,
    seed,
):
    # Deterministic stratified 75/25 auxiliary split; target remains wholly disjoint.
    train = []
    val = []

    for c in sorted(set(y_aux.tolist())):
        idx = np.flatnonzero(y_aux == c)
        cut = max(
            1,
            int(np.floor(0.75 * len(idx))),
        )
        train.extend(idx[:cut])
        val.extend(idx[cut:])

    if not val:
        raise ValueError("auxiliary validation is empty")

    scores = []

    for C in CS:
        p = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=C,
                penalty="l2",
                solver="lbfgs",
                max_iter=3000,
                multi_class="multinomial",
                random_state=seed,
            ),
        )

        p.fit(
            X_aux[train],
            y_aux[train],
        )
        scores.append(
            balanced_accuracy_score(
                y_aux[val],
                p.predict(X_aux[val]),
            )
        )

    best = CS[int(np.argmax(scores))]

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=best,
            penalty="l2",
            solver="lbfgs",
            max_iter=3000,
            multi_class="multinomial",
            random_state=seed,
        ),
    )

    model.fit(X_aux, y_aux)
    pred = model.predict(X_target)

    labels = sorted(
        set(y_aux.tolist())
        | set(y_target.tolist())
    )

    majority = max(
        set(y_aux.tolist()),
        key=lambda x: int(np.sum(y_aux == x)),
    )
    prior = np.full_like(
        y_target,
        majority,
    )

    rng = np.random.default_rng(seed + 100000)
    shuffled = y_aux.copy()
    rng.shuffle(shuffled)

    negative = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=best,
            penalty="l2",
            solver="lbfgs",
            max_iter=3000,
            multi_class="multinomial",
            random_state=seed,
        ),
    )

    negative.fit(X_aux, shuffled)
    neg = negative.predict(X_target)

    return {
        "selected_C": best,
        "aux_validation_scores": dict(
            zip(
                map(str, CS),
                scores,
            )
        ),
        "balanced_accuracy": balanced_accuracy_score(
            y_target,
            pred,
        ),
        "macro_f1": f1_score(
            y_target,
            pred,
            average="macro",
            zero_division=0,
        ),
        "confusion_matrix": confusion_matrix(
            y_target,
            pred,
            labels=labels,
        ).tolist(),
        "class_labels": labels,
        "class_support": [
            int(np.sum(y_target == x))
            for x in labels
        ],
        "majority_balanced_accuracy": balanced_accuracy_score(
            y_target,
            prior,
        ),
        "majority_macro_f1": f1_score(
            y_target,
            prior,
            average="macro",
            zero_division=0,
        ),
        "shuffled_balanced_accuracy": balanced_accuracy_score(
            y_target,
            neg,
        ),
        "shuffled_macro_f1": f1_score(
            y_target,
            neg,
            average="macro",
            zero_division=0,
        ),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--aux",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--target",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--secret",
        choices=("activity", "subject"),
        required=True,
    )
    p.add_argument(
        "--seed",
        type=int,
        required=True,
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    a = p.parse_args()

    aux = np.load(
        a.aux,
        allow_pickle=False,
    )
    target = np.load(
        a.target,
        allow_pickle=False,
    )

    key = (
        "y_activity"
        if a.secret == "activity"
        else "y_subject"
    )

    out = fit_evaluate(
        aux["X"],
        aux[key],
        target["X"],
        target[key],
        a.seed,
    )

    with a.output.open(
        "x",
        encoding="utf-8",
    ) as f:
        json.dump(
            out,
            f,
            sort_keys=True,
            indent=2,
        )
        f.write("\n")


if __name__ == "__main__":
    main()