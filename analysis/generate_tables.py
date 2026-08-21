#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.statistics import paired_bootstrap, seed_summary, systems_summary


THREAT = r"""% Automatically generated; do not edit.
\begin{tabular}{p{0.08\linewidth}p{0.09\linewidth}p{0.11\linewidth}p{0.11\linewidth}p{0.12\linewidth}p{0.07\linewidth}p{0.13\linewidth}p{0.14\linewidth}p{0.12\linewidth}}
\hline
Study & Owner & Executor & Aggregator & Executor behaviour & Collusion & Secret/property & Executor view & Out of scope \\
\hline
I--OR privacy & Trusted own data; no post-delegation learning & Untrusted for privacy & Trusted decryption/aggregation & Honest-but-curious; records full view & None & Chest features, heart rate, activity & CKKS inputs/targets, eval keys, models, sizes/timing, linked plaintext residuals & E--A collusion, key compromise, malicious A/crypto \\
I--ER privacy & Trusted own data; no post-delegation learning & Untrusted for privacy & Trusted decryption/aggregation & Honest-but-curious; records full view & None & Same; specific residual channel tested & Corresponding metadata/ciphertexts/models, but no plaintext residual & Complete accumulated-view or CKKS privacy \\
II privacy & Labels/loss local in U-splits & Untrusted for privacy & Trusted FL aggregation & Honest-but-curious & None & Activity and closed-set O\_1 subject identity & Cut activations, gradients, linkage at rounds 1/5/10 & Universal label inference or other datasets/models \\
II authenticated evasion & Verifies context/tag/replay; completes owner segment & Authorized, key-holding & Trusted FL aggregation & Work-evasive; fresh valid messages & None & Prescribed-work attribution & Authorized protocol context and returned payload & Non-repudiation, cryptographic proof of execution \\
\hline
\end{tabular}
"""


