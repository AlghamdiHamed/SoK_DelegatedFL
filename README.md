# Delegated Federated Learning controlled experiments

This repository contains the reproducibility package for two controlled experiments accompanying a systematization of delegated federated learning (DFL). The experiments provide mechanism-level evidence for composition gaps identified by the systematization. They do not propose a new federated-learning protocol.

- **Study I—full delegation:** tests whether encrypted delegated inputs protect the executor's accumulated view when a correlated residual is opened.
- **Study II—partial delegation:** tests whether owner-local labels remain inferable from executor-visible intermediates and whether contextual HMAC establishes prescribed-work assurance.
- **Systems study:** measures how delegation and protection redistribute owner computation, memory, communication, state, setup, and round time.

The implementation uses native processes, length-prefixed TCP messages, and measured network traffic. It does not use Docker or Flower.

## Final experimental design

| Item | Frozen setting |
|---|---|
| Dataset | PAMAP2 Protocol recordings; optional recordings and activity 0 excluded |
| Owners | O_1={S1,S2,S3}, O_2={S4,S5,S6}, O_3={S7,S8,S9} |
| Activities | 1, 2, 3, 4, 5, 6, 7, 12, 13, 16, 17, 24 |
| Windowing | 2.56 s windows, 1.28 s stride, at least 90% activity purity |
| Learning seeds | 101, 211, 307 |
| Global rounds | 10 |
| Systems measurements | seed 101; 2 unreported warm-ups; 3 fresh-process repetitions |
| Full matrix | 90 immutable run units |
| Study-I runtime | Go 1.26.5; Lattigo v5.0.7 |
| Study-II runtime | Python 3.12.3 on x86-64 and Python 3.13.5 on ARM64; PyTorch 2.6.0 |

The final five-role testbed was:

| Role | Host used in the reference campaign | Function |
|---|---|---|
| O_1 | Raspberry Pi 4 | Constrained owner whose execution placement changes |
| O_2 | Raspberry Pi 4 | Ordinary local training in every configuration |
| O_3 | Raspberry Pi 5 | Ordinary local training in every configuration |
| E | Intel Core i9 workstation, 32 GB RAM | Delegate/executor and experiment controller |
| A | Dell OptiPlex 7010, Core i5-3470, 8 GB RAM | Physically separate aggregator and Study-I decryption authority |

The aggregator substitution and Python-version amendment are disclosed in [`STUDY_PROTOCOL.md`](STUDY_PROTOCOL.md). [`CLAIMS.md`](CLAIMS.md) defines the permitted interpretations.

## Configurations

### Study I

| ID | O_1 placement | Executor-visible data |
|---|---|---|
| I-L | Plaintext gradient computed locally | No delegated O_1 dataset |
| I-P | Plaintext dataset delegated | Plaintext O_1 training records |
| I-OR | CKKS inputs and targets; residual opened by trusted A | Ciphertexts, models, metadata, and linked plaintext residuals |
| I-ER | CKKS inputs, targets, and residual | Corresponding ciphertexts and metadata, but no plaintext residual |

All owners contribute one full-batch gradient per round. The aggregator performs sample-weighted synchronous federated gradient descent. In I-P, I-OR, and I-ER, O_1 performs no semantically necessary learning after delegation begins.

### Study II

| ID | O_1 execution path |
|---|---|
| II-L | Entire 36-128-64-32-12 network at the owner |
| II-S | Conventional split after the 128-unit layer |
| II-U-E | U-shaped split with an early cut: owner 36-128, executor 128-64-32, owner 32-12 |
| II-U-D | U-shaped split with a deeper cut: owner 36-128-64, executor 64-32, owner 32-12 |
| II-U-H | II-U-E plus contextual HMAC-SHA-256 |

O_2 and O_3 remain ordinary local-training clients. The privacy probe uses temporally disjoint auxiliary and target partitions. The work-evasion study covers partial execution, stale work, sign modification, and sample substitution by an authorized executor holding the valid HMAC key.

