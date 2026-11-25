#!/usr/bin/env bash
# Same small launcher for idle calibration and real trials. No learning logic.
set -u
out=$1; shift
cg=$(sed -n 's/^0:://p' /proc/self/cgroup)
cg="/sys/fs/cgroup$cg"
printf '%s\n' "$cg" > "$out/cgroup.path"
cat "$cg/memory.max" > "$out/cap.before"
cat "$cg/memory.swap.max" > "$out/swap.before"
"$@" & app=$!
printf '%s\n' "$app" > "$out/app.pid"
wait "$app"; rc=$?
cat "$cg/memory.peak" > "$out/cgroup.peak"
cat "$cg/memory.events" > "$out/cgroup.events"
printf '%s\n' "$rc" > "$out/app.exit"
exit "$rc"
