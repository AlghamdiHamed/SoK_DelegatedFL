#!/usr/bin/env python3
"""Allocate an immutable raw-run directory and print its ID."""
from __future__ import annotations

import argparse
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--study", required=True, choices=("study1", "study2", "systems", "pilot"))
    ap.add_argument("--configuration", required=True)
    ap.add_argument("--seed", required=True, type=int)
    ap.add_argument("--repetition", type=int)
    ap.add_argument("--warmup", action="store_true")
    args = ap.parse_args()
    if args.study == "systems" and not args.warmup and (args.repetition is None or not 1 <= args.repetition <= 3):
        raise SystemExit("measured systems runs require --repetition 1..3")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = f"{args.study}-{args.configuration}-{args.seed}-{stamp}-{secrets.token_hex(6)}"
    run_dir = args.root.resolve() / "results" / "raw" / run_id
    os.mkdir(run_dir, mode=0o700)
    metadata = {
        "run_id": run_id,
        "study": args.study,
        "configuration": args.configuration,
        "seed": args.seed,
        "repetition": args.repetition,
        "warmup": args.warmup,
        "allocated_at_utc": datetime.now(timezone.utc).isoformat(),
        "immutable": True,
    }
    with (run_dir / "run.json").open("x", encoding="utf-8") as f:
        json.dump(metadata, f, sort_keys=True, indent=2)
        f.write("\n")
    print(run_id)


if __name__ == "__main__":
    main()
