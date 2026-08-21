#!/usr/bin/env bash
# Run the complete DFL experiment matrix from the executor workstation while
# launching the amended, physically separate aggregator over SSH.
set -Eeuo pipefail
shopt -s inherit_errexit nullglob
umask 077

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

PY=${DFL_PYTHON:-$ROOT/.venv/bin/python}
PI1=${DFL_PI1:-pi1@192.168.0.146}
PI2=${DFL_PI2:-pi2@192.168.0.66}
PI3=${DFL_PI3:-pi3@192.168.0.118}
EXECUTOR_IP=${DFL_EXECUTOR_IP:-192.168.0.57}
AGGREGATOR_HOST=${DFL_AGGREGATOR_HOST:-ubuntu@192.168.0.49}
AGGREGATOR_IP=${DFL_AGGREGATOR_IP:-192.168.0.49}
S1_KEY_STATE_REMOTE_NAME=${DFL_STUDY1_KEY_STATE_REMOTE_NAME:-study1-final-optiplex-keys}
S1_KEY_STATE_REMOTE=""
HMAC_EXEC_KEY=${DFL_HMAC_EXECUTOR_KEY:-$HOME/dfl-secure/study2_O1_E_executor.key}
HMAC_OWNER_REMOTE_NAME=${DFL_HMAC_OWNER_KEY_NAME:-study2_O1_E_owner.key}
RUN_SCOPE=${DFL_RUN_SCOPE:-full}
[[ $RUN_SCOPE == full || $RUN_SCOPE == smoke ]] || { echo "DFL_RUN_SCOPE must be full or smoke" >&2; exit 2; }

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=6)
RSYNC_RSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
THREAD_ENV=(OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 GOMAXPROCS=1)
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 GOMAXPROCS=1
SEEDS=(101 211 307)
S1_CONFIGS=(I-L I-P I-OR I-ER)
S2_CONFIGS=(II-L II-S II-U-E II-U-D II-U-H)
WORK_MODES=(partial_0.25 partial_0.5 partial_0.75 stale_work sign_modification sample_substitution)

if [[ -n ${DFL_SESSION_ID:-} ]]; then
  SESSION_ID=$DFL_SESSION_ID
else
  SESSION_ID=$($PY - <<'PY'
from datetime import datetime, timezone
import secrets
print(datetime.now(timezone.utc).strftime("full-%Y%m%dT%H%M%SZ-")+secrets.token_hex(4))
PY
  )
