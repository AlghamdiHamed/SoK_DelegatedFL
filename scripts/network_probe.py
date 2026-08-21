#!/usr/bin/env python3
"""Capture negotiated link details and measured ICMP RTT before a run."""
from __future__ import annotations
import argparse, json, os, socket, subprocess
from datetime import datetime, timezone
from pathlib import Path


def cmd(argv):
    try:
        env = os.environ.copy()
        env["PATH"] = env.get("PATH", "") + ":/usr/sbin:/sbin"
        p = subprocess.run(argv, text=True, capture_output=True, timeout=30, check=False, env=env)
        return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except Exception as exc:
        return {"argv": argv, "error": repr(exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--role", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--peer", required=True)
    ap.add_argument("--interface")
    a = ap.parse_args()
    run = a.root / "results" / "raw" / a.run_id
    if not (run / "run.json").is_file(): raise SystemExit("run ID not allocated")
    route = cmd(["ip", "route", "get", a.peer])
    interface = a.interface
    if not interface and "stdout" in route:
        words = route["stdout"].split()
        if "dev" in words: interface = words[words.index("dev") + 1]
    record = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "role": a.role,
              "hostname": socket.gethostname(), "peer": a.peer, "route": route,
              "ping_10": cmd(["ping", "-n", "-c", "10", "-W", "2", a.peer]),
              "interface": interface, "ethtool": cmd(["ethtool", interface]) if interface else None,
              "iw_link": cmd(["iw", "dev", interface, "link"]) if interface else None,
              "ip_link": cmd(["ip", "-s", "-j", "link", "show", "dev", interface]) if interface else None}
    out = run / f"network_{a.role}.json"
    with out.open("x", encoding="utf-8") as f: json.dump(record, f, sort_keys=True, indent=2); f.write("\n")
    print(out)

if __name__ == "__main__": main()
