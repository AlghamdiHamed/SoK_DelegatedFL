# Feasibility budget grid

One Pi 4 target owner; seed 101; idle-relative cgroup limits; ten original rounds.
Predictions are a structural live-tensor model, not guarantees; temporary copies/allocator/cache effects can raise or lower measured charged usage.
Transfer times are ideal serialization estimates, not measured 1/10 Mbit/s experiments. No energy was measured.

| Method | Width | Extra MiB | Total cap MiB | Predicted fit | Measured outcome | Peak RSS MiB | Charged peak MiB |
|---|---:|---:|---:|---|---|---:|---:|
| II-L | 64 | 16 | 202.17 | False | infeasible_oom | NA | 202.18 |
| II-L | 64 | 64 | 250.17 | False | infeasible_oom | NA | 250.27 |
| II-L | 64 | 256 | 442.17 | False | infeasible_oom | NA | 442.17 |
| II-U-E | 64 | 16 | 202.17 | False | infeasible_oom | NA | 202.21 |
| II-U-E | 64 | 64 | 250.17 | False | infeasible_oom | NA | 250.18 |
| II-U-E | 64 | 256 | 442.17 | False | infeasible_oom | NA | 442.17 |
| II-U-D | 64 | 16 | 202.17 | False | infeasible_oom | NA | 202.28 |
| II-U-D | 64 | 64 | 250.17 | False | infeasible_oom | NA | 250.19 |
| II-U-D | 64 | 256 | 442.17 | False | infeasible_oom | NA | 442.17 |
| II-L | 32 | 16 | 202.17 | False | infeasible_oom | NA | 202.17 |
| II-L | 32 | 64 | 250.17 | False | infeasible_oom | NA | 250.17 |
| II-L | 32 | 256 | 442.17 | True | infeasible_oom | NA | 442.17 |
| II-U-E | 32 | 16 | 202.17 | False | infeasible_oom | NA | 202.17 |
| II-U-E | 32 | 64 | 250.17 | False | infeasible_oom | NA | 250.17 |
| II-U-E | 32 | 256 | 442.17 | True | infeasible_oom | NA | 442.17 |
| II-U-D | 32 | 16 | 202.17 | False | infeasible_oom | NA | 202.17 |
| II-U-D | 32 | 64 | 250.17 | False | infeasible_oom | NA | 250.17 |
| II-U-D | 32 | 256 | 442.17 | True | infeasible_oom | NA | 442.17 |
| II-L | 16 | 16 | 202.17 | False | infeasible_oom | NA | 202.17 |
| II-L | 16 | 64 | 250.17 | True | infeasible_oom | NA | 250.17 |
| II-L | 16 | 256 | 442.17 | True | feasible | 390.80 | 309.25 |
| II-U-E | 16 | 16 | 202.17 | False | infeasible_oom | NA | 202.17 |
| II-U-E | 16 | 64 | 250.17 | True | infeasible_oom | NA | 250.17 |
| II-U-E | 16 | 256 | 442.17 | True | feasible | 389.20 | 399.39 |
| II-U-D | 16 | 16 | 202.17 | False | infeasible_oom | NA | 202.17 |
| II-U-D | 16 | 64 | 250.17 | True | infeasible_oom | NA | 250.17 |
| II-U-D | 16 | 256 | 442.17 | True | feasible | 392.11 | 319.32 |
| II-L | 1 | 16 | 202.17 | True | feasible | 281.17 | 187.22 |
| II-L | 1 | 64 | 250.17 | True | feasible | 281.30 | 187.23 |
| II-L | 1 | 256 | 442.17 | True | feasible | 281.29 | 187.31 |
| II-U-E | 1 | 16 | 202.17 | True | feasible | 281.34 | 187.98 |
| II-U-E | 1 | 64 | 250.17 | True | feasible | 281.35 | 188.01 |
| II-U-E | 1 | 256 | 442.17 | True | feasible | 281.15 | 188.10 |
| II-U-D | 1 | 16 | 202.17 | True | feasible | 281.09 | 187.81 |
| II-U-D | 1 | 64 | 250.17 | True | feasible | 281.28 | 187.65 |
| II-U-D | 1 | 256 | 442.17 | True | feasible | 281.41 | 187.67 |
| I-L | 1 | 16 | 18.38 | not modelled | feasible | 5.88 | 3.08 |
| I-L | 1 | 64 | 66.38 | not modelled | feasible | 5.78 | 3.08 |
| I-L | 1 | 256 | 258.38 | not modelled | feasible | 5.66 | 3.08 |
| I-ER | 1 | 16 | 18.38 | not modelled | infeasible_oom | NA | 18.38 |
| I-ER | 1 | 64 | 66.38 | not modelled | infeasible_oom | NA | 66.38 |
| I-ER | 1 | 256 | 258.38 | not modelled | feasible | 85.57 | 86.66 |
