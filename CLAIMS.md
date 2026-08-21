# Claim ledger

Status is `untested` until raw data pass all gates. Code completion or a control passing is not empirical support.

| ID | Prespecified proposition | Required evidence | Status | Allowed wording if supported |
|---|---|---|---|---|
| C1 | I-OR's opened, linked residuals expose information beyond encrypted inputs | HE correctness; executor-view trace; rank/conditioning; underdetermined minimum-norm estimates and controls across 3 seeds | untested | Opening a correlated residual to the tested encrypted-input full-delegation executor exposed information not protected by encryption of the underlying records. |
| C2 | I-ER removes the demonstrated residual channel | No-residual trace gate; inability to instantiate residual equations; metadata control | untested | Keeping the tested residual encrypted removed the specific residual-based channel demonstrated in I-OR; this does not establish privacy of all executor observations. |
| C3 | Owner-local labels can remain predictable from executor intermediates | Disjoint auxiliary/target probe, negative controls, seed-level II-U-E/II-U-D outcomes | untested | In the tested split configurations, label locality alone did not establish label privacy because executor-visible representations remained predictive. |
| C4 | Contextual HMAC does not prove prescribed computation by a key-holding executor | All positive controls pass; fresh valid-key evasion envelopes accepted; deviation/work outcomes | untested | Contextual HMAC authenticated the returned message and context, but did not establish that the prescribed computation generated it. |
| C5 | Protection/cut placement changes the composition of O_1 burden | Three fresh-process repetitions; paired boundary definitions; multidimensional I-L/I-P/I-OR/I-ER and II-U-E/II-U-D tables | untested | Closing tested privacy or attribution gaps changed the composition of the constrained owner's residual workload. |

Unsupported propositions remain in this table with observed effects and uncertainty. The analysis must not claim universal DFL privacy failure, complete I-ER privacy, unique reconstruction without rank/conditioning, universal cross-round leakage, HMAC insecurity, complete work assurance, E-A collusion resistance, universal IoT feasibility, overall superiority from one resource dimension, significance from technical restarts, or corpus-wide prevalence.
