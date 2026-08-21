# Preregistered study protocol

Status: **lean final design frozen after disclosed pre-final-run hardware and
run-budget amendments; all earlier A/E-colocated and 20-round campaigns are
retained as pilot evidence and are not final five-role results**.

## Disclosed aggregator amendment

After the completed A/E-colocated pilot and before any physically separated
final run, the unavailable i7/16 GiB laptop was replaced as A by a physically
separate Dell OptiPlex 7010 desktop with an Intel Core i5-3470, approximately
8 GiB RAM, and Ubuntu 24.04.3. Before the final campaign, the run budget was
also reduced for operational feasibility to three matched learning seeds,
three measured fresh-process systems repetitions, and ten global rounds. The
first three entries of the original seed list and first three blocks of the
original randomized systems schedule are used, so no outcome-based seed or
configuration selection occurs. Dataset records, configurations, attacks,
controls, learning rates, correctness tolerances, and measurement boundaries
remain unchanged. The final manuscript reports this amendment and treats its
uncertainty descriptively. Pilot and final observations are never pooled.

## Experimental unit and stopping rules

The learning/privacy experimental unit is a learning seed. Seeds 101, 211, and 307 are run once per matched configuration. No technical attack restart is treated as an independent learning observation. The systems experimental unit is a fresh process repetition. After two unreported warm-ups per configuration, three measured repetitions use ML seed 101. No run is dropped for an outcome. A run may be invalidated only by a prespecified gate failure (hardware/network state, corrupted trace, incorrect HE arithmetic, thermal throttling, or incomplete messages), with the failure retained and a replacement assigned a new run ID.

No final-run hyperparameter, record count, split, linkage condition, probe family, correctness tolerance, or primary outcome is changed after this amendment is frozen. An unsupported hypothesis is reported as unsupported.

## Physical roles

Exactly five processes on five physical roles are used: O_1 Raspberry Pi 4
(only changing placement), O_2 Raspberry Pi 4 (always ordinary local
training), O_3 Raspberry Pi 5 (always ordinary local training), E i9/32 GiB
workstation, and the amended A Dell OptiPlex 7010/i5-3470/approximately 8 GiB
desktop. A coordinates/aggregates and is Study-I's trusted decryption
authority. E is honest-but-curious for privacy. E-A collusion is outside
scope.

`manifests/collect.py` records hostname, board/revision, RAM, CPU, governor/frequency, OS/kernel, storage, temperature, throttle bits, free memory, runtime, Git commit, dependencies, config/dataset hashes, seed, timestamps, network state, and command. Study I is pinned to Go 1.26.5 exactly; the build rejects other versions and disables automatic toolchain substitution. Go nodes additionally retain `go version`, `go env`, `uname -a`, `lscpu`, and `free -b`. A node-role mismatch fails the gate.

## Dataset and partitions

Only `subject101.dat`...`subject109.dat` under PAMAP2 `Protocol/` are accepted. Activity 0 and optional files are excluded. The 12 retained activities are `{1,2,3,4,5,6,7,12,13,16,17,24}`.

Within each subject and each contiguous activity interval, raw rows are divided chronologically using ratios learning 0.50, utility_test 0.20, attacker_auxiliary 0.15, attacker_target 0.15. A 2.56 s (256-row at 100 Hz) guard is removed on both sides of every boundary. Missing sensor/heart-rate values are linearly interpolated **within one subject/activity/partition only**; windows with remaining missing required values are rejected. Thus interpolation cannot cross an incompatible boundary.

Windows have 256 rows and stride 128. Purity must be at least 0.90. Every record stores subject, owner, activity, contiguous-interval ID, partition, raw row indices, raw timestamps, record ID, and feature schema hash. Identifiers and timestamps are manifest metadata only, never model features.

Feasibility counts are computed before cryptography/training. Study I targets 10 learning and 5 utility windows per owner/activity; Study II targets 24 and 8. For each study/partition the selected count is reduced uniformly to the largest count supported by every required owner/activity cell. Selection is without replacement using seed 101 and record IDs are frozen. Auxiliary/target probe windows are likewise frozen from their disjoint partitions. Manifest and record-file hashes become the dataset-manifest hash.

## Study I

The model is `y_hat=w^T x+b`, MSE convention `0.5*mean((y_hat-y)^2)`, hence gradients are `X^T e/N` and `mean(e)`. Ten synchronous rounds use sample-weighted full-batch gradients and global learning rate 0.0006653586255336017, deterministically set to the inverse largest eigenvalue of the frozen pooled-learning Hessian before final runs. O_2/O_3 are local in every condition.

Configurations: I-L local plaintext; I-P plaintext full delegation; I-OR encrypted X/y with per-sample residual opened only by A and returned to E; I-ER encrypted X/y with residual retained encrypted and only the final local gradient opened by A. In delegated conditions O_1 performs authorization, encryption, network/state/logging/result receipt only. O_1 never predicts, forms residuals/gradients, or updates learning state. The operation trace is checked before Type-I labeling.

A creates CKKS secret/public/relinearization/rotation material. O_1 gets the public encryption material; E gets public/evaluation material; only A gets the secret key. `study1_ckks_parameters.json` records the candidate/frozen parameter status, version/commit, explicit moduli bit sizes, scale, depth, packing, rotations, ciphertext count, serialization, and security estimate. Hidden parameter selection is prohibited.