## Repository contents

```text
analysis/       Gate checks, result collection, statistics, and table generation
configs/        Frozen protocol, topology, seed, and systems schedules
manifests/      Frozen PAMAP2 record selections and manifest utilities
preprocessing/  PAMAP2 parsing, temporal partitioning, windowing, and validation
results/        Empty raw/processed locations plus frozen CKKS parameters
scripts/        Run allocation, monitoring, key provisioning, and orchestration
study1/         Go implementation of Study I
study2/         Python/PyTorch implementation of Study II
tests/          Protocol, preprocessing, integrity, and analysis tests
```

Raw PAMAP2 recordings, secret keys, run logs, and measured results are not part of the source repository.

## Prerequisites

Use five physical Linux hosts with routable TCP connectivity. Clone the same committed repository to `~/experiment` on every host. The executor must have passwordless SSH access to O_1, O_2, O_3, and A.

Required system tools include Git, OpenSSH, rsync, `flock`, `ss`, `ping`, `iw`, and `ethtool`. Ports 7001/TCP on E and 7002/TCP on A must be reachable from the owners and unused before the runner starts. Synchronize the clocks on all hosts.

Install Go 1.26.5 on every host. The build deliberately disables automatic Go toolchain substitution. Create a Python virtual environment on every host and install the exact dependency lock:

```bash
cd ~/experiment
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r study2/requirements.lock
make study1-build
```

Run the source tests on the executor:

```bash
cd ~/experiment
.venv/bin/python -m unittest discover -s tests -v
```

The dependency lock matches the runtimes used in the reference campaign. If a platform cannot install those exact versions, record and justify the amended lock before collecting results; do not combine results from different locks as a matched campaign.

## Dataset preparation

The repository includes the frozen record manifests and their SHA-256 digest. Validate them before deployment:

```bash
cd ~/experiment
.venv/bin/python preprocessing/pamap2.py validate --root .
```

To reproduce preprocessing from the raw data, obtain PAMAP2 separately and place only `Protocol/subject101.dat` through `Protocol/subject109.dat` under `data/pamap2/Protocol/`. Run feasibility and freezing in a disposable copy whose `manifests/` directory does not already contain generated record files, then compare its manifest digest and CSVs with this repository:

```bash
.venv/bin/python preprocessing/pamap2.py feasibility \
  --root . --pamap2 data/pamap2/Protocol
.venv/bin/python preprocessing/pamap2.py freeze \
  --root . --pamap2 data/pamap2/Protocol
.venv/bin/python preprocessing/pamap2.py validate --root .
```

Freezing uses exclusive file creation and will not overwrite the authoritative manifests.

## Configure the hosts

The reference addresses are recorded in `configs/network.yaml`. For a different testbed, edit `configs/network.yaml` and `configs/hardware.yaml`, commit those changes, and distribute that exact commit to all five hosts. Pass the same addresses to the runner through environment variables.

Example:

```bash
export DFL_PI1=pi1@192.168.0.146
export DFL_PI2=pi2@192.168.0.66
export DFL_PI3=pi3@192.168.0.118
export DFL_EXECUTOR_IP=192.168.0.57
export DFL_AGGREGATOR_HOST=ubuntu@192.168.0.49
export DFL_AGGREGATOR_IP=192.168.0.49
```

Verify passwordless SSH and the two experiment ports before starting. The runner also checks the Git commit, dataset digest, dependency versions, clean tracked worktrees, available storage, and port state.

## Provision keys

Generate the Study-I authority state once on A:

```bash
cd ~/experiment
mkdir -p -m 700 ~/dfl-secure
umask 077
study1/bin/keygen \
  --output ~/dfl-secure/study1-final-optiplex-keys
```

Only A may retain `secret.key`. The owners receive encryption material through the protocol, and E receives only the required evaluation material.

