# Results directories

`raw/` receives immutable per-run artifacts. `orchestration/` receives session ledgers, logs, completion reports, and gate reports. `processed/` receives automatically collected CSVs and manuscript tables.

The source repository includes only the frozen Study-I CKKS parameter file. Measured outputs are intentionally excluded because they are campaign artifacts rather than source code. A full run populates the required CSVs from its completed-run ledger and refuses publication output when a relevant gate fails.

Never overwrite or edit a raw run directory. Invalid or interrupted attempts remain available for audit but do not enter the completed-run ledger.