Correctness tolerance, frozen before final runs: each HE round must have max absolute gradient error <=1e-4, relative L2 gradient error <=1e-4, aggregated parameter deviation <=2e-3, training-loss difference <=1e-4, and final RMSE difference <=1e-3. Any exceedance invalidates the HE result.

The I-OR reconstruction uses only E's genuine view. For R in {1,5,10}, each linked record yields rows `[w_t^T,-1]` and right-hand side `e_t-b_t` over unknown `[x^T,y]^T`. A direct one-sided Jacobi SVD implemented in Go reports rank (tolerance `max(shape)*s_max*eps`), all singular values, condition number, solver, ridge alpha 0, convergence, and equation count before outcomes. Because at most ten equations constrain thirteen unknowns, the final design is underdetermined with nullity at least three. Reported reconstructions are minimum-norm estimates; unique reconstruction is not claimed.

Linkage conditions are correct stable; independent shuffle each round; 25/50/75% correct; deliberately permuted assignment. Within a seed they reuse models/data. Outcomes are heart-rate MAE/RMSE/R2; standardized feature RMSE/relative-L2/cosine; and auxiliary-only L2 multinomial activity probe balanced accuracy/macro-F1 against 1/12 and shuffled labels. The feasibility stage froze only one O_1 auxiliary record per activity, which makes an auxiliary-only stratified train/validation split undefined. Study I did not prescribe regularization tuning; before any reconstruction or activity-probe outcome was produced, C=1.0 was therefore frozen for every seed. This amendment and the small auxiliary support are reported as limitations. I-ER adds a trace assertion excluding plaintext residuals and a prespecified metadata-only probe; this establishes only absence of the tested channel.

## Study II

The exact MLP is 36-128-64-32-12 with ReLU hidden activations. SGD lr 0.02, batch 32, one local epoch, and ten full-participation sample-weighted FedAvg rounds are used. II-L is local; II-S is conventional split (labels visible at E); II-U-E has owner 36-128, E 128-64-32, owner 32-12; II-U-D has owner 36-128-64, E 64-32, owner 32-12; II-U-H is II-U-E plus contextual HMAC.

After O_1 local training, E returns its delegated segment. O_1 reconstructs the complete logical-client state and sends its owner-attributed contribution to A. The returned segment is owner-boundary communication. A never completes missing O_1 learning.

For honest-but-curious privacy, E records z, cut gradient g, (z,g), and linked views at rounds 1,5,10. Separate probes predict activity and closed-set O_1 subject identity. Primary probe is L2 multinomial logistic regression; C in {0.01,0.1,1,10} is chosen only on auxiliary validation. Outcomes: balanced accuracy, macro-F1, confusion/support, majority/prior and shuffled controls. The prespecified cross-round effect is linked rounds 1/5/10 minus the same view at round 10, paired within seed with a paired 95% bootstrap CI.

II-U-H uses a random 256-bit owner-E pair key. Canonical JSON (UTF-8, sorted keys, compact separators, integer fields) includes version, IDs, round/batch/cut, model-state SHA-256, sample count, sequence, base64 nonce, and payload SHA-256. HMAC-SHA-256 covers the envelope bytes. O_1 keeps highest accepted sequence and seen nonces scoped to task. Positive controls mutate payload/tag/task/round/batch/model hash, replay exactly, and use stale sequence.

An external attacker lacks the key. The authorized work-evasive E has it and returns fresh valid envelopes for prespecified deviations: deterministic first 25/50/75% of the minibatch with omitted rows returned as zeros; prior-batch result reused and shape-checked in a fresh envelope (the first shape class is honestly primed and excluded from saved-compute percentages); all cut-gradient values negated; and a seed-derived nonidentity permutation of sample/result association. Report HMAC acceptance, compute time/saving, L2/cosine from honest, global loss, test balanced accuracy/macro-F1, and convergence. Acceptance with the valid key is not an HMAC failure.

## Measurement boundaries and analysis

Owner active time is process CPU time consumed during the owner's round,
including owner computation and required serialization but excluding blocked
network time. Owner protocol-online time is wall time from local round receipt
through accepted owner contribution including required network/serialization.
End-to-end round time is A's dispatch timestamp to its acceptance of all three
contributions. Cold start is recorded independently as module/main entry to the
role's protocol-ready point (owner state loaded or server listening); it is not
equated with Study-I encryption/delegation setup. Setup/encryption, cold start,
per-round, executor, and aggregator costs are separate.

Every message log records sender/receiver/task/client/round/type, payload and
complete serialized byte counts, timestamp, SHA-256, connection direction, and
actual socket bytes. Persistent state is measured from dedicated state
directories. Peak RSS is the maximum of the OS process maximum and the sampled
RSS trace; both raw sources are retained. Start and end Pi throttle states are
validated. Negotiated link information and ten-packet RTT samples are retained,
with median and p95 regenerated from the individual observations. Every
configuration runs in a new process. Energy appears only if a direct configured
meter returns reproducible samples.

Learning outcomes report all three raw seed values, mean, sample SD, and a t-based 95% CI. Matched effects use within-seed differences and a 50,000-draw paired bootstrap CI, interpreted as low-powered descriptive uncertainty with only three pairs. Systems report all three raw repetitions, median, and range; IQR and tail percentiles are not interpreted from n=3. The retained schedule consists of the first three complete blocks generated before measurement from seed 20240517 and stored in `configs/systems_schedule.csv`.