def exclusive(path: Path, text: str):
    with path.open("x", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def esc(x):
    return str(x).replace("_", r"\_").replace("%", r"\%")


def tabular(caption, headers, rows):
    align = "l" + "r" * (len(headers) - 1)
    out = [
        r"\begin{table}[t]",
        r"\centering",
        r"\small",
        r"\caption{" + esc(caption) + "}",
        r"\begin{tabular}{" + align + "}",
        r"\hline",
        " & ".join(map(esc, headers)) + r" \\",
        r"\hline",
    ]

    out += [
        " & ".join(esc(x) for x in row) + r" \\"
        for row in rows
    ]

    out += [
        r"\hline",
        r"\end{tabular}",
        r"\end{table}",
        "",
    ]

    return "\n".join(out)


def f3(x):
    try:
        return f"{float(x):.3f}"
    except:
        return str(x)


def summarize_seed(rows, groups, metric):
    nested = defaultdict(lambda: defaultdict(list))

    for r in rows:
        if r.get(metric, "") != "":
            nested[
                tuple(r[x] for x in groups)
            ][r["seed"]].append(float(r[metric]))

    out = []

    for key, seeds in sorted(nested.items()):
        per = [
            float(np.mean(v))
            for _, v in sorted(
                seeds.items(),
                key=lambda z: int(z[0]),
            )
        ]

        s = seed_summary(per)

        out.append(
            (
                *key,
                f3(s["mean"]),
                f3(s["sample_sd"]),
                f"[{f3(s['ci95_low'])}, {f3(s['ci95_high'])}]",
            )
        )

    return out


def summarize_systems(rows, groups, metric):
    nested = defaultdict(list)

    for r in rows:
        if (
            r.get(metric, "") != ""
            and r.get("gate_pass", "").lower() == "true"
        ):
            nested[
                tuple(r[x] for x in groups)
            ].append(float(r[metric]))

    out = []

    for key, v in sorted(nested.items()):
        # Collapse multiple round rows within each fresh run before the
        # three-repetition descriptive summary.
        runvals = defaultdict(list)

        for r in rows:
            if (
                tuple(r[x] for x in groups) == key
                and r.get(metric, "") != ""
                and r.get("gate_pass", "").lower() == "true"
            ):
                runvals[r["run_id"]].append(
                    float(r[metric])
                )

        s = systems_summary(
            [
                float(np.mean(x))
                for x in runvals.values()
            ]
        )

        out.append(
            (
                *key,
                f3(s["median"]),
                f"[{f3(s['minimum'])}, {f3(s['maximum'])}]",
            )
        )

    return out


def manuscript(root):
    p = root / "results" / "processed"
    chunks = [
        "% Automatically generated from gate-passing CSV files.\n"
    ]

    s1p = read(
        p / "study1_privacy.csv"
    )

    chunks.append(
        tabular(
            "Study I reconstruction and semantic outcomes across learning seeds.",
            [
                "Configuration",
                "R",
                "Linkage",
                "Metric mean",
                "SD",
                "95% CI",
            ],
            summarize_seed(
                s1p,
                [
                    "configuration",
                    "R",
                    "linkage",
                ],
                "feature_standardized_rmse",
            ),
        )
    )

    s1r = read(
        p / "study1_resources.csv"
    )

    chunks.append(
        tabular(
            "Study I owner resource components; three fresh processes.",
            [
                "Configuration",
                "Role",
                "Median active ns",
                "Range",
            ],
            summarize_systems(
                s1r,
                [
                    "configuration",
                    "role",
                ],
                "owner_active_cpu_ns",
            ),
        )
    )

    util = [
        r
        for r in read(
            p / "study2_utility.csv"
        )
        if r["round_id"] == "10"
    ]

    chunks.append(
        tabular(
            "Study II final utility across learning seeds.",
            [
                "Configuration",
                "Mean balanced accuracy",
                "SD",
                "95% CI",
            ],
            summarize_seed(
                util,
                ["configuration"],
                "test_balanced_accuracy",
            ),
        )
    )

    priv = read(
        p / "study2_privacy.csv"
    )

    chunks.append(
        tabular(
            "Study II privacy probes with auxiliary-only tuning.",
            [
                "Configuration",
                "Round",
                "View",
                "Secret",
                "Mean balanced accuracy",
                "SD",
                "95% CI",
            ],
            summarize_seed(
                priv,
                [
                    "configuration",
                    "round_id",
                    "view",
                    "secret",
                ],
                "balanced_accuracy",
            ),
        )
    )

    cross = []
    pv = defaultdict(dict)

    for r in priv:
        pv[
            (
                r["configuration"],
                r["view"],
                r["secret"],
                r["seed"],
            )
        ][r["round_id"]] = float(
            r["balanced_accuracy"]
        )

    grouped = defaultdict(
        lambda: ([], [])
    )

    for (
        cfg,
        view,
        secret,
        seed,
    ), x in pv.items():
        if (
            "linked_1_5_10" in x
            and "10" in x
        ):
            grouped[
                (cfg, view, secret)
            ][0].append(
                x["linked_1_5_10"]
            )
            grouped[
                (cfg, view, secret)
            ][1].append(x["10"])

    for key, (
        linked,
        single,
    ) in sorted(grouped.items()):
        q = paired_bootstrap(
            linked,
            single,
        )

        cross.append(
            (
                *key,
                f3(q["paired_mean"]),
                f"[{f3(q['ci95_low'])}, {f3(q['ci95_high'])}]",
            )
        )

    chunks.append(
        tabular(
            "Paired cross-round privacy-probe effects (linked minus round 10).",
            [
                "Configuration",
                "View",
                "Secret",
                "Paired mean",
                "Bootstrap 95% CI",
            ],
            cross,
        )
    )

    cut = read(
        p / "study2_cut_comparison.csv"
    )

    chunks.append(
        tabular(
            "Early and deep U-cut privacy, utility, and owner burden "
            "(learning means; systems medians).",
            [
                "Configuration",
                "Activity BA",
                "Subject BA",
                "Test BA",
                "Macro-F1",
                "CE",
                "Owner active ns",
                "Peak MiB",
                "Sent bytes",
                "Received bytes",
            ],
            [
                (
                    r["configuration"],
                    f3(
                        r[
                            "activity_balanced_accuracy"
                        ]
                    ),
                    f3(
                        r[
                            "subject_balanced_accuracy"
                        ]
                    ),
                    f3(
                        r[
                            "test_balanced_accuracy"
                        ]
                    ),
                    f3(
                        r[
                            "test_macro_f1"
                        ]
                    ),
                    f3(
                        r[
                            "test_cross_entropy"
                        ]
                    ),
                    f3(
                        r[
                            "owner_active_cpu_ns"
                        ]
                    ),
                    f3(
                        r[
                            "owner_peak_rss_mib"
                        ]
                    ),
                    f3(
                        r[
                            "bytes_sent"
                        ]
                    ),
                    f3(
                        r[
                            "bytes_received"
                        ]
                    ),
                )
                for r in cut
            ],
        )
    )

    controls = read(
        p / "study2_hmac_controls.csv"
    )
    controlrows = []

    for c in sorted(
        {
            r["control"]
            for r in controls
        }
    ):
        rr = [
            r
            for r in controls
            if r["control"] == c
        ]

        controlrows.append(
            (
                c,
                sum(
                    x[
                        "observed_accept"
                    ].lower()
                    == "true"
                    for x in rr
                ),
                len(rr),
                sum(
                    x[
                        "gate_pass"
                    ].lower()
                    == "true"
                    for x in rr
                ),
            )
        )

    chunks.append(
        tabular(
            "Contextual HMAC positive controls.",
            [
                "Control",
                "Accepted",
                "Trials",
                "Gate passes",
            ],
            controlrows,
        )
    )

    work = read(
        p / "study2_work_evasion.csv"
    )

    chunks.append(
        tabular(
            "Authorized valid-key work deviations.",
            [
                "Manipulation",
                "Fraction",
                "Mean L2",
                "SD",
                "95% CI",
            ],
            summarize_seed(
                work,
                [
                    "manipulation",
                    "fraction",
                ],
                "l2_distance",
            ),
        )
    )

    s2r = read(
        p / "study2_resources.csv"
    )

    chunks.append(
        tabular(
            "Study II owner resource components; three fresh processes.",
            [
                "Configuration",
                "Role",
                "Median online ns",
                "Range",
            ],
            summarize_systems(
                s2r,
                [
                    "configuration",
                    "role",
                ],
                "owner_protocol_online_ns",
            ),
        )
    )

    # Paired early-minus-deep effects are computed only from within-seed pairs.
    paired = []

    for metric, secret in (
        (
            "activity_balanced_accuracy",
            "activity",
        ),
        (
            "subject_balanced_accuracy",
            "subject",
        ),
    ):
        by = defaultdict(dict)

        for r in priv:
            if (
                r["round_id"] == "10"
                and r["view"] == "z_and_g"
                and r["secret"] == secret
            ):
                by[
                    r["seed"]
                ][
                    r["configuration"]
                ] = float(
                    r[
                        "balanced_accuracy"
                    ]
                )

        a = [
            x["II-U-E"]
            for x in by.values()
            if "II-U-E" in x
            and "II-U-D" in x
        ]

        b = [
            x["II-U-D"]
            for x in by.values()
            if "II-U-E" in x
            and "II-U-D" in x
        ]

        q = paired_bootstrap(a, b)

        paired.append(
            (
                metric,
                f3(q["paired_mean"]),
                f"[{f3(q['ci95_low'])}, {f3(q['ci95_high'])}]",
            )
        )

    by = defaultdict(dict)

    for r in util:
        by[
            r["seed"]
        ][
            r["configuration"]
        ] = float(
            r[
                "test_balanced_accuracy"
            ]
        )

    a = [
        x["II-U-E"]
        for x in by.values()
        if "II-U-E" in x
        and "II-U-D" in x
    ]

    b = [
        x["II-U-D"]
        for x in by.values()
        if "II-U-E" in x
        and "II-U-D" in x
    ]

    q = paired_bootstrap(a, b)

    paired.append(
        (
            "test_balanced_accuracy",
            f3(q["paired_mean"]),
            f"[{f3(q['ci95_low'])}, {f3(q['ci95_high'])}]",
        )
    )

    for metric in (
        "owner_active_cpu_ns",
        "socket_bytes_sent",
        "socket_bytes_received",
    ):
        by = defaultdict(dict)

        for r in s2r:
            if r["role"] == "O_1":
                by[
                    r["repetition"]
                ][
                    r["configuration"]
                ] = float(r[metric])

        a = [
            x["II-U-E"]
            for x in by.values()
            if "II-U-E" in x
            and "II-U-D" in x
        ]

        b = [
            x["II-U-D"]
            for x in by.values()
            if "II-U-E" in x
            and "II-U-D" in x
        ]

        q = paired_bootstrap(a, b)

        paired.append(
            (
                metric,
                f3(q["paired_mean"]),
                f"[{f3(q['ci95_low'])}, {f3(q['ci95_high'])}]",
            )
        )

    chunks.append(
        tabular(
            "Within-seed early-minus-deep cut effects.",
            [
                "Metric",
                "Paired mean",
                "Bootstrap 95% CI",
            ],
            paired,
        )
    )

    return "\n".join(chunks)


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--root",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--threat-model-only",
        action="store_true",
    )

    a = p.parse_args()

    out = (
        a.root
        / "results"
        / "processed"
    )

    target = (
        out
        / "threat_models.tex"
    )

    if not target.exists():
        exclusive(
            target,
            THREAT,
        )

    if a.threat_model_only:
        return

    subprocess.run(
        [
            "python3",
            str(
                a.root
                / "analysis"
                / "gates.py"
            ),
            "--root",
            str(a.root),
            "--phase",
            "manuscript",
        ],
        check=True,
    )

    exclusive(
        out / "manuscript_tables.tex",
        manuscript(a.root),
    )


if __name__ == "__main__":
    main()