For II-U-H, the full runner provisions one 32-byte owner-executor pair key if neither copy exists. It stores the executor copy under `~/dfl-secure/` on E and the owner copy under `~/dfl-secure/` on O_1. If only one copy exists, the runner stops instead of silently rotating the key.

Never commit key material.

## Run a five-role smoke campaign

Run orchestration from E:

```bash
cd ~/experiment
source .venv/bin/activate

SESSION=smoke-five-role-$(date -u +%Y%m%dT%H%M%SZ)

DFL_SESSION_ID="$SESSION" \
DFL_RUN_SCOPE=smoke \
DFL_PI1="$DFL_PI1" \
DFL_PI2="$DFL_PI2" \
DFL_PI3="$DFL_PI3" \
DFL_EXECUTOR_IP="$DFL_EXECUTOR_IP" \
DFL_AGGREGATOR_HOST="$DFL_AGGREGATOR_HOST" \
DFL_AGGREGATOR_IP="$DFL_AGGREGATOR_IP" \
  scripts/run_full_experiment.sh
```

Smoke scope runs I-OR and II-U-H once and retains their immutable raw artifacts. Inspect both run directories before the full campaign.

## Run the complete experiment

Use a new session ID:

```bash
cd ~/experiment
source .venv/bin/activate

SESSION=full-dfl-$(date -u +%Y%m%dT%H%M%SZ)

DFL_SESSION_ID="$SESSION" \
DFL_RUN_SCOPE=full \
DFL_PI1="$DFL_PI1" \
DFL_PI2="$DFL_PI2" \
DFL_PI3="$DFL_PI3" \
DFL_EXECUTOR_IP="$DFL_EXECUTOR_IP" \
DFL_AGGREGATOR_HOST="$DFL_AGGREGATOR_HOST" \
DFL_AGGREGATOR_IP="$DFL_AGGREGATOR_IP" \
  scripts/run_full_experiment.sh
```

The full scope executes 90 immutable run units. It runs the learning/privacy matrix, authorized work-evasion matrix, systems warm-ups, and three blocked systems repetitions. It then collects the processed CSVs and evaluates every validation gate.

If execution is interrupted, rerun the same command with the same `DFL_SESSION_ID`. Completed units are verified and skipped; incomplete attempts remain immutable and are not entered in the completed-run ledger.

## Outputs

Each run receives a unique directory under `results/raw/<run-id>/`. The session ledger and reports are written under `results/orchestration/<session-id>/`. Final CSVs are written under `results/processed/`.

The most important completion checks are:

```bash
SESSION=<completed-session-id>

awk 'NF{n++} END{print n+0}' \
  "results/orchestration/$SESSION/completed.tsv"

cat "$(ls -1t results/orchestration/$SESSION/completion_*.md | head -n 1)"

.venv/bin/python -m json.tool \
  "$(ls -1t results/orchestration/$SESSION/gates_*.json | head -n 1)"
```

A complete full campaign must report 90 completed units and `"passed": true`. No manuscript table should be generated from a campaign whose relevant gate failed.

## Measurement and interpretation boundaries

- Communication reports application payload, serialized protocol, sent, and received bytes separately.
- Memory is measured in fresh processes using OS-level peak RSS plus a sampled trace.
- Timing separates owner active CPU time, owner protocol-online time, and end-to-end round time.
- Energy is omitted unless a direct reproducible power meter is configured.
- Three-seed confidence intervals are descriptive and low-powered. Technical repetitions are not treated as independent learning seeds.
- Study I does not cover executor-aggregator collusion, secret-key compromise, malicious aggregators, or malicious cryptographic implementations.
- Absence of the opened-residual channel in I-ER is not a proof of complete privacy.
- Acceptance of a fresh valid-key work-evasion message is not an HMAC failure; it demonstrates the distinction between authentication and prescribed-work assurance.
