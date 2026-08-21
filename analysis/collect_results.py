#!/usr/bin/env python3
"""Regenerate processed evidence from immutable run directories."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.init_results import SCHEMAS


GENERIC = (
    "study1_correctness.csv",
    "study1_privacy.csv",
    "study1_linkage.csv",
    "study2_utility.csv",
    "study2_privacy.csv",
    "study2_cut_comparison.csv",
    "study2_hmac_controls.csv",
    "study2_work_evasion.csv",
)

STUDY1_PRODUCTS = {
    "study1_correctness.csv",
    "study1_privacy.csv",
    "study1_linkage.csv",
}

STUDY2_PRODUCTS = {
    "study2_utility.csv",
    "study2_privacy.csv",
    "study2_hmac_controls.csv",
    "study2_work_evasion.csv",
}


def mean(values):
    x = list(values)
    return sum(x) / len(x) if x else ""


def median(values):
    x = sorted(values)
    n = len(x)

    if not n:
        return ""

    return x[n // 2] if n % 2 else (x[n // 2 - 1] + x[n // 2]) / 2


def atomic_csv(path, fields, rows):
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        dir=path.parent,
        text=True,
    )
    os.close(fd)

    try:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=fields,
                extrasaction="ignore",
            )
            w.writeheader()
            w.writerows(rows)

        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def nested(x, *keys, default=""):
    for k in keys:
        if not isinstance(x, dict):
            return default
        x = x.get(k)

    return default if x is None else x


def percentile(values, q):
    x = sorted(values)

    if not x:
        return ""

    pos = (len(x) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)

    return x[lo] if lo == hi else x[lo] + (x[hi] - x[lo]) * (pos - lo)


def ping_metrics(text):
    values = [
        float(x)
        for x in re.findall(
            r"time[=<]([\d.]+)\s*ms",
            text or "",
        )
    ]

    loss_match = re.search(
        r"([\d.]+)% packet loss",
        text or "",
    )

    return (
        percentile(values, 0.5),
        percentile(values, 0.95),
        loss_match.group(1) if loss_match else "",
    )


def link_metrics(record):
    text = nested(record, "ethtool", "stdout") or ""

    speed = re.search(
        r"^\s*Speed:\s*(.+)$",
        text,
        re.MULTILINE,
    )
    duplex = re.search(
        r"^\s*Duplex:\s*(.+)$",
        text,
        re.MULTILINE,
    )

    if speed:
        return (
            speed.group(1).strip(),
            duplex.group(1).strip() if duplex else "",
        )

    wireless = nested(record, "iw_link", "stdout") or ""

    bitrate = re.search(
        r"^\s*tx bitrate:\s*(.+)$",
        wireless,
        re.MULTILINE,
    )

    return (
        bitrate.group(1).strip() if bitrate else "",
        "wireless" if bitrate else "",
    )


def manifest_state(record):
    zones = nested(
        record,
        "hardware",
        "thermal_and_throttle",
        "zones",
        default=[],
    )

    temperature = (
        zones[0].get("millidegrees_c") / 1000
        if zones and zones[0].get("millidegrees_c") is not None
        else ""
    )

    throttle = nested(
        record,
        "hardware",
        "thermal_and_throttle",
        "vcgencmd_get_throttled",
        "stdout",
    )

    governors = nested(
        record,
        "hardware",
        "cpu_governors",
        default={},
    )

    return (
        temperature,
        throttle,
        json.dumps(
            governors,
            sort_keys=True,
            separators=(",", ":"),
        ),
        nested(
            record,
            "hardware",
            "memory",
            "MemAvailable_bytes",
        ),
    )


def pi_unthrottled(record):
    if not nested(
        record,
        "hardware",
        "raspberry_pi_model",
    ):
        return True

    return "0x0" in (
        nested(
            record,
            "hardware",
            "thermal_and_throttle",
            "vcgencmd_get_throttled",
            "stdout",
        )
        or ""
    )


def peak_rss(record):
    sampled = record.get(
        "sampled_rss",
        record.get("sampled", []),
    )

    sampled_peak = max(
        (
            int(x.get("rss_bytes", 0))
            for x in sampled
        ),
        default=0,
    )

    return max(
        int(record.get("os_peak_rss_bytes", 0)),
        sampled_peak,
    )


def message_totals(path):
    out = {
        "sent": {
            "payload": 0,
            "serialized": 0,
            "socket": 0,
        },
        "received": {
            "payload": 0,
            "serialized": 0,
            "socket": 0,
        },
    }

    if not path.is_file():
        return out

    for line in path.read_text().splitlines():
        r = json.loads(line)
        d = out[r["direction"]]

        d["payload"] += int(r["payload_bytes"])
        d["serialized"] += int(r["serialized_bytes"])
        d["socket"] += int(r["socket_bytes"])

    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--root",
        type=Path,
        required=True,
    )

    a = p.parse_args()

    root = a.root.resolve()
    raw = root / "results" / "raw"
    out = root / "results" / "processed"

    # Direct per-run evidence products.
    for name in GENERIC:
        fields = SCHEMAS[name].split(",")
        rr = []

        for x in sorted(raw.glob(f"*/{name}")):
            meta_path = x.parent / "run.json"

            if not meta_path.is_file():
                continue

            meta = json.loads(meta_path.read_text())

            if (
                name in STUDY1_PRODUCTS
                and meta.get("study") != "study1"
            ):
                continue

            if (
                name in STUDY2_PRODUCTS
                and meta.get("study") != "study2"
            ):
                continue

            # This table is regenerated below from the filtered learning and
            # systems evidence; never ingest a stale per-run copy.
            if name == "study2_cut_comparison.csv":
                continue

            rr.extend(read_csv(x))

        atomic_csv(
            out / name,
            fields,
            rr,
        )

    # Frozen dataset identity is copied, not recomputed from outcomes.
    atomic_csv(
        out / "dataset_manifest.csv",
        SCHEMAS["dataset_manifest.csv"].split(","),
        read_csv(
            root / "manifests" / "dataset_manifest.csv"
        ),
    )

    ops = []

    for x in sorted(
        raw.glob("*/operations_*.csv")
    ):
        ops.extend(read_csv(x))

    atomic_csv(
        out / "study1_operation_trace.csv",
        SCHEMAS["study1_operation_trace.csv"].split(","),
        ops,
    )

    hardware = []
    network = []
    s1res = []
    s2res = []

    for run in sorted(
        x
        for x in raw.iterdir()
        if x.is_dir()
        and (x / "run.json").is_file()
    ):
        rm = json.loads(
            (run / "run.json").read_text()
        )
        rid = rm["run_id"]
        configuration = rm["configuration"]

        for start in sorted(
            run.glob("node_*_start.json")
        ):
            s = json.loads(start.read_text())
            role = s["role"]

            endp = run / f"node_{role}_end.json"

            e = (
                json.loads(endp.read_text())
                if endp.is_file()
                else {}
            )

            temp = nested(
                s,
                "hardware",
                "thermal_and_throttle",
                "zones",
                default=[],
            )

            temp_c = (
                temp[0].get("millidegrees_c") / 1000
                if temp
                and temp[0].get("millidegrees_c") is not None
                else ""
            )

            th = nested(
                s,
                "hardware",
                "thermal_and_throttle",
                "vcgencmd_get_throttled",
                "stdout",
            )

            model = (
                nested(
                    s,
                    "hardware",
                    "raspberry_pi_model",
                )
                or nested(
                    s,
                    "hardware",
                    "system_product",
                )
            )

            hardware.append(
                {
                    "run_id": rid,
                    "role": role,
                    "hostname": s["hostname"],
                    "model": model,
                    "board_revision": nested(
                        s,
                        "hardware",
                        "board_revision",
                    ),
                    "cpu_model": nested(
                        s,
                        "hardware",
                        "cpu_model",
                    ),
                    "cores": nested(
                        s,
                        "hardware",
                        "logical_cpus",
                    ),
                    "threads": nested(
                        s,
                        "hardware",
                        "logical_cpus",
                    ),
                    "ram_bytes": nested(
                        s,
                        "hardware",
                        "memory",
                        "MemTotal_bytes",
                    ),
                    "os": nested(
                        s,
                        "software",
                        "os",
                    ),
                    "kernel": nested(
                        s,
                        "software",
                        "kernel",
                    ),
                    "runtime": nested(
                        s,
                        "software",
                        "python",
                    ),
                    "git_commit": nested(
                        s,
                        "reproducibility",
                        "git_commit",
                    ),
                    "config_hashes": json.dumps(
                        nested(
                            s,
                            "reproducibility",
                            "configuration_file_sha256",
                            default={},
                        ),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "dataset_manifest_hash": nested(
                        s,
                        "reproducibility",
                        "dataset_manifest_sha256",
                    ),
                    "seed": nested(
                        s,
                        "reproducibility",
                        "random_seed",
                    ),
                    "start_time": s.get(
                        "captured_at_utc",
                        "",
                    ),
                    "end_time": e.get(
                        "captured_at_utc",
                        "",
                    ),
                    "command_line": nested(
                        s,
                        "reproducibility",
                        "command_line",
                    ),
                    "temperature_c": temp_c,
                    "throttled": th,
                    "available_memory_bytes": nested(
                        s,
                        "hardware",
                        "memory",
                        "MemAvailable_bytes",
                    ),
                    "storage_total_bytes": nested(
                        s,
                        "hardware",
                        "storage",
                        "total_bytes",
                    ),
                    "storage_free_bytes": nested(
                        s,
                        "hardware",
                        "storage",
                        "free_bytes",
                    ),
                }
            )

        for npth in sorted(
            run.glob("network_*.json")
        ):
            n = json.loads(npth.read_text())

            rtt_median, p95, loss = ping_metrics(
                nested(
                    n,
                    "ping_10",
                    "stdout",
                )
            )

            speed, duplex = link_metrics(n)

            network.append(
                {
                    "run_id": rid,
                    "role": n["role"],
                    "peer": n["peer"],
                    "interface": n.get(
                        "interface",
                        "",
                    ),
                    "link_speed": speed,
                    "duplex": duplex,
                    "rtt_median_ms": rtt_median,
                    "rtt_p95_ms": p95,
                    "packet_loss_percent": loss,
                    "captured_at_utc": n[
                        "captured_at_utc"
                    ],
                }
            )

        # Resource evidence is defined only by the three fresh-process systems
        # repetitions. Learning, attack, pilot, and warm-up processes must not
        # enter systems summaries merely because they also emitted monitors.
        if (
            rm.get("study") != "systems"
            or rm.get("warmup")
        ):
            continue

        if (
            not isinstance(
                rm.get("repetition"),
                int,
            )
            or not 1 <= rm["repetition"] <= 3
        ):
            continue

        for rp in sorted(
            run.glob("resources_*.json")
        ):
            role = rp.stem.removeprefix(
                "resources_"
            )

            r = json.loads(rp.read_text())

            msgs = message_totals(
                run / f"messages_{role}.jsonl"
            )

            sp = run / f"node_{role}_start.json"
            ep = run / f"node_{role}_end.json"
            npth = run / f"network_{role}.json"

            gate = (
                sp.is_file()
                and ep.is_file()
                and npth.is_file()
            )

            sm = (
                json.loads(sp.read_text())
                if sp.is_file()
                else {}
            )

            em = (
                json.loads(ep.read_text())
                if ep.is_file()
                else {}
            )

            nm = (
                json.loads(npth.read_text())
                if npth.is_file()
                else {}
            )

            gate = (
                gate
                and pi_unthrottled(sm)
                and pi_unthrottled(em)
            )

            (
                temperature,
                throttle_start,
                governor,
                available,
            ) = manifest_state(sm)

            _, throttle_end, _, _ = manifest_state(
                em
            )

            rtt_median, _, _ = ping_metrics(
                nested(
                    nm,
                    "ping_10",
                    "stdout",
                )
            )

            throttle = json.dumps(
                {
                    "start": throttle_start,
                    "end": throttle_end,
                },
                sort_keys=True,
                separators=(",", ":"),
            )

            cold_path = (
                run / f"cold_start_{role}.json"
            )

            cold = (
                json.loads(
                    cold_path.read_text()
                ).get("cold_start_ns", "")
                if cold_path.is_file()
                else ""
            )

            e2e_files = sorted(
                run.glob(
                    "aggregator_round_*_timing.json"
                )
            )

            e2e = (
                sum(
                    json.loads(
                        x.read_text()
                    )["end_to_end_round_ns"]
                    for x in e2e_files
                )
                / len(e2e_files)
                if e2e_files
                else ""
            )

            common = {
                "run_id": rid,
                "repetition": rm.get(
                    "repetition",
                    "",
                ),
                "configuration": configuration,
                "role": role,
                "temperature_c": temperature,
                "throttled": throttle,
                "governor": governor,
                "available_memory_bytes": available,
                "rtt_median_ms": rtt_median,
                "direct_energy_joules": "",
                "gate_pass": str(gate).lower(),
            }

            if configuration.startswith("I-"):
                rounds = (
                    read_csv(
                        run
                        / f"owner_{role}_rounds.csv"
                    )
                    if (
                        run
                        / f"owner_{role}_rounds.csv"
                    ).is_file()
                    else []
                )

                setup = (
                    json.loads(
                        (
                            run
                            / f"owner_{role}_setup.json"
                        ).read_text()
                    )
                    if (
                        run
                        / f"owner_{role}_setup.json"
                    ).is_file()
                    else {}
                )

                active = (
                    sum(
                        int(
                            x[
                                "owner_active_cpu_ns"
                            ]
                        )
                        for x in rounds
                    )
                    / len(rounds)
                    if rounds
                    else r.get(
                        "process_cpu_ns",
                        "",
                    )
                )

                online = (
                    sum(
                        int(
                            x[
                                "owner_protocol_online_ns"
                            ]
                        )
                        for x in rounds
                    )
                    / len(rounds)
                    if rounds
                    else ""
                )

                inventory = (
                    json.loads(
                        (
                            run
                            / "study1_key_inventory.json"
                        ).read_text()
                    )
                    if role == "A"
                    and (
                        run
                        / "study1_key_inventory.json"
                    ).is_file()
                    else {}
                )

                persistent = sum(
                    x["bytes"]
                    for x in inventory.get(
                        "files",
                        {},
                    ).values()
                )

                s1res.append(
                    {
                        **common,
                        "encryption_setup_ns": setup.get(
                            "encryption_and_delegation_setup_ns",
                            "",
                        ),
                        "owner_active_cpu_ns": active,
                        "owner_peak_rss_mib": (
                            peak_rss(r) / 1048576
                        ),
                        "upload_payload_bytes": msgs[
                            "sent"
                        ]["payload"],
                        "upload_serialized_bytes": msgs[
                            "sent"
                        ]["serialized"],
                        "download_payload_bytes": msgs[
                            "received"
                        ]["payload"],
                        "download_serialized_bytes": msgs[
                            "received"
                        ]["serialized"],
                        "sent_bytes": msgs["sent"][
                            "socket"
                        ],
                        "received_bytes": msgs[
                            "received"
                        ]["socket"],
                        "persistent_state_bytes": persistent,
                        "cold_start_ns": cold,
                        "per_round_online_ns": online,
                        "end_to_end_round_ns": e2e,
                    }
                )

            elif configuration.startswith("II-"):
                online = ""
                ownerp = (
                    run
                    / f"owner_{role}_rounds.jsonl"
                )

                ownerrows = (
                    [
                        json.loads(x)
                        for x in ownerp.read_text().splitlines()
                    ]
                    if ownerp.is_file()
                    else []
                )

                if ownerrows:
                    online = (
                        sum(
                            x[
                                "owner_protocol_online_ns"
                            ]
                            for x in ownerrows
                        )
                        / len(ownerrows)
                    )

                ar = (
                    run
                    / "aggregator_rounds.jsonl"
                )

                a2e = (
                    [
                        json.loads(x)[
                            "end_to_end_round_ns"
                        ]
                        for x in ar.read_text().splitlines()
                    ]
                    if ar.is_file()
                    else []
                )

                s2e2e = (
                    sum(a2e) / len(a2e)
                    if a2e
                    else ""
                )

                persistent = (
                    ownerrows[-1].get(
                        "replay_state_bytes",
                        0,
                    )
                    + ownerrows[-1].get(
                        "hmac_key_bytes",
                        0,
                    )
                    if ownerrows
                    else 0
                )

                owneractive = (
                    sum(
                        x[
                            "owner_active_cpu_ns"
                        ]
                        for x in ownerrows
                    )
                    / len(ownerrows)
                    if ownerrows
                    else r.get(
                        "process_cpu_ns",
                        "",
                    )
                )

                s2res.append(
                    {
                        **common,
                        "owner_active_cpu_ns": owneractive,
                        "owner_protocol_online_ns": online,
                        "end_to_end_round_ns": s2e2e,
                        "peak_rss_mib": (
                            peak_rss(r) / 1048576
                        ),
                        "payload_bytes_sent": msgs[
                            "sent"
                        ]["payload"],
                        "serialized_bytes_sent": msgs[
                            "sent"
                        ]["serialized"],
                        "socket_bytes_sent": msgs[
                            "sent"
                        ]["socket"],
                        "payload_bytes_received": msgs[
                            "received"
                        ]["payload"],
                        "serialized_bytes_received": msgs[
                            "received"
                        ]["serialized"],
                        "socket_bytes_received": msgs[
                            "received"
                        ]["socket"],
                        "persistent_state_bytes": persistent,
                        "cold_start_ns": cold,
                    }
                )

    atomic_csv(
        out / "hardware_manifest.csv",
        SCHEMAS["hardware_manifest.csv"].split(","),
        hardware,
    )

    atomic_csv(
        out / "network_manifest.csv",
        SCHEMAS["network_manifest.csv"].split(","),
        network,
    )

    atomic_csv(
        out / "study1_resources.csv",
        SCHEMAS["study1_resources.csv"].split(","),
        s1res,
    )

    atomic_csv(
        out / "study2_resources.csv",
        SCHEMAS["study2_resources.csv"].split(","),
        s2res,
    )

    # Main early/deep table: learning means and independent systems medians
    # remain visibly distinct in the unit label.
    privacy = read_csv(
        out / "study2_privacy.csv"
    )
    utility = read_csv(
        out / "study2_utility.csv"
    )
    cut = []

    for cfg in (
        "II-U-E",
        "II-U-D",
    ):

        def mean_metric(secret):
            vals = [
                float(r["balanced_accuracy"])
                for r in privacy
                if r["configuration"] == cfg
                and r["round_id"] == "10"
                and r["view"] == "z_and_g"
                and r["secret"] == secret
            ]
            return mean(vals)

        u = [
            r
            for r in utility
            if r["configuration"] == cfg
            and r["round_id"] == "10"
        ]

        rr = [
            r
            for r in s2res
            if r["configuration"] == cfg
            and r["role"] == "O_1"
            and r["gate_pass"] == "true"
        ]

        med = lambda k: median(
            float(x[k])
            for x in rr
            if x.get(k, "") != ""
        )

        cut.append(
            {
                "seed": "learning_mean_n3__systems_median_n3",
                "configuration": cfg,
                "activity_balanced_accuracy": mean_metric(
                    "activity"
                ),
                "subject_balanced_accuracy": mean_metric(
                    "subject"
                ),
                "test_balanced_accuracy": mean(
                    float(
                        x[
                            "test_balanced_accuracy"
                        ]
                    )
                    for x in u
                ),
                "test_macro_f1": mean(
                    float(
                        x[
                            "test_macro_f1"
                        ]
                    )
                    for x in u
                ),
                "test_cross_entropy": mean(
                    float(
                        x[
                            "test_cross_entropy"
                        ]
                    )
                    for x in u
                ),
                "owner_active_cpu_ns": med(
                    "owner_active_cpu_ns"
                ),
                "owner_peak_rss_mib": med(
                    "peak_rss_mib"
                ),
                "bytes_sent": med(
                    "socket_bytes_sent"
                ),
                "bytes_received": med(
                    "socket_bytes_received"
                ),
                "persistent_state_bytes": med(
                    "persistent_state_bytes"
                ),
                "round_wall_ns": med(
                    "end_to_end_round_ns"
                ),
            }
        )

    atomic_csv(
        out / "study2_cut_comparison.csv",
        SCHEMAS["study2_cut_comparison.csv"].split(","),
        cut,
    )


if __name__ == "__main__":
    main()