#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path


FIELDS = [
    "run_id",
    "configuration",
    "round_id",
    "role",
    "operation",
    "semantically_necessary_learning",
    "timestamp_utc",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--raw-root",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    a = p.parse_args()
    all_rows = []

    for path in sorted(
        a.raw_root.glob("*/operations_*.csv")
    ):
        with path.open(
            newline="",
            encoding="utf-8",
        ) as f:
            all_rows.extend(
                csv.DictReader(f)
            )

    violations = [
        r
        for r in all_rows
        if r["configuration"] in (
            "I-P",
            "I-OR",
            "I-ER",
        )
        and r["role"] == "O_1"
        and r[
            "semantically_necessary_learning"
        ].lower()
        == "true"
    ]

    if violations:
        raise SystemExit(
            "Type-I violation: "
            + repr(violations[:3])
        )

    if not any(
        r["configuration"]
        in (
            "I-P",
            "I-OR",
            "I-ER",
        )
        for r in all_rows
    ):
        raise SystemExit(
            "no delegated trace"
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
        w.writerows(all_rows)


if __name__ == "__main__":
    main()