#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import balanced_accuracy_score, f1_score

from study2.attacks.work_evasion import l2_cosine
from study2.common.data import load_frozen
from study2.common.models import FullMLP, load_arrays
from study2.common.wire import decode_arrays


FIELDS = (
    "run_id,seed,manipulation,fraction,hmac_acceptance,"
    "executor_active_compute_ns,executor_compute_saved_percent,"
    "l2_distance,cosine_similarity,round_id,global_loss,"
    "test_balanced_accuracy,test_macro_f1,convergence_effect,"
    "attack_restart"
).split(",")


def metrics(run, round_id, Xtrain, ytrain, Xtest, ytest):
    state = decode_arrays(
        (run / f"global_model_round_{round_id:02d}.bin").read_bytes()
    )
    m = FullMLP()
    load_arrays(m, state)
    m.eval()

    with torch.no_grad():
        lt = m(torch.from_numpy(Xtrain))
        loss = float(
            torch.nn.functional.cross_entropy(
                lt,
                torch.from_numpy(ytrain),
            )
        )
        p = (
            m(torch.from_numpy(Xtest))
            .argmax(1)
            .numpy()
        )

    return (
        state,
        loss,
        balanced_accuracy_score(ytest, p),
        f1_score(
            ytest,
            p,
            average="macro",
            zero_division=0,
        ),
    )


def exec_times(run):
    out = {}

    for line in (
        run / "executor_batches.jsonl"
    ).read_text().splitlines():
        r = json.loads(line)

        if not r.get("probe_partition"):
            out[r["round_id"]] = (
                out.get(r["round_id"], 0)
                + int(r["executor_active_compute_ns"])
            )

    return out


def flatten(s):
    return np.concatenate(
        [
            s[k].astype(np.float64).ravel()
            for k in sorted(s)
        ]
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--honest-run",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--attack-run",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--records",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--run-id",
        required=True,
    )
    p.add_argument(
        "--seed",
        type=int,
        required=True,
    )
    p.add_argument(
        "--manipulation",
        required=True,
    )
    p.add_argument(
        "--fraction",
        default="",
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    a = p.parse_args()

    learn = [
        load_frozen(a.records, o, "learning")
        for o in ("O_1", "O_2", "O_3")
    ]
    test = [
        load_frozen(a.records, o, "utility_test")
        for o in ("O_1", "O_2", "O_3")
    ]

    Xl = np.concatenate(
        [x[0] for x in learn]
    )
    yl = np.concatenate(
        [x[1] for x in learn]
    )
    Xt = np.concatenate(
        [x[0] for x in test]
    )
    yt = np.concatenate(
        [x[1] for x in test]
    )

    ht = exec_times(a.honest_run)
    at = exec_times(a.attack_run)

    owner_rows = [
        json.loads(x)
        for x in (
            a.attack_run / "owner_O_1_rounds.jsonl"
        ).read_text().splitlines()
    ]

    if (
        len(owner_rows) != 10
        or not all(
            x.get("hmac_acceptance")
            for x in owner_rows
        )
    ):
        raise ValueError(
            "fresh valid-key response was not accepted in every round"
        )

    rows = []

    for r in range(1, 11):
        hs, hl, hba, hf = metrics(
            a.honest_run,
            r,
            Xl,
            yl,
            Xt,
            yt,
        )
        s, loss, ba, mf = metrics(
            a.attack_run,
            r,
            Xl,
            yl,
            Xt,
            yt,
        )

        honest_segment = decode_arrays(
            (
                a.honest_run
                / f"returned_segment_round_{r:02d}.bin"
            ).read_bytes()
        )
        attack_segment = decode_arrays(
            (
                a.attack_run
                / f"returned_segment_round_{r:02d}.bin"
            ).read_bytes()
        )

        d, c = l2_cosine(
            flatten(honest_segment),
            flatten(attack_segment),
        )

        saved = (
            100 * (ht[r] - at[r]) / ht[r]
            if ht[r]
            else float("nan")
        )

        rows.append(
            {
                "run_id": a.run_id,
                "seed": a.seed,
                "manipulation": a.manipulation,
                "fraction": a.fraction,
                "hmac_acceptance": "true",
                "executor_active_compute_ns": at[r],
                "executor_compute_saved_percent": saved,
                "l2_distance": d,
                "cosine_similarity": c,
                "round_id": r,
                "global_loss": loss,
                "test_balanced_accuracy": ba,
                "test_macro_f1": mf,
                "convergence_effect": loss - hl,
                "attack_restart": "",
            }
        )

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
        w.writerows(rows)


if __name__ == "__main__":
    main()