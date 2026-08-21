#!/usr/bin/env python3
"""Collect per-node reproducibility evidence without mutating prior evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def command(argv: list[str]) -> dict:
    try:
        p = subprocess.run(argv, text=True, capture_output=True, timeout=30, check=False)
        return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "error": repr(exc)}


def read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def config_hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): sha256(p) for p in sorted((root / "configs").iterdir()) if p.is_file()}


def cpu_governors() -> dict[str, str]:
    out = {}
    for p in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")):
        out[p.parent.parent.name] = read_text(str(p)) or "unreadable"
    return out


def cpu_frequencies() -> dict[str, dict[str, str | None]]:
    out = {}
    for d in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq")):
        out[d.parent.name] = {
            "scaling_cur_freq_khz": read_text(str(d / "scaling_cur_freq")),
            "cpuinfo_max_freq_khz": read_text(str(d / "cpuinfo_max_freq")),
        }
    return out


def thermal() -> dict:
    zones = []
    for p in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        raw = read_text(str(p))
        zones.append({"path": str(p), "millidegrees_c": int(raw) if raw and raw.lstrip("-").isdigit() else None})
    return {"zones": zones, "vcgencmd_get_throttled": command(["vcgencmd", "get_throttled"]),
            "vcgencmd_measure_clock_arm": command(["vcgencmd", "measure_clock", "arm"])}


def memory() -> dict:
    keys = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    values = {}
    text = read_text("/proc/meminfo") or ""
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in keys:
            values[key] = rest.strip()
            fields = rest.split()
            if fields and fields[0].isdigit():
                values[key + "_bytes"] = int(fields[0]) * (1024 if len(fields) > 1 and fields[1].lower() == "kb" else 1)
    return values


def git_commit(root: Path) -> str | None:
    result = command(["git", "-C", str(root), "rev-parse", "HEAD"])
    return result.get("stdout", "").strip() or None


def manifest(args: argparse.Namespace) -> dict:
    root = args.root.resolve()
    usage = shutil.disk_usage(root)
    pi_model = read_text("/proc/device-tree/model")
    system_product = read_text("/sys/class/dmi/id/product_name")
    system_vendor = read_text("/sys/class/dmi/id/sys_vendor")
    chassis_type = read_text("/sys/class/dmi/id/chassis_type")
    cpuinfo = read_text("/proc/cpuinfo")
    cpu_model = next((line.split(":", 1)[1].strip() for line in (cpuinfo or "").splitlines() if line.lower().startswith(("model name", "hardware"))), platform.processor())
    dataset_hash = read_text(str(root / "manifests" / "dataset_manifest.sha256"))
    return {
        "schema_version": 1,
        "phase": args.phase,
        "run_id": args.run_id,
        "role": args.role,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "hardware": {
            "raspberry_pi_model": pi_model,
            "system_product": system_product,
            "system_vendor": system_vendor,
            "chassis_type": chassis_type,
            "board_revision": next((line.split(":", 1)[1].strip() for line in (cpuinfo or "").splitlines() if line.startswith("Revision")), None),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_model": cpu_model,
            "cpuinfo": cpuinfo,
            "logical_cpus": os.cpu_count(),
            "memory": memory(),
            "storage": {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free},
            "cpu_governors": cpu_governors(),
            "cpu_frequencies": cpu_frequencies(),
            "thermal_and_throttle": thermal(),
        },
        "software": {
            "os": platform.platform(), "kernel": platform.release(), "python": sys.version,
            "go_version": command(["go", "version"]), "go_env": command(["go", "env"]),
            "uname": command(["uname", "-a"]), "lscpu": command(["lscpu"]), "free_b": command(["free", "-b"]),
            "pip_freeze": command([sys.executable, "-m", "pip", "freeze"]),
            "thread_configuration": {k: os.environ.get(k) for k in ("GOMAXPROCS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")},
        },
        "reproducibility": {
            "git_commit": git_commit(root), "configuration_file_sha256": config_hashes(root),
            "dataset_manifest_sha256": dataset_hash, "random_seed": args.seed,
            "command_line": args.command_line,
        },
        "network_state": {
            "ip_address": command(["ip", "-j", "address"]),
            "ip_link": command(["ip", "-s", "-j", "link"]),
            "route": command(["ip", "route"]),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--role", required=True, choices=("O_1", "O_2", "O_3", "E", "A"))
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--phase", required=True, choices=("start", "end"))
    ap.add_argument("--seed", required=True, type=int)
    ap.add_argument("--command-line", required=True)
    args = ap.parse_args()
    run_dir = args.root.resolve() / "results" / "raw" / args.run_id
    if not (run_dir / "run.json").is_file():
        raise SystemExit(f"unknown run ID: {args.run_id}")
    out = run_dir / f"node_{args.role}_{args.phase}.json"
    with out.open("x", encoding="utf-8") as f:
        json.dump(manifest(args), f, sort_keys=True, indent=2)
        f.write("\n")
    print(out)


if __name__ == "__main__":
    main()
