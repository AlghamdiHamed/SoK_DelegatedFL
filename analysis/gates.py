#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Gate:
    group: str
    name: str
    passed: bool
    detail: str


def rows(path):
    if not path.is_file():
        return []

    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def nonempty(path):
    return path.is_file() and len(rows(path)) > 0


def evaluate(root: Path):
    p = root / "results" / "processed"
    m = root / "manifests"
    g = []

    needed = [
        m / "dataset_manifest.csv",
        m / "study1_records.csv",
        m / "study2_records.csv",
        m / "dataset_manifest.sha256",
    ] + [
        m / f"{study}_{owner}_records.csv"
        for study in ("study1", "study2")
        for owner in ("O_1", "O_2", "O_3")
    ]

    g.append(
        Gate(
            "dataset",
            "frozen_sample_manifest",
            all(x.is_file() for x in needed),
            "all manifest artifacts exist",
        )
    )

    try:
        sys.path.insert(0, str(root))
        from preprocessing.pamap2 import assert_no_temporal_overlap, read_manifest

        ws = read_manifest(root)
        assert_no_temporal_overlap(ws)
        g.append(
            Gate(
                "dataset",
                "no_temporal_overlap",
                True,
                f"{len(ws)} selected records",
            )
        )
    except Exception as e:
        g.append(
            Gate(
                "dataset",
                "no_temporal_overlap",
                False,
                repr(e),
            )
        )

    try:
        h = hashlib.sha256()

        names = [
            "dataset_manifest.csv",
            "study1_records.csv",
            "study2_records.csv",
        ] + [
            f"{study}_{owner}_records.csv"
            for study in ("study1", "study2")
            for owner in ("O_1", "O_2", "O_3")
        ]

        for n in names:
            h.update(n.encode() + b"\0" + (m / n).read_bytes())

        ok = h.hexdigest() == (m / "dataset_manifest.sha256").read_text().strip()

        g.append(
            Gate(
                "dataset",
                "manifest_hash",
                ok,
                h.hexdigest(),
            )
        )
    except Exception as e:
        g.append(
            Gate(
                "dataset",
                "manifest_hash",
                False,
                repr(e),
            )
        )

    try:
        h1 = next(csv.reader((m / "study1_records.csv").open()))
        h2 = next(csv.reader((m / "study2_records.csv").open()))

        x1 = [x for x in h1 if x.startswith("x_")]
        x2 = [x for x in h2 if x.startswith("x_")]

        forbidden = (
            "subject",
            "timestamp",
            "temperature",
            "orientation",
            "heart_rate",
        )

        ok = (
            len(x1) == 12
            and len(x2) == 36
            and not any(
                any(z in x.lower() for z in forbidden)
                for x in x1 + x2
            )
        )

        g.append(
            Gate(
                "dataset",
                "feature_schema",
                ok,
                f"study1={len(x1)}, study2={len(x2)}",
            )
        )
    except Exception as e:
        g.append(
            Gate(
                "dataset",
                "feature_schema",
                False,
                repr(e),
            )
        )

    ops = rows(p / "study1_operation_trace.csv")
    delegated = [
        r
        for r in ops
        if r.get("configuration") in ("I-P", "I-OR", "I-ER")
    ]
    violation = [
        r
        for r in delegated
        if r.get("role") == "O_1"
        and r.get("semantically_necessary_learning", "true").lower() == "true"
    ]

    g.append(
        Gate(
            "study1",
            "type_i_operation_trace",
            bool(delegated) and not violation,
            f"delegated_ops={len(delegated)}, violations={len(violation)}",
        )
    )

    try:
        c = json.loads((p / "study1_ckks_parameters.json").read_text())

        ok = (
            c.get("status") == "frozen"
            and c.get("lattigo", {}).get("git_commit")
            and c.get("estimated_security_bits") is not None
            and c.get("pilot_evidence")
        )

        g.append(
            Gate(
                "study1",
                "ckks_parameters_frozen",
                bool(ok),
                f"status={c.get('status')}",
            )
        )
    except Exception as e:
        g.append(
            Gate(
                "study1",
                "ckks_parameters_frozen",
                False,
                repr(e),
            )
        )

    keysets = []
    keyok = True

    for invp in (root / "results" / "raw").glob("*/study1_key_inventory.json"):
        inv = json.loads(invp.read_text())
        files = inv.get("files", {})

        keysets.append(
            tuple(
                (k, v.get("sha256"))
                for k, v in sorted(files.items())
            )
        )

        keyok &= (
            not inv.get("secret_key_leaves_A", True)
            and files.get("secret.key", {}).get("possessing_roles") == ["A"]
        )

    g.append(
        Gate(
            "study1",
            "fixed_key_architecture",
            bool(keysets) and keyok and len(set(keysets)) == 1,
            f"inventories={len(keysets)}, distinct={len(set(keysets))}",
        )
    )

    corr = rows(p / "study1_correctness.csv")

    g.append(
        Gate(
            "study1",
            "he_correctness",
            bool(corr)
            and all(r.get("gate_pass", "").lower() == "true" for r in corr),
            f"rows={len(corr)}",
        )
    )

    priv = rows(p / "study1_privacy.csv")

    rankok = bool(priv) and all(
        r.get("equations")
        and r.get("rank")
        and r.get("singular_values")
        and r.get("condition_number")
        and r.get("rank_tolerance")
        for r in priv
        if r.get("configuration") == "I-OR"
    )

    g.append(
        Gate(
            "study1",
            "rank_before_interpretation",
            rankok,
            f"rows={len(priv)}",
        )
    )

    ior = [
        r
        for r in priv
        if r.get("configuration") == "I-OR"
    ]

    underdetermined = (
        bool(ior)
        and {r.get("R") for r in ior} == {"1", "5", "10"}
        and all(
            int(r["equations"]) <= 10 and int(r["rank"]) < 13
            for r in ior
        )
    )

    g.append(
        Gate(
            "study1",
            "underdetermined_interpretation",
            underdetermined,
            "maximum 10 equations for 13 unknowns; unique reconstruction prohibited",
        )
    )

    raw = list((root / "results" / "raw").glob("*/messages_E.jsonl"))
    text = "\n".join(x.read_text(errors="replace") for x in raw)

    g.append(
        Gate(
            "study1",
            "executor_view_logged",
            bool(raw) and "PLAINTEXT_RESIDUALS" in text,
            "executor logs contain opened channel for I-OR",
        )
    )

    view_ok = True
    view_detail = []

    for run_json in (root / "results" / "raw").glob("*/run.json"):
        meta = json.loads(run_json.read_text())
        run = run_json.parent

        log = (
            (run / "messages_E.jsonl").read_text(errors="replace")
            if (run / "messages_E.jsonl").is_file()
            else ""
        )

        if meta.get("configuration") == "I-ER":
            bad = (
                "PLAINTEXT_RESIDUALS" in log
                or bool(list(run.glob("executor_view_plaintext_residuals_*.json")))
            )
            view_ok &= not bad
            view_detail.append(
                f"I-ER:{run.name}:{'bad' if bad else 'clean'}"
            )

        if meta.get("configuration") == "I-OR":
            good = (
                "PLAINTEXT_RESIDUALS" in log
                and len(list(run.glob("executor_view_plaintext_residuals_*.json")))
                == 10
            )
            view_ok &= good
            view_detail.append(
                f"I-OR:{run.name}:{'complete' if good else 'missing'}"
            )

    g.append(
        Gate(
            "study1",
            "residual_channel_control",
            bool(view_detail) and view_ok,
            ";".join(view_detail),
        )
    )

    try:
        ws = read_manifest(root)

        aux = [
            x
            for x in ws
            if x.study == "study2"
            and x.partition == "attacker_auxiliary"
        ]

        target = [
            x
            for x in ws
            if x.study == "study2"
            and x.partition == "attacker_target"
        ]

        disjoint = all(
            a.subject != b.subject
            or a.raw_end_row < b.raw_start_row
            or b.raw_end_row < a.raw_start_row
            for a in aux
            for b in target
        )

        g.append(
            Gate(
                "study2_privacy",
                "auxiliary_target_disjoint",
                bool(aux) and bool(target) and disjoint,
                f"aux={len(aux)}, target={len(target)}",
            )
        )
    except Exception as e:
        g.append(
            Gate(
                "study2_privacy",
                "auxiliary_target_disjoint",
                False,
                repr(e),
            )
        )

    p2 = rows(p / "study2_privacy.csv")
    observed_seeds = {r.get("seed") for r in p2}

    g.append(
        Gate(
            "study2_privacy",
            "three_seeds_and_controls",
            observed_seeds == {"101", "211", "307"}
            and all(r.get("shuffled_balanced_accuracy") for r in p2),
            f"seeds={sorted(observed_seeds)}",
        )
    )

    hc = rows(p / "study2_hmac_controls.csv")

    expected = {
        "payload_modification",
        "tag_modification",
        "exact_replay",
        "wrong_task",
        "wrong_round",
        "wrong_batch",
        "wrong_model_state_hash",
        "stale_sequence",
    }

    observed = {
        r.get("control")
        for r in hc
        if r.get("gate_pass", "").lower() == "true"
    }

    g.append(
        Gate(
            "study2_integrity",
            "hmac_positive_controls",
            expected <= observed,
            f"passed={sorted(observed)}",
        )
    )

    we = rows(p / "study2_work_evasion.csv")

    g.append(
        Gate(
            "study2_integrity",
            "fresh_valid_key_evasion",
            bool(we)
            and all(
                r.get("hmac_acceptance", "").lower() == "true"
                for r in we
            ),
            f"rows={len(we)}",
        )
    )

    go_manifests = []

    for run_json in (root / "results" / "raw").glob("*/run.json"):
        meta = json.loads(run_json.read_text())

        if str(meta.get("configuration", "")).startswith("I-"):
            for start in run_json.parent.glob("node_*_start.json"):
                manifest = json.loads(start.read_text())

                reported = (
                    manifest.get("software", {})
                    .get("go_version", {})
                    .get("stdout", "")
                    .strip()
                )

                go_manifests.append(
                    (manifest.get("role", ""), reported)
                )

    go_ok = bool(go_manifests) and all(
        "go version go1.26.5 " in reported
        for _, reported in go_manifests
    )

    g.append(
        Gate(
            "study1",
            "go_toolchain",
            go_ok,
            (
                f"manifests={len(go_manifests)}, "
                f"versions={sorted(set(v for _, v in go_manifests))}"
            ),
        )
    )

    hw = rows(p / "hardware_manifest.csv")
    byrun = {}
    pair_counts = {}

    for r in hw:
        byrun.setdefault(r["run_id"], {})[r["role"]] = r
        pair = (r["run_id"], r["role"])
        pair_counts[pair] = pair_counts.get(pair, 0) + 1

    hwok = (
        bool(byrun)
        and len(hw) == len(byrun) * 5
        and all(v == 1 for v in pair_counts.values())
    )

    for rid, x in byrun.items():
        hwok &= set(x) == {"O_1", "O_2", "O_3", "E", "A"}
        hwok &= len({r.get("hostname", "") for r in x.values()}) == 5

        hwok &= (
            "Raspberry Pi 4" in x.get("O_1", {}).get("model", "")
            and "Raspberry Pi 4" in x.get("O_2", {}).get("model", "")
            and "Raspberry Pi 5" in x.get("O_3", {}).get("model", "")
        )

        hwok &= "i9" in x.get("E", {}).get("cpu_model", "").lower()

        hwok &= (
            "optiplex 7010" in x.get("A", {}).get("model", "").lower()
            and "i5-3470" in x.get("A", {}).get("cpu_model", "").lower()
        )

        try:
            hwok &= int(x.get("A", {}).get("ram_bytes", 0)) >= 7_500_000_000
        except (TypeError, ValueError):
            hwok = False

    g.append(
        Gate(
            "resources",
            "five_physical_roles",
            bool(hwok),
            (
                f"runs={len(byrun)}, rows={len(hw)}, "
                f"unique_pairs={len(pair_counts)}"
            ),
        )
    )

    nw = rows(p / "network_manifest.csv")
    nwruns = {}
    nwpairs = {}

    for r in nw:
        nwruns.setdefault(r["run_id"], set()).add(r["role"])
        pair = (r["run_id"], r["role"])
        nwpairs[pair] = nwpairs.get(pair, 0) + 1

    nw_required = (
        "interface",
        "link_speed",
        "duplex",
        "rtt_median_ms",
        "rtt_p95_ms",
        "packet_loss_percent",
    )

    nwok = (
        bool(nwruns)
        and len(nw) == len(nwruns) * 5
        and all(v == 1 for v in nwpairs.values())
        and all(
            v == {"O_1", "O_2", "O_3", "E", "A"}
            for v in nwruns.values()
        )
        and all(
            all(r.get(k, "") != "" for k in nw_required)
            for r in nw
        )
    )

    g.append(
        Gate(
            "resources",
            "network_state_recorded",
            nwok,
            (
                f"runs={len(nwruns)}, rows={len(nw)}, "
                f"unique_pairs={len(nwpairs)}"
            ),
        )
    )

    for name in (
        "study1_resources.csv",
        "study2_resources.csv",
    ):
        rr = rows(p / name)
        configs = {r.get("configuration") for r in rr}

        required = (
            "cold_start_ns",
            "available_memory_bytes",
            "governor",
            "rtt_median_ms",
        )

        ok = (
            bool(rr)
            and all(
                r.get("gate_pass", "").lower() == "true"
                for r in rr
            )
            and all(
                all(r.get(k, "") != "" for k in required)
                for r in rr
            )
            and all(
                len(
                    {
                        r.get("run_id")
                        for r in rr
                        if r.get("configuration") == c
                    }
                )
                == 3
                for c in configs
            )
        )

        g.append(
            Gate(
                "resources",
                name.replace(".csv", ""),
                ok,
                f"rows={len(rr)}, configs={len(configs)}",
            )
        )

    return g


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--root",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--phase",
        choices=("manuscript", "check"),
        default="check",
    )
    p.add_argument(
        "--output",
        type=Path,
    )

    a = p.parse_args()
    gs = evaluate(a.root.resolve())

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": a.phase,
        "passed": all(x.passed for x in gs),
        "gates": [asdict(x) for x in gs],
    }

    print(
        json.dumps(
            report,
            sort_keys=True,
            indent=2,
        )
    )

    if a.output:
        with a.output.open("x", encoding="utf-8") as f:
            json.dump(
                report,
                f,
                sort_keys=True,
                indent=2,
            )
            f.write("\n")

    if a.phase == "manuscript" and not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()