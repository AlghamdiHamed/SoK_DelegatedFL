#!/usr/bin/env python3
"""PAMAP2 Protocol feasibility, frozen selection, and overlap validation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import yaml

# PAMAP2 Protocol activities. IDs 9, 10, and 11 belong to optional
# recordings and must not be requested from the Protocol-only dataset.
ACTIVITIES = (1, 2, 3, 4, 5, 6, 7, 12, 13, 16, 17, 24)
SUBJECT_OWNER = {i: f"O_{1 + (i - 1) // 3}" for i in range(1, 10)}
MOTION = {
    "hand": tuple(range(4, 16)),
    "chest": tuple(range(21, 33)),
    "ankle": tuple(range(38, 50)),
}
CHANNELS = ("acc16_x", "acc16_y", "acc16_z", "acc6_x", "acc6_y", "acc6_z",
            "gyro_x", "gyro_y", "gyro_z", "magnet_x", "magnet_y", "magnet_z")
PARTITIONS = ("learning", "utility_test", "attacker_auxiliary", "attacker_target")


@dataclass(frozen=True)
class Window:
    study: str
    record_id: str
    owner: str
    subject: str
    activity: int
    interval_id: int
    partition: str
    raw_start_row: int
    raw_end_row: int
    raw_start_timestamp: float
    raw_end_timestamp: float
    purity: float
    features: tuple[float, ...]
    target: float | None


def config(root: Path, study: str) -> dict:
    with (root / "configs" / f"{study}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def expected_files(pamap2: Path) -> list[Path]:
    files = [pamap2 / f"subject10{i}.dat" for i in range(1, 10)]
    missing = [str(p) for p in files if not p.is_file()]
    if missing: raise ValueError("missing PAMAP2 Protocol files: " + ", ".join(missing))
    if "optional" in str(pamap2).lower(): raise ValueError("Optional recordings path is forbidden")
    return files


def contiguous_activity_intervals(activity: np.ndarray) -> Iterable[tuple[int, int, int]]:
    start = 0
    interval = 0
    for i in range(1, len(activity) + 1):
        if i == len(activity) or activity[i] != activity[start]:
            a = int(activity[start])
            if a in ACTIVITIES:
                yield interval, start, i
                interval += 1
            start = i


def partition_ranges(start: int, end: int, ratios: dict[str, float], guard: int) -> dict[str, tuple[int, int]]:
    n = end - start
    cumulative = [0.0]
    for p in PARTITIONS: cumulative.append(cumulative[-1] + float(ratios[p]))
    if not math.isclose(cumulative[-1], 1.0, abs_tol=1e-12): raise ValueError("partition ratios must sum to 1")
    bounds = [start + int(round(n * x)) for x in cumulative]
    bounds[0], bounds[-1] = start, end
    out = {}
    for i, p in enumerate(PARTITIONS):
        lo = bounds[i] + (guard if i else 0)
        hi = bounds[i + 1] - (guard if i + 1 < len(PARTITIONS) else 0)
        out[p] = (max(lo, start), min(max(lo, hi), end))
    return out


def interpolate_within_partition(x: np.ndarray) -> np.ndarray:
    out = x.astype(np.float64, copy=True)
    idx = np.arange(out.shape[0])
    for j in range(out.shape[1]):
        good = np.isfinite(out[:, j])
        if good.sum() >= 2:
            out[:, j] = np.interp(idx, idx[good], out[good, j], left=np.nan, right=np.nan)
    return out


def feature_schema(study: str) -> tuple[list[int], list[str]]:
    locations = ("chest",) if study == "study1" else ("hand", "chest", "ankle")
    cols, names = [], []
    for loc in locations:
        cols.extend(MOTION[loc]); names.extend([f"{loc}_{x}" for x in CHANNELS])
    return cols, names


def windows_for_subject(path: Path, subject_number: int, study: str, cfg: dict) -> list[Window]:
    raw = np.loadtxt(path, dtype=np.float64)
    if raw.ndim != 2 or raw.shape[1] != 54: raise ValueError(f"{path}: expected 54 PAMAP2 columns")
    activity = raw[:, 1].astype(np.int64)
    cols, names = feature_schema(study)
    schema_hash = hashlib.sha256((study + "|" + "|".join(names)).encode()).hexdigest()
    output = []
    for interval_id, begin, finish in contiguous_activity_intervals(activity):
        a = int(activity[begin])
        for partition, (lo, hi) in partition_ranges(begin, finish, cfg["partitions"], cfg["guard_rows_each_side"]).items():
            if hi - lo < cfg["window_rows"]: continue
            needed = [0, 1, 2, *cols]
            segment = interpolate_within_partition(raw[lo:hi, needed])
            # Generate at the declared stride, then retain a maximal non-overlapping pool.
            last_end = -1
            for offset in range(0, len(segment) - cfg["window_rows"] + 1, cfg["stride_rows"]):
                abs_start, abs_end = lo + offset, lo + offset + cfg["window_rows"] - 1
                if abs_start <= last_end: continue
                block = segment[offset:offset + cfg["window_rows"]]
                purity = float(np.mean(block[:, 1] == a))
                feat = block[:, 3:]
                hr = block[:, 2]
                if purity < cfg["purity_min"] or not np.isfinite(feat).all(): continue
                if study == "study1" and not np.isfinite(hr).all(): continue
                target = float(np.median(hr)) if study == "study1" else None
                key = f"{study}|S{subject_number}|{interval_id}|{partition}|{abs_start}|{abs_end}|{schema_hash}"
                output.append(Window(study, hashlib.sha256(key.encode()).hexdigest(), SUBJECT_OWNER[subject_number],
                                     f"S{subject_number}", a, interval_id, partition, abs_start, abs_end,
                                     float(raw[abs_start, 0]), float(raw[abs_end, 0]), purity,
                                     tuple(np.mean(feat, axis=0).tolist()), target))
                last_end = abs_end
    return output


def availability(windows: list[Window]) -> dict[tuple[str, int, str], int]:
    result = {}
    for w in windows:
        k = (w.owner, w.activity, w.partition); result[k] = result.get(k, 0) + 1
    return result


def uniform_counts(windows: list[Window], cfg: dict) -> dict[str, int]:
    avail = availability(windows); result = {}
    for part, target in cfg["selection_target_per_owner_activity"].items():
        cells = [avail.get((owner, a, part), 0) for owner in ("O_1", "O_2", "O_3") for a in ACTIVITIES]
        result[part] = min(int(target), min(cells))
        if result[part] <= 0: raise ValueError(f"no uniform positive selection supported for {part}; minimum availability={min(cells)}")
    return result


def select(windows: list[Window], counts: dict[str, int], seed: int) -> list[Window]:
    groups = {}
    for w in windows: groups.setdefault((w.owner, w.activity, w.partition), []).append(w)
    selected = []
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda w: w.record_id)
        derived = int(hashlib.sha256((str(seed) + "|" + "|".join(map(str, key))).encode()).hexdigest()[:16], 16)
        random.Random(derived).shuffle(items)
        selected.extend(items[:counts[key[2]]])
    return sorted(selected, key=lambda w: (w.study, w.owner, w.activity, w.partition, w.subject, w.raw_start_row))


def assert_no_temporal_overlap(windows: list[Window]) -> None:
    by_subject = {}
    for w in windows: by_subject.setdefault((w.study, w.subject), []).append(w)
    for key, items in by_subject.items():
        items = sorted(items, key=lambda w: (w.raw_start_row, w.raw_end_row))
        for i, left in enumerate(items):
            for right in items[i + 1:]:
                if right.raw_start_row > left.raw_end_row: break
                if left.partition != right.partition:
                    raise AssertionError(f"incompatible temporal overlap {key}: {left.record_id} {right.record_id}")
                if left.record_id != right.record_id:
                    raise AssertionError(f"selected windows overlap within partition {key}: {left.record_id} {right.record_id}")


def write_csv_exclusive(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames); w.writeheader(); w.writerows(rows)


def freeze(root: Path, pamap2: Path) -> None:
    all_selected = []
    feasibility = {}
    for study in ("study1", "study2"):
        cfg = config(root, study); windows = []
        for subject, path in enumerate(expected_files(pamap2), 1): windows.extend(windows_for_subject(path, subject, study, cfg))
        counts = uniform_counts(windows, cfg); chosen = select(windows, counts, 101); assert_no_temporal_overlap(chosen)
        if study == "study2":
            for part in ("attacker_auxiliary", "attacker_target"):
                for subject in ("S1", "S2", "S3"):
                    if not any(w.partition == part and w.subject == subject for w in chosen):
                        raise ValueError(f"closed-set subject probe has no {subject}/{part} support")
        all_selected.extend(chosen); feasibility[study] = {"available": {"|".join(map(str, k)): v for k, v in availability(windows).items()}, "frozen_counts": counts}
        names = feature_schema(study)[1]
        fields = ["record_id", "owner", "subject", "activity", "partition"] + [f"x_{i:02d}_{n}" for i, n in enumerate(names)]
        if study == "study1": fields.append("target_heart_rate")
        rows = []
        for x in chosen:
            row = {"record_id": x.record_id, "owner": x.owner, "subject": x.subject, "activity": x.activity, "partition": x.partition}
            row.update({fields[5 + i]: format(v, ".17g") for i, v in enumerate(x.features)})
            if study == "study1": row["target_heart_rate"] = format(x.target, ".17g")
            rows.append(row)
        write_csv_exclusive(root / "manifests" / f"{study}_records.csv", fields, rows)
        for owner in ("O_1", "O_2", "O_3"):
            write_csv_exclusive(root / "manifests" / f"{study}_{owner}_records.csv", fields, [r for r in rows if r["owner"] == owner])
    manifest_fields = ["study", "record_id", "owner", "subject", "activity", "interval_id", "partition",
                       "raw_start_row", "raw_end_row", "raw_start_timestamp", "raw_end_timestamp", "purity"]
    write_csv_exclusive(root / "manifests" / "dataset_manifest.csv", manifest_fields, [
        {"study": w.study, "record_id": w.record_id, "owner": w.owner, "subject": w.subject, "activity": w.activity,
         "interval_id": w.interval_id, "partition": w.partition, "raw_start_row": w.raw_start_row,
         "raw_end_row": w.raw_end_row, "raw_start_timestamp": format(w.raw_start_timestamp, ".17g"),
         "raw_end_timestamp": format(w.raw_end_timestamp, ".17g"), "purity": format(w.purity, ".17g")} for w in all_selected])
    with (root / "manifests" / "feasibility.json").open("x", encoding="utf-8") as f: json.dump(feasibility, f, sort_keys=True, indent=2); f.write("\n")
    h = hashlib.sha256()
    names = ["dataset_manifest.csv", "study1_records.csv", "study2_records.csv"] + [f"{study}_{owner}_records.csv" for study in ("study1", "study2") for owner in ("O_1", "O_2", "O_3")]
    for name in names:
        p = root / "manifests" / name; h.update(name.encode() + b"\0" + p.read_bytes())
    with (root / "manifests" / "dataset_manifest.sha256").open("x", encoding="ascii") as f: f.write(h.hexdigest() + "\n")


def read_manifest(root: Path) -> list[Window]:
    out = []
    with (root / "manifests" / "dataset_manifest.csv").open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Window(r["study"], r["record_id"], r["owner"], r["subject"], int(r["activity"]),
                              int(r["interval_id"]), r["partition"], int(r["raw_start_row"]), int(r["raw_end_row"]),
                              float(r["raw_start_timestamp"]), float(r["raw_end_timestamp"]), float(r["purity"]), (), None))
    return out


def validate(root: Path) -> None:
    ws = read_manifest(root); assert_no_temporal_overlap(ws)
    for w in ws:
        if w.owner != SUBJECT_OWNER[int(w.subject[1:])]: raise AssertionError("owner assignment changed")
        if w.activity not in ACTIVITIES or w.purity < 0.90: raise AssertionError("invalid activity/purity")
        if w.raw_end_timestamp < w.raw_start_timestamp: raise AssertionError("timestamp order")
    expected = (root / "manifests" / "dataset_manifest.sha256").read_text().strip()
    h = hashlib.sha256()
    names = ["dataset_manifest.csv", "study1_records.csv", "study2_records.csv"] + [f"{study}_{owner}_records.csv" for study in ("study1", "study2") for owner in ("O_1", "O_2", "O_3")]
    for name in names:
        p = root / "manifests" / name; h.update(name.encode() + b"\0" + p.read_bytes())
    if h.hexdigest() != expected: raise AssertionError("dataset manifest hash mismatch")
    print(json.dumps({"status": "PASS", "records": len(ws), "dataset_manifest_sha256": expected}))


def feasibility(root: Path, pamap2: Path) -> None:
    report = {}
    for study in ("study1", "study2"):
        cfg = config(root, study); ws = []
        for subject, path in enumerate(expected_files(pamap2), 1): ws.extend(windows_for_subject(path, subject, study, cfg))
        report[study] = {"available": {"|".join(map(str, k)): v for k, v in availability(ws).items()},
                         "uniform_counts": uniform_counts(ws, cfg)}
    print(json.dumps(report, sort_keys=True, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="command", required=True)
    for name in ("feasibility", "freeze"):
        p = sub.add_parser(name); p.add_argument("--root", type=Path, required=True); p.add_argument("--pamap2", type=Path, required=True)
    p = sub.add_parser("validate"); p.add_argument("--root", type=Path, required=True)
    a = ap.parse_args(); root = a.root.resolve()
    if a.command == "feasibility": feasibility(root, a.pamap2.resolve())
    elif a.command == "freeze": freeze(root, a.pamap2.resolve())
    else: validate(root)


if __name__ == "__main__": main()
