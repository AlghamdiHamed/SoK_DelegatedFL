#!/usr/bin/env python3
"""Promote the one prespecified CKKS candidate only from a passing pilot record."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--pilot", type=Path, required=True)
    a = p.parse_args()

    target = a.root / "results" / "processed" / "study1_ckks_parameters.json"
    params = json.loads(target.read_text())
    pilot = json.loads(a.pilot.read_text())

    required = (
        "run_id",
        "status",
        "lattigo_git_commit",
        "security_estimate_bits",
        "depth_pass",
        "precision_pass",
        "serialization_pass",
    )

    if any(k not in pilot for k in required):
        raise SystemExit("pilot record is incomplete")

    if pilot["status"] != "pass" or not all(
        pilot[x]
        for x in (
            "depth_pass",
            "precision_pass",
            "serialization_pass",
        )
    ):
        raise SystemExit("pilot did not pass")

    if int(pilot["security_estimate_bits"]) < 128:
        raise SystemExit("pilot security estimate below 128 bits")

    if pilot["lattigo_git_commit"] != params["lattigo"]["git_commit"]:
        raise SystemExit("Lattigo commit mismatch")

    if params["status"] != "pilot_candidate":
        raise SystemExit("parameters are not an unfrozen candidate")

    params["status"] = "frozen"
    params["estimated_security_bits"] = pilot["security_estimate_bits"]
    params["pilot_evidence"] = {
        "run_id": pilot["run_id"],
        "sha256": hashlib.sha256(a.pilot.read_bytes()).hexdigest(),
    }
    params["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()

    fd, tmp = tempfile.mkstemp(
        dir=target.parent,
        prefix=target.name + ".",
    )
    os.close(fd)

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                params,
                f,
                sort_keys=True,
                indent=2,
            )
            f.write("\n")

        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


if __name__ == "__main__":
    main()