fi
[[ $SESSION_ID =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid DFL_SESSION_ID" >&2; exit 2; }
ORCH="$ROOT/results/orchestration/$SESSION_ID"
mkdir -p "$ORCH/logs"
exec 9>"$ORCH/runner.lock"
flock -n 9 || { echo "another runner owns session $SESSION_ID" >&2; exit 2; }
exec > >(tee -a "$ORCH/runner.log") 2>&1

LEDGER="$ORCH/completed.tsv"
ALLOCATED="$ORCH/allocated.tsv"
: >>"$LEDGER"
: >>"$ALLOCATED"

A_PID=""
E_PID=""
ACTIVE_RUN_ID=""
REMOTE_PIDS=()
REMOTE_LABELS=()

log() { printf '\n[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { echo "FATAL: $*" >&2; exit 1; }

cleanup_active() {
  local status=$?
  trap - EXIT INT TERM HUP
  set +e
  [[ -z $E_PID ]] || kill -TERM "$E_PID" 2>/dev/null
  if [[ -n $A_PID && -n $ACTIVE_RUN_ID ]]; then
    ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" "test ! -s ~/experiment/results/raw/$ACTIVE_RUN_ID/process_A.pid || kill -TERM \"\$(cat ~/experiment/results/raw/$ACTIVE_RUN_ID/process_A.pid)\"" 2>/dev/null || true
  fi
  [[ -z $A_PID ]] || kill -TERM "$A_PID" 2>/dev/null
  for pid in "${REMOTE_PIDS[@]:-}"; do kill -TERM "$pid" 2>/dev/null; done
  [[ -z $E_PID ]] || wait "$E_PID" 2>/dev/null
  [[ -z $A_PID ]] || wait "$A_PID" 2>/dev/null
  for pid in "${REMOTE_PIDS[@]:-}"; do wait "$pid" 2>/dev/null; done
  if (( status != 0 )); then
    echo "Runner stopped with status $status. Resume safely with:" >&2
    echo "  DFL_SESSION_ID=$SESSION_ID DFL_RUN_SCOPE=$RUN_SCOPE scripts/run_full_experiment.sh" >&2
  fi
  exit "$status"
}
trap cleanup_active EXIT INT TERM HUP

shell_join() {
  local out="" q
  for q in "$@"; do printf -v q '%q' "$q"; out+="$q "; done
  printf '%s' "${out% }"
}

completed_run() {
  local unit=$1
  awk -F '\t' -v key="$unit" '$1==key {print $2; exit}' "$LEDGER"
}

already_complete() {
  local unit=$1 rid
  rid=$(completed_run "$unit")
  [[ -n $rid ]] || return 1
  [[ -f results/raw/$rid/run_complete.json ]] || die "ledger points to incomplete run: $unit -> $rid"
  log "SKIP completed $unit ($rid)"
  return 0
}

mark_complete() {
  local unit=$1 rid=$2
  "$PY" - "$ROOT/results/raw/$rid/run_complete.json" "$SESSION_ID" "$unit" <<'PY'
from datetime import datetime, timezone
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
with p.open("x",encoding="utf-8") as f:
    json.dump({"session_id":sys.argv[2],"unit":sys.argv[3],"completed_at_utc":datetime.now(timezone.utc).isoformat()},f,sort_keys=True,indent=2)
    f.write("\n")
PY
  printf '%s\t%s\n' "$unit" "$rid" >>"$LEDGER"
}

remote_home() {
  ssh "${SSH_OPTS[@]}" "$1" 'printf %s "$HOME"'
}

preflight() {
  log "preflight"
  for command in git ssh scp rsync flock sha256sum ss; do command -v "$command" >/dev/null || die "missing command: $command"; done
  [[ -x $PY ]] || die "missing experiment Python: $PY"
  [[ -x study1/bin/owner && -x study1/bin/delegate && -x study1/bin/aggregator ]] || die "Study-I binaries are not built"
  [[ -s manifests/dataset_manifest.sha256 ]] || die "dataset manifest is not frozen"
  local aggregator_home
  aggregator_home=$(remote_home "$AGGREGATOR_HOST")
  S1_KEY_STATE_REMOTE="$aggregator_home/dfl-secure/$S1_KEY_STATE_REMOTE_NAME"
  ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" "test -s '$S1_KEY_STATE_REMOTE/secret.key' && test -s '$S1_KEY_STATE_REMOTE/rotation.keys'" || die "invalid Study-I key state on A: $S1_KEY_STATE_REMOTE"
  [[ $($PY -c 'import json;print(json.load(open("results/processed/study1_ckks_parameters.json"))["status"])') == frozen ]] || die "CKKS parameters are not frozen"
  [[ -z $(git status --porcelain --untracked-files=no) ]] || die "tracked worktree changes exist; commit the runner and collection fixes first"
  [[ $(go env GOVERSION) == go1.26.5 ]] || die "local Go is not go1.26.5"

  local commit dataset host remote_commit remote_dataset
  commit=$(git rev-parse HEAD)
  dataset=$(<manifests/dataset_manifest.sha256)
  for host in "$PI1" "$PI2" "$PI3"; do
    remote_commit=$(ssh "${SSH_OPTS[@]}" "$host" 'git -C ~/experiment rev-parse HEAD')
    remote_dataset=$(ssh "${SSH_OPTS[@]}" "$host" 'cat ~/experiment/manifests/dataset_manifest.sha256')
    [[ $remote_commit == "$commit" ]] || die "$host commit differs: $remote_commit != $commit"
    [[ $remote_dataset == "$dataset" ]] || die "$host dataset hash differs"
    [[ -z $(ssh "${SSH_OPTS[@]}" "$host" 'git -C ~/experiment status --porcelain --untracked-files=no') ]] || die "$host has tracked worktree changes"
    ssh "${SSH_OPTS[@]}" "$host" 'test "$(go env GOVERSION)" = go1.26.5; test -x ~/experiment/.venv/bin/python'
  done

  remote_commit=$(ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" 'git -C ~/experiment rev-parse HEAD')
  remote_dataset=$(ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" 'cat ~/experiment/manifests/dataset_manifest.sha256')
  [[ $remote_commit == "$commit" ]] || die "$AGGREGATOR_HOST commit differs: $remote_commit != $commit"
  [[ $remote_dataset == "$dataset" ]] || die "$AGGREGATOR_HOST dataset hash differs"
  [[ -z $(ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" 'git -C ~/experiment status --porcelain --untracked-files=no') ]] || die "$AGGREGATOR_HOST has tracked worktree changes"
  ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" 'test "$(go env GOVERSION)" = go1.26.5; test -x ~/experiment/.venv/bin/python'

  "$PY" - <<'PY'
import numpy, scipy, sklearn, torch
assert torch.__version__.split("+")[0]=="2.6.0"
assert numpy.__version__=="2.1.3"
assert scipy.__version__=="1.14.1"
assert sklearn.__version__=="1.5.2"
PY
  for host in "$PI1" "$PI2" "$PI3"; do
    ssh "${SSH_OPTS[@]}" "$host" 'cd ~/experiment && .venv/bin/python -c '\''import numpy,scipy,sklearn,torch;assert torch.__version__.split("+")[0]=="2.6.0";assert numpy.__version__=="2.1.3";assert scipy.__version__=="1.14.1";assert sklearn.__version__=="1.5.2"'\'''
  done
  ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" 'cd ~/experiment && .venv/bin/python -c '\''import numpy,scipy,sklearn,torch;assert torch.__version__.split("+")[0]=="2.6.0";assert numpy.__version__=="2.1.3";assert scipy.__version__=="1.14.1";assert sklearn.__version__=="1.5.2"'\'''
  if ss -H -ltn '( sport = :7001 )' | grep -q .; then
    ss -H -ltnp '( sport = :7001 )' >&2 || true
    die "executor port 7001 is already in use"
  fi
  ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" '! ss -H -ltn "( sport = :7002 )" | grep -q .' || die "aggregator port 7002 is already in use"
  log "disk availability"
  df -h "$ROOT"
  for host in "$PI1" "$PI2" "$PI3"; do ssh "${SSH_OPTS[@]}" "$host" 'df -h ~/experiment | tail -n 1'; done
  ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" 'df -h ~/experiment | tail -n 1'
}

ensure_hmac_key() {
  log "HMAC pair-key provisioning check"
  local pi1_home owner_path local_exists=0 remote_exists=0 owner_tmp
  pi1_home=$(remote_home "$PI1")
  HMAC_OWNER_REMOTE="$pi1_home/dfl-secure/$HMAC_OWNER_REMOTE_NAME"
  [[ -s $HMAC_EXEC_KEY ]] && local_exists=1
  ssh "${SSH_OPTS[@]}" "$PI1" "test -s '$HMAC_OWNER_REMOTE'" && remote_exists=1 || true
  if (( local_exists == 0 && remote_exists == 0 )); then
    mkdir -p -m 700 "$(dirname "$HMAC_EXEC_KEY")"
    owner_tmp=$(mktemp /tmp/dfl-hmac-owner.XXXXXX)
    "$PY" scripts/provision_hmac.py --owner-output "${owner_tmp}.key" --executor-output "$HMAC_EXEC_KEY"
    scp "${SSH_OPTS[@]}" "${owner_tmp}.key" "$PI1:/tmp/$HMAC_OWNER_REMOTE_NAME.upload"
    ssh "${SSH_OPTS[@]}" "$PI1" "umask 077; mkdir -p -m 700 '$pi1_home/dfl-secure'; test ! -e '$HMAC_OWNER_REMOTE'; cp '/tmp/$HMAC_OWNER_REMOTE_NAME.upload' '$HMAC_OWNER_REMOTE'; chmod 600 '$HMAC_OWNER_REMOTE'; python3 -c \"from pathlib import Path;Path('/tmp/$HMAC_OWNER_REMOTE_NAME.upload').unlink()\""
    "$PY" - "${owner_tmp}.key" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).unlink()
PY
  elif (( local_exists != remote_exists )); then
    die "only one HMAC pair-key copy exists; restore its counterpart instead of silently rotating the key"
  fi
  [[ $(stat -c %s "$HMAC_EXEC_KEY") == 32 ]] || die "executor HMAC key is not 32 bytes"
  ssh "${SSH_OPTS[@]}" "$PI1" "test \"\$(stat -c %s '$HMAC_OWNER_REMOTE')\" = 32"
  local local_hash remote_hash
  local_hash=$(sha256sum "$HMAC_EXEC_KEY" | awk '{print $1}')
  remote_hash=$(ssh "${SSH_OPTS[@]}" "$PI1" "sha256sum '$HMAC_OWNER_REMOTE'" | awk '{print $1}')
  [[ $local_hash == "$remote_hash" ]] || die "owner/executor HMAC key copies differ"
}

write_session_metadata() {
  [[ -f $ORCH/session.json ]] && return
  "$PY" - "$ORCH/session.json" "$SESSION_ID" "$(git rev-parse HEAD)" "$EXECUTOR_IP" "$AGGREGATOR_IP" <<'PY'
from datetime import datetime, timezone
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
with p.open("x",encoding="utf-8") as f:
    json.dump({"session_id":sys.argv[2],"git_commit":sys.argv[3],"created_at_utc":datetime.now(timezone.utc).isoformat(),"executor":sys.argv[4],"aggregator":sys.argv[5],"aggregator_executor_colocated":False,"secret_key_os_isolation_between_A_and_E":True,"eligible_as_five_physical_role_result":True,"aggregator_hardware_amendment":"Dell OptiPlex 7010; Intel Core i5-3470; approximately 8 GiB RAM; Ubuntu 24.04.3 replaces planned i7/16 GiB laptop","lean_final_amendment":{"learning_seeds":[101,211,307],"global_rounds":10,"systems_repetitions":3,"systems_warmups":2,"expected_run_units":90},"runtime_deviation":"Python 3.12.3 x86_64 and Python 3.13.5 ARM64 replace prescribed Python 3.11","energy":"omitted unless direct measurement artifacts are independently supplied"},f,sort_keys=True,indent=2)
    f.write("\n")
PY
}

ALLOCATED_RUN_ID=""
allocate_run() {
  local unit=$1 study=$2 configuration=$3 seed=$4 repetition=$5 warmup=$6
  local args=(scripts/new_run.py --root "$ROOT" --study "$study" --configuration "$configuration" --seed "$seed")
  [[ -z $repetition ]] || args+=(--repetition "$repetition")
  (( warmup == 0 )) || args+=(--warmup)
  ALLOCATED_RUN_ID=$($PY "${args[@]}")
  local rid=$ALLOCATED_RUN_ID run="$ROOT/results/raw/$ALLOCATED_RUN_ID"
  "$PY" - "$run/orchestration.json" "$SESSION_ID" "$unit" <<'PY'
from datetime import datetime,timezone
import json,sys
from pathlib import Path
with Path(sys.argv[1]).open("x",encoding="utf-8") as f:
    json.dump({"session_id":sys.argv[2],"unit":sys.argv[3],"topology":"five_physical_roles_E_192.168.0.57_A_192.168.0.49","five_physical_role_gate":True,"aggregator_hardware_amendment":"OptiPlex_7010_i5_3470_8GiB","recorded_at_utc":datetime.now(timezone.utc).isoformat()},f,sort_keys=True,indent=2);f.write("\n")
PY
  printf '%s\t%s\n' "$unit" "$rid" >>"$ALLOCATED"
  for host in "$PI1" "$PI2" "$PI3"; do
    scp "${SSH_OPTS[@]}" "$run/run.json" "$host:/tmp/$rid.run.json" >/dev/null
    ssh "${SSH_OPTS[@]}" "$host" "cd ~/experiment && python3 scripts/join_run.py --root . --run-json '/tmp/$rid.run.json'" >/dev/null
  done
  scp "${SSH_OPTS[@]}" "$run/run.json" "$AGGREGATOR_HOST:/tmp/$rid.run.json" >/dev/null
  ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" "cd ~/experiment && python3 scripts/join_run.py --root . --run-json '/tmp/$rid.run.json'" >/dev/null
  log "allocated $unit -> $rid"
}

observe_local_start() {
  local role=$1 rid=$2 seed=$3 command_line=$4
  "$PY" manifests/collect.py --root . --role "$role" --run-id "$rid" --phase start --seed "$seed" --command-line "$command_line" >/dev/null
  "$PY" scripts/network_probe.py --root . --role "$role" --run-id "$rid" --peer "$AGGREGATOR_IP" >/dev/null
}

observe_local_end() {
  local role=$1 rid=$2 seed=$3 command_line=$4
  "$PY" manifests/collect.py --root . --role "$role" --run-id "$rid" --phase end --seed "$seed" --command-line "$command_line" >/dev/null
}

wait_port() {
  local address=$1 port=$2 pid=$3 attempt

  for ((attempt=1; attempt<=6000; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      die "server process $pid exited before port $port opened"
    fi

    if [[ $address == 127.0.0.1 ]]; then
      if ss -H -ltn "( sport = :$port )" | grep -q .; then
        return 0
      fi
    else
      [[ $address == "$AGGREGATOR_IP" ]] ||
        die "unsupported listener address: $address"

      if ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" \
        "ss -H -ltn '( sport = :$port )' | grep -q ." \
        >/dev/null 2>&1
      then
        return 0
      fi
    fi

    sleep 0.1
  done

  die "timeout waiting for port $port"
}

launch_s1_aggregator() {
  local rid=$1 seed=$2 configuration=$3
  local ssh_log="$ROOT/results/raw/$rid/process_A_ssh.log"
  ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" bash -s -- "$rid" "$seed" "$configuration" "$S1_KEY_STATE_REMOTE" "$EXECUTOR_IP" >"$ssh_log" 2>&1 <<'REMOTE' &
set -uo pipefail
rid=$1;seed=$2;configuration=$3;key_state=$4;executor_ip=$5
cd ~/experiment || exit 90
export GOMAXPROCS=1
cmd=(study1/bin/aggregator --configuration "$configuration" --listen 0.0.0.0:7002 --run-id "$rid" --run-dir "results/raw/$rid")
[[ $configuration != I-OR && $configuration != I-ER ]] || cmd+=(--key-state "$key_state")
printf -v command_line '%q ' "${cmd[@]}"
python3 manifests/collect.py --root . --role A --run-id "$rid" --phase start --seed "$seed" --command-line "$command_line" || exit 91
python3 scripts/network_probe.py --root . --role A --run-id "$rid" --peer "$executor_ip" || exit 92
env GOMAXPROCS=1 "${cmd[@]}" >"results/raw/$rid/process_A.log" 2>&1 & child=$!
echo "$child" >"results/raw/$rid/process_A.pid"
trap 'kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; exit 143' TERM INT HUP
set +e;wait "$child";rc=$?;set -e
end_rc=0;python3 manifests/collect.py --root . --role A --run-id "$rid" --phase end --seed "$seed" --command-line "$command_line" || end_rc=$?
(( rc != 0 || end_rc == 0 )) || rc=$end_rc
exit "$rc"
REMOTE
  A_PID=$!
}

launch_s2_aggregator() {
  local rid=$1 seed=$2
  local ssh_log="$ROOT/results/raw/$rid/process_A_ssh.log"
  ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" bash -s -- "$rid" "$seed" "$EXECUTOR_IP" >"$ssh_log" 2>&1 <<'REMOTE' &
set -uo pipefail
rid=$1;seed=$2;executor_ip=$3
cd ~/experiment || exit 90
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
py=.venv/bin/python
cmd=("$py" -m study2.aggregator.main --listen 0.0.0.0:7002 --run-dir "results/raw/$rid" --seed "$seed")
printf -v command_line '%q ' "${cmd[@]}"
"$py" manifests/collect.py --root . --role A --run-id "$rid" --phase start --seed "$seed" --command-line "$command_line" || exit 91
"$py" scripts/network_probe.py --root . --role A --run-id "$rid" --peer "$executor_ip" || exit 92
env OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 "${cmd[@]}" >"results/raw/$rid/process_A.log" 2>&1 & child=$!
echo "$child" >"results/raw/$rid/process_A.pid"
trap 'kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; exit 143' TERM INT HUP
set +e;wait "$child";rc=$?;set -e
end_rc=0;"$py" manifests/collect.py --root . --role A --run-id "$rid" --phase end --seed "$seed" --command-line "$command_line" || end_rc=$?
(( rc != 0 || end_rc == 0 )) || rc=$end_rc
exit "$rc"
REMOTE
  A_PID=$!
}

launch_s1_owner() {
  local host=$1 role=$2 rid=$3 seed=$4 configuration=$5
  local log_path="$ROOT/results/raw/$rid/process_${role}.log"
  ssh "${SSH_OPTS[@]}" "$host" bash -s -- "$role" "$rid" "$seed" "$configuration" "$EXECUTOR_IP" "$AGGREGATOR_IP" >"$log_path" 2>&1 <<'REMOTE' &
set -uo pipefail
role=$1;rid=$2;seed=$3;configuration=$4;executor_ip=$5;aggregator_ip=$6
cd ~/experiment || exit 90
export GOMAXPROCS=1
cmd=(study1/bin/owner --owner "$role" --configuration "$configuration" --records "manifests/study1_${role}_records.csv" --executor "$executor_ip:7001" --aggregator "$aggregator_ip:7002" --run-id "$rid" --run-dir "results/raw/$rid" --task-id "$rid")
printf -v command_line '%q ' "${cmd[@]}"
python3 manifests/collect.py --root . --role "$role" --run-id "$rid" --phase start --seed "$seed" --command-line "$command_line" || exit 91
python3 scripts/network_probe.py --root . --role "$role" --run-id "$rid" --peer "$aggregator_ip" || exit 92
env GOMAXPROCS=1 "${cmd[@]}" & child=$!
trap 'kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; exit 143' TERM INT HUP
set +e;wait "$child";rc=$?;set -e
end_rc=0;python3 manifests/collect.py --root . --role "$role" --run-id "$rid" --phase end --seed "$seed" --command-line "$command_line" || end_rc=$?
(( rc != 0 || end_rc == 0 )) || rc=$end_rc
exit "$rc"
REMOTE
  REMOTE_PIDS+=("$!");REMOTE_LABELS+=("$role")
}

launch_s2_owner() {
  local host=$1 role=$2 rid=$3 seed=$4 configuration=$5 work_mode=$6 collect_privacy=$7
  local key_path="";[[ $role == O_1 && $configuration == II-U-H ]] && key_path=$HMAC_OWNER_REMOTE
  local key_arg=${key_path:-NONE}
  local log_path="$ROOT/results/raw/$rid/process_${role}.log"
  ssh "${SSH_OPTS[@]}" "$host" bash -s -- "$role" "$rid" "$seed" "$configuration" "$EXECUTOR_IP" "$AGGREGATOR_IP" "$work_mode" "$collect_privacy" "$key_arg" >"$log_path" 2>&1 <<'REMOTE' &
set -uo pipefail
role=$1;rid=$2;seed=$3;configuration=$4;executor_ip=$5;aggregator_ip=$6;work_mode=$7;collect_privacy=$8;key_path=$9
[[ $key_path != NONE ]] || key_path=""
cd ~/experiment || exit 90
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
py=.venv/bin/python
cmd=("$py" -m study2.owner.main --owner "$role" --configuration "$configuration" --records "manifests/study2_${role}_records.csv" --executor "$executor_ip:7001" --aggregator "$aggregator_ip:7002" --run-dir "results/raw/$rid" --seed "$seed" --task-id "$rid" --work-mode "$work_mode")
[[ -z $key_path ]] || cmd+=(--hmac-key "$key_path")
[[ $collect_privacy == 0 ]] || cmd+=(--collect-privacy)
printf -v command_line '%q ' "${cmd[@]}"
"$py" manifests/collect.py --root . --role "$role" --run-id "$rid" --phase start --seed "$seed" --command-line "$command_line" || exit 91
"$py" scripts/network_probe.py --root . --role "$role" --run-id "$rid" --peer "$aggregator_ip" || exit 92
env OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 "${cmd[@]}" & child=$!
trap 'kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; exit 143' TERM INT HUP
set +e;wait "$child";rc=$?;set -e
end_rc=0;"$py" manifests/collect.py --root . --role "$role" --run-id "$rid" --phase end --seed "$seed" --command-line "$command_line" || end_rc=$?
(( rc != 0 || end_rc == 0 )) || rc=$end_rc
exit "$rc"
REMOTE
  REMOTE_PIDS+=("$!");REMOTE_LABELS+=("$role")
}

wait_owners() {
  local failed=0 i rc
  set +e
  for i in "${!REMOTE_PIDS[@]}"; do
    wait "${REMOTE_PIDS[$i]}";rc=$?
    if (( rc != 0 )); then echo "owner ${REMOTE_LABELS[$i]} failed with $rc" >&2;failed=1;fi
  done
  set -e
  REMOTE_PIDS=();REMOTE_LABELS=()
  return "$failed"
}

stop_server() {
  local pid=$1 label=$2 rc=0
  [[ -n $pid ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then kill -TERM "$pid";fi
  set +e;wait "$pid";rc=$?;set -e
  (( rc == 0 )) || { echo "$label server exited with $rc" >&2;return "$rc"; }
}

stop_remote_aggregator() {
  local rid=$1 rc=0
  if kill -0 "$A_PID" 2>/dev/null; then
    ssh "${SSH_OPTS[@]}" "$AGGREGATOR_HOST" "test ! -s ~/experiment/results/raw/$rid/process_A.pid || kill -TERM \"\$(cat ~/experiment/results/raw/$rid/process_A.pid)\"" || true
  fi
  set +e;wait "$A_PID";rc=$?;set -e
  A_PID=""
  (( rc == 0 )) || { echo "A remote server exited with $rc" >&2;return "$rc"; }
}

sync_pis() {
  local rid=$1 host
  for host in "$PI1" "$PI2" "$PI3"; do
    rsync -a --ignore-existing --exclude run.json -e "$RSYNC_RSH" "$host:~/experiment/results/raw/$rid/" "$ROOT/results/raw/$rid/"
  done
}


sync_aggregator() {
  local rid=$1
  rsync -a --ignore-existing --exclude run.json -e "$RSYNC_RSH" "$AGGREGATOR_HOST:~/experiment/results/raw/$rid/" "$ROOT/results/raw/$rid/"
}

verify_round_files() {
  "$PY" - "$ROOT/results/raw/$1" "$2" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);study=sys.argv[2]
suffix="json" if study=="study1" else "bin"
models=list(p.glob(f"global_model_round_*.{suffix}"))
assert len(models)==10,(p,len(models))
for owner in ("O_1","O_2","O_3"):
    q=p/(f"owner_{owner}_rounds.csv" if study=="study1" else f"owner_{owner}_rounds.jsonl")
    assert q.is_file(),q
    lines=q.read_text().splitlines()
    assert len(lines)==(11 if study=="study1" else 10),(q,len(lines))
PY
}

run_study1() {
  local unit=$1 study=$2 configuration=$3 seed=$4 repetition=$5 warmup=$6 analyze=$7
  already_complete "$unit" && return
  allocate_run "$unit" "$study" "$configuration" "$seed" "$repetition" "$warmup"
  local rid=$ALLOCATED_RUN_ID
  local run="$ROOT/results/raw/$rid"
  ACTIVE_RUN_ID=$rid
  local e_cmd="not-participating: Study-I $configuration"
  if [[ $configuration != I-L ]]; then
    e_cmd=$(shell_join study1/bin/delegate --configuration "$configuration" --listen 0.0.0.0:7001 --aggregator "$AGGREGATOR_IP:7002" --run-id "$rid" --run-dir "$run")
  fi
  observe_local_start E "$rid" "$seed" "$e_cmd"
  launch_s1_aggregator "$rid" "$seed" "$configuration"
  wait_port "$AGGREGATOR_IP" 7002 "$A_PID"
  if [[ $configuration != I-L ]]; then
    env GOMAXPROCS=1 study1/bin/delegate --configuration "$configuration" --listen 0.0.0.0:7001 --aggregator "$AGGREGATOR_IP:7002" --run-id "$rid" --run-dir "$run" >"$run/process_E.log" 2>&1 &E_PID=$!
    wait_port 127.0.0.1 7001 "$E_PID"
  else E_PID="";fi
  launch_s1_owner "$PI1" O_1 "$rid" "$seed" "$configuration"
  launch_s1_owner "$PI2" O_2 "$rid" "$seed" "$configuration"
  launch_s1_owner "$PI3" O_3 "$rid" "$seed" "$configuration"
  local owners_rc=0 server_rc=0
  wait_owners || owners_rc=$?
  [[ -z $E_PID ]] || stop_server "$E_PID" E || server_rc=$?;E_PID=""
  stop_remote_aggregator "$rid" || server_rc=$?
  observe_local_end E "$rid" "$seed" "$e_cmd"
  sync_aggregator "$rid"
  sync_pis "$rid"
  ACTIVE_RUN_ID=""
  (( owners_rc == 0 && server_rc == 0 )) || die "Study-I process failure in $rid"
  verify_round_files "$rid" study1
  if (( analyze == 1 )) && [[ $configuration == I-OR || $configuration == I-ER ]]; then
    study1/bin/validate --records manifests/study1_records.csv --run-dir "$run" --run-id "$rid" --seed "$seed" --configuration "$configuration" --output "$run/study1_correctness.csv"
    "$PY" - "$run/study1_correctness.csv" <<'PY'
import csv,sys
r=list(csv.DictReader(open(sys.argv[1])));assert len(r)==10 and all(x["gate_pass"]=="true" for x in r)
PY
  fi
  if (( analyze == 1 )) && [[ $configuration == I-OR ]]; then
    study1/bin/privacy --records manifests/study1_records.csv --run-dir "$run" --run-id "$rid" --seed "$seed" --output "$run/study1_privacy.csv" --linkage-output "$run/study1_linkage.csv"
  fi
  if (( analyze == 1 )) && [[ $configuration == I-ER ]]; then
    study1/bin/viewcheck --run-dir "$run" --records manifests/study1_records.csv --run-id "$rid" --seed "$seed" --output "$run/study1_privacy.csv"
  fi
  mark_complete "$unit" "$rid"
}

run_study2() {
  local unit=$1 study=$2 configuration=$3 seed=$4 repetition=$5 warmup=$6 work_mode=$7 collect_privacy=$8 honest_rid=$9
  already_complete "$unit" && return
  allocate_run "$unit" "$study" "$configuration" "$seed" "$repetition" "$warmup"
  local rid=$ALLOCATED_RUN_ID
  local run="$ROOT/results/raw/$rid"
  ACTIVE_RUN_ID=$rid
  local e_cmd="not-participating: Study-II $configuration"
  local e_args=()
  if [[ $configuration != II-L ]]; then
    e_args=("$PY" -m study2.delegate.main --listen 0.0.0.0:7001 --run-dir "$run")
    [[ $configuration != II-U-H ]] || e_args+=(--hmac-key "$HMAC_EXEC_KEY")
    e_cmd=$(shell_join "${e_args[@]}")
  fi
  observe_local_start E "$rid" "$seed" "$e_cmd"
  launch_s2_aggregator "$rid" "$seed"
  wait_port "$AGGREGATOR_IP" 7002 "$A_PID"
  if [[ $configuration != II-L ]]; then
    env "${THREAD_ENV[@]}" "${e_args[@]}" >"$run/process_E.log" 2>&1 &E_PID=$!
    wait_port 127.0.0.1 7001 "$E_PID"
  else E_PID="";fi
  launch_s2_owner "$PI1" O_1 "$rid" "$seed" "$configuration" "$work_mode" "$collect_privacy"
  launch_s2_owner "$PI2" O_2 "$rid" "$seed" "$configuration" honest 0
  launch_s2_owner "$PI3" O_3 "$rid" "$seed" "$configuration" honest 0
  local owners_rc=0 server_rc=0
  wait_owners || owners_rc=$?
  [[ -z $E_PID ]] || stop_server "$E_PID" E || server_rc=$?;E_PID=""
  stop_remote_aggregator "$rid" || server_rc=$?
  observe_local_end E "$rid" "$seed" "$e_cmd"
  sync_aggregator "$rid"
  sync_pis "$rid"
  ACTIVE_RUN_ID=""
  (( owners_rc == 0 && server_rc == 0 )) || die "Study-II process failure in $rid"
  verify_round_files "$rid" study2
  if [[ $study == study2 && $work_mode == honest ]]; then
    "$PY" -m study2.aggregator.evaluate --run-dir "$run" --run-id "$rid" --records manifests/study2_records.csv --seed "$seed" --configuration "$configuration" --output "$run/study2_utility.csv"
    if (( collect_privacy == 1 )); then
      "$PY" -m study2.attacks.run_privacy --run-dir "$run" --records manifests/study2_records.csv --run-id "$rid" --seed "$seed" --configuration "$configuration" --output "$run/study2_privacy.csv"
    fi
    if [[ $configuration == II-U-H ]]; then
      "$PY" -m study2.attacks.hmac_controls --run-id "$rid" --seed "$seed" --key "$HMAC_EXEC_KEY" --output "$run/study2_hmac_controls.csv"
    fi
  fi
  if [[ $study == study2 && $work_mode != honest ]]; then
    [[ -n $honest_rid && -f results/raw/$honest_rid/run_complete.json ]] || die "missing honest comparator for $unit"
    local manipulation=$work_mode fraction=""
    case $work_mode in
      partial_0.25) manipulation=partial_execution;fraction=0.25;;
      partial_0.5) manipulation=partial_execution;fraction=0.50;;
      partial_0.75) manipulation=partial_execution;fraction=0.75;;
      stale_work) manipulation=stale_work;;
      sign_modification) manipulation=sign_modification;;
      sample_substitution) manipulation=sample_substitution;;
    esac
    "$PY" -m study2.attacks.analyze_work_evasion --honest-run "results/raw/$honest_rid" --attack-run "$run" --records manifests/study2_records.csv --run-id "$rid" --seed "$seed" --manipulation "$manipulation" --fraction "$fraction" --output "$run/study2_work_evasion.csv"
  fi
  mark_complete "$unit" "$rid"
}

run_learning_matrix() {
  local seed cfg unit honest mode
  log "Study I: three matched learning seeds across four configurations"
  for seed in "${SEEDS[@]}"; do
    for cfg in "${S1_CONFIGS[@]}"; do
      unit="learning.study1.seed${seed}.${cfg}"
      run_study1 "$unit" study1 "$cfg" "$seed" "" 0 1
    done
  done

  log "Study II: three matched learning seeds across five honest configurations"
  for seed in "${SEEDS[@]}"; do
    for cfg in "${S2_CONFIGS[@]}"; do
      unit="learning.study2.seed${seed}.${cfg}"
      if [[ $cfg == II-L ]]; then
        run_study2 "$unit" study2 "$cfg" "$seed" "" 0 honest 0 ""
      else
        run_study2 "$unit" study2 "$cfg" "$seed" "" 0 honest 1 ""
      fi
    done
  done

  log "Study II: authorized work-evasion matrix"
  for seed in "${SEEDS[@]}"; do
    honest=$(completed_run "learning.study2.seed${seed}.II-U-H")
    [[ -n $honest ]] || die "missing II-U-H honest run for seed $seed"
    for mode in "${WORK_MODES[@]}"; do
      unit="work.study2.seed${seed}.${mode}"
      run_study2 "$unit" study2 II-U-H "$seed" "" 0 "$mode" 0 "$honest"
    done
  done
}

run_systems_matrix() {
  local study block order cfg unit row
  local schedule_rows=()
  mapfile -t schedule_rows < <(awk 'NR > 1 && NF { print }' configs/systems_schedule.csv)
  [[ ${#schedule_rows[@]} -eq 27 ]] || die "expected 27 systems schedule rows; found ${#schedule_rows[@]}"
  log "Systems warm-ups: first two prespecified blocks, unreported"
  for row in "${schedule_rows[@]}"; do
    IFS=, read -r study block order cfg <<<"$row"
    (( block <= 2 )) || continue
    unit="systems.warmup.${study}.block${block}.${cfg}"
    if [[ $study == study1 ]]; then
      run_study1 "$unit" systems "$cfg" 101 "$block" 1 0
    else
      run_study2 "$unit" systems "$cfg" 101 "$block" 1 honest 0 ""
    fi
  done

  log "Systems measurements: three blocked fresh-process repetitions"
  for row in "${schedule_rows[@]}"; do
    IFS=, read -r study block order cfg <<<"$row"
    unit="systems.measured.${study}.repetition${block}.${cfg}"
    if [[ $study == study1 ]]; then
      run_study1 "$unit" systems "$cfg" 101 "$block" 0 0
    else
      run_study2 "$unit" systems "$cfg" 101 "$block" 0 honest 0 ""
    fi
  done
}

build_outputs() {
  log "building processed outputs from this session's immutable completed runs"
  local analysis_root="$ORCH/analysis_root" gate_stamp gate_file type_file
  [[ $(awk 'NF{n++}END{print n+0}' "$LEDGER") == 90 ]] || die "expected 90 completed run units"
  mkdir -p "$analysis_root/results/raw"
  "$PY" - "$ROOT" "$analysis_root" "$LEDGER" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]).resolve();analysis=Path(sys.argv[2]).resolve();ledger=Path(sys.argv[3])
for name in ("manifests","analysis","preprocessing"):
    p=analysis/name
    if not p.exists():p.symlink_to(root/name,target_is_directory=True)
processed=analysis/"results"/"processed"
if not processed.exists():processed.symlink_to(root/"results"/"processed",target_is_directory=True)
raw=analysis/"results"/"raw"
for line in ledger.read_text().splitlines():
    if not line.strip():continue
    _,rid=line.split("\t",1);source=root/"results"/"raw"/rid;target=raw/rid
    if not (source/"run_complete.json").is_file():raise SystemExit(f"incomplete ledger run: {rid}")
    if not target.exists():target.symlink_to(source,target_is_directory=True)
PY
  "$PY" analysis/init_results.py --root "$analysis_root"
  "$PY" analysis/collect_results.py --root "$analysis_root"
  "$PY" - "$ROOT/results/processed" <<'PY'
import csv,sys
from pathlib import Path
p=Path(sys.argv[1])
expected={
 "hardware_manifest.csv":450,
 "network_manifest.csv":450,
 "study1_correctness.csv":60,
 "study1_privacy.csv":57,
 "study1_linkage.csv":54,
 "study1_resources.csv":57,
 "study2_utility.csv":150,
 "study2_privacy.csv":288,
 "study2_cut_comparison.csv":2,
 "study2_hmac_controls.csv":24,
 "study2_work_evasion.csv":180,
 "study2_resources.csv":72,
}
for name,want in expected.items():
    with (p/name).open(newline="",encoding="utf-8") as f:got=sum(1 for _ in csv.DictReader(f))
    if got!=want:raise SystemExit(f"{name}: expected {want} rows, found {got}")
print("all processed row-count boundaries passed")
PY
  gate_stamp=$(date -u +%Y%m%dT%H%M%SZ)
  type_file="$ORCH/type1_validation_$gate_stamp.csv"
  "$PY" analysis/validate_type1.py --raw-root "$analysis_root/results/raw" --output "$type_file"
  "$PY" analysis/generate_tables.py --root "$analysis_root" --threat-model-only
  gate_file="$ORCH/gates_$gate_stamp.json"
  "$PY" analysis/gates.py --root "$analysis_root" --phase check --output "$gate_file" | tee "$ORCH/gates_$gate_stamp.stdout.json"
  "$PY" - "$ORCH/completion_$gate_stamp.md" "$SESSION_ID" "$LEDGER" "$gate_file" <<'PY'
from pathlib import Path
import json,sys
out,session,ledger,gates=Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]),Path(sys.argv[4])
entries=[x.split("\t",1) for x in ledger.read_text().splitlines() if x.strip()]
report=json.loads(gates.read_text());failed=[x for x in report["gates"] if not x["passed"]]
with out.open("x",encoding="utf-8") as f:
    f.write(f"# Full experiment session {session}\n\n")
    f.write(f"Completed immutable run units: {len(entries)}.\n\n")
    f.write("Aggregator A ran on the physically separate, amended Dell OptiPlex 7010 at 192.168.0.49; executor E ran at 192.168.0.57. The hardware amendment is disclosed in STUDY_PROTOCOL.md.\n\n")
    f.write("## Failed gates\n\n")
    if failed:
        for x in failed:f.write(f"- {x['group']} / {x['name']}: {x['detail']}\n")
    else:f.write("- None.\n")
PY
  log "processed outputs complete"
  echo "Session: $SESSION_ID"
  echo "Completion report: $ORCH/completion_$gate_stamp.md"
  echo "Gate report: $gate_file"
  echo "Processed CSVs: $ROOT/results/processed"
}

main() {
  preflight
  ensure_hmac_key
  write_session_metadata
  log "session $SESSION_ID begins; resume with DFL_SESSION_ID=$SESSION_ID"
  if [[ $RUN_SCOPE == smoke ]]; then
    run_study1 "smoke.study1.seed101.I-OR" study1 I-OR 101 "" 0 1
    run_study2 "smoke.study2.seed101.II-U-H" study2 II-U-H 101 "" 0 honest 1 ""
    log "five-role smoke complete; raw artifacts retained under session $SESSION_ID"
    return
  fi
  run_learning_matrix
  run_systems_matrix
  build_outputs
}

main "$@"
