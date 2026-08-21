#!/usr/bin/env python3
"""Create the same immutable run identity on another physical node."""
from __future__ import annotations
import argparse,json,os
from pathlib import Path
def main():
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,required=True);p.add_argument("--run-json",type=Path,required=True);a=p.parse_args();raw=a.run_json.read_bytes();meta=json.loads(raw);rid=meta["run_id"]
    if not meta.get("immutable") or "/" in rid or rid in (".",".."):raise SystemExit("invalid run metadata")
    dest=a.root.resolve()/"results"/"raw"/rid;os.mkdir(dest,0o700)
    with (dest/"run.json").open("xb") as f:f.write(raw)
    print(dest)
if __name__=="__main__":main()

