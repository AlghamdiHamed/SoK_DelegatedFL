#!/usr/bin/env python3
"""Join opaque handles to secrets outside E, then run frozen probes."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np

from study2.attacks.probes import fit_evaluate


FIELDS = (
    "run_id,seed,configuration,round_id,view,secret,selected_C,"
    "balanced_accuracy,macro_f1,confusion_matrix,class_support,"
    "majority_balanced_accuracy,majority_macro_f1,"
    "shuffled_balanced_accuracy,shuffled_macro_f1,attack_restart"
).split(",")


def labels(records):
    with records.open(newline="", encoding="utf-8") as f:
        return {
            r["record_id"]: (
                int(r["activity"]),
                int(r["subject"][1:]) - 1,
            )
            for r in csv.DictReader(f)
        }


def load_round(
    view_dir,
    round_id,
    partition,
    label_map,
):
    files = sorted(
        view_dir.glob(
            f"round_{round_id:02d}_{partition}_batch_*.npz"
        )
    )
    z = []
    g = []
    handles = []

    if not files:
        raise ValueError(
            f"no executor views for round {round_id}/{partition}"
        )

    for p in files:
        x = np.load(
            p,
            allow_pickle=False,
        )
        z.append(
            x["z"].reshape(
                len(x["z"]),
                -1,
            )
        )
        g.append(
            x["g"].reshape(
                len(x["g"]),
                -1,
            )
        )
        handles.extend(
            x["sample_handles"].tolist()
        )

    activity = np.asarray(
        [label_map[h][0] for h in handles]
    )
    subject = np.asarray(
        [label_map[h][1] for h in handles]
    )

    return {
        "z": np.concatenate(z),
        "g": np.concatenate(g),
        "handles": handles,
        "activity": activity,
        "subject": subject,
    }


def aligned_linked(rounds, view):
    common = set(rounds[0]["handles"])

    for r in rounds[1:]:
        common &= set(r["handles"])

    ids = sorted(common)
    parts = []

    for r in rounds:
        pos = {
            h: i
            for i, h in enumerate(r["handles"])
        }

        x = (
            r[view]
            if view != "z_and_g"
            else np.concatenate(
                [r["z"], r["g"]],
                axis=1,
            )
        )

        parts.append(
            x[[pos[h] for h in ids]]
        )

    pos = {
        h: i
        for i, h in enumerate(
            rounds[0]["handles"]
        )
    }

    return (
        np.concatenate(
            parts,
            axis=1,
        ),
        rounds[0]["activity"][
            [pos[h] for h in ids]
        ],
        rounds[0]["subject"][
            [pos[h] for h in ids]
        ],
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--run-dir",
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
        "--configuration",
        required=True,
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    a = p.parse_args()
    lm = labels(a.records)
    cache = {}

    for part in (
        "attacker_auxiliary",
        "attacker_target",
    ):
        cache[part] = [
            load_round(
                a.run_dir / "executor_views",
                r,
                part,
                lm,
            )
            for r in (1, 5, 10)
        ]

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

        for view in (
            "z",
            "g",
            "z_and_g",
        ):
            for ri, r in enumerate(
                (1, 5, 10)
            ):
                aux = cache[
                    "attacker_auxiliary"
                ][ri]
                target = cache[
                    "attacker_target"
                ][ri]

                Xa = (
                    aux[view]
                    if view != "z_and_g"
                    else np.concatenate(
                        [aux["z"], aux["g"]],
                        1,
                    )
                )
                Xt = (
                    target[view]
                    if view != "z_and_g"
                    else np.concatenate(
                        [target["z"], target["g"]],
                        1,
                    )
                )

                for secret in (
                    "activity",
                    "subject",
                ):
                    q = fit_evaluate(
                        Xa,
                        aux[secret],
                        Xt,
                        target[secret],
                        a.seed,
                    )

                    row = {
                        "run_id": a.run_id,
                        "seed": a.seed,
                        "configuration": a.configuration,
                        "round_id": r,
                        "view": view,
                        "secret": secret,
                        "attack_restart": "",
                    }

                    row.update(
                        {
                            k: (
                                json.dumps(
                                    v,
                                    separators=(",", ":"),
                                )
                                if isinstance(
                                    v,
                                    (list, dict),
                                )
                                else v
                            )
                            for k, v in q.items()
                            if k in FIELDS
                        }
                    )

                    w.writerow(row)

            Xa, ya, sa = aligned_linked(
                cache["attacker_auxiliary"],
                view,
            )
            Xt, yt, st = aligned_linked(
                cache["attacker_target"],
                view,
            )

            for secret, la, lt in (
                ("activity", ya, yt),
                ("subject", sa, st),
            ):
                q = fit_evaluate(
                    Xa,
                    la,
                    Xt,
                    lt,
                    a.seed,
                )

                row = {
                    "run_id": a.run_id,
                    "seed": a.seed,
                    "configuration": a.configuration,
                    "round_id": "linked_1_5_10",
                    "view": view,
                    "secret": secret,
                    "attack_restart": "",
                }

                row.update(
                    {
                        k: (
                            json.dumps(
                                v,
                                separators=(",", ":"),
                            )
                            if isinstance(
                                v,
                                (list, dict),
                            )
                            else v
                        )
                        for k, v in q.items()
                        if k in FIELDS
                    }
                )

                w.writerow(row)


if __name__ == "__main__":
    main()