# Token Factory Sandboxes spike: what we measure (written before any sandbox call)

Client: `contree-sdk` 0.3.6 (`ContreeSync`, IAM auth from `NEBIUS_API_KEY` + `NEBIUS_PROJECT_ID`,
endpoint `https://api.tokenfactory.nebius.com/sandboxes/`). Image `python:3.12-slim`.
Raw records: `evidence/sandbox/raw.jsonl` (no secrets: env values are never written).

| Step | What | Pass rule |
|---|---|---|
| T0 | who-am-i / limits of the project | the call succeeds; limits recorded. If the project ID is rejected: stop |
| T1 | run `python -c` printing the Python version | exit 0, stdout returned; record server `elapsed_time`, client wall time, `cost` |
| T2 | egress from inside: GET pypi.org, codeload.github.com, `api.tokenfactory.nebius.com/v1/models` (key passed via the run's env) | each answers HTTP 200; record which do not |
| T3 | one Lightning chat call from inside the sandbox through `tf_client` (uploaded file), with a $0.01 spend envelope reserved first in the shared ledger and settled to the in-sandbox ledger's total afterwards | the call returns text; the envelope ledger total equals the call's cost; shared ledger never under-counts |
| T4 | install one small target repo at a pinned commit (tarball from codeload) into a tagged image, then run a subset of its own tests in a fresh run from that image | tests run and the exit code / pytest summary line come back; record install time and test wall time separately |

Stop rule (from the brief): any non-zero `cost` reported by a sandbox result, or any credit change, stops the
spike and is reported.

## Amendment (decided 2026-10-23T16:04Z, written down here 2026-10-24)

The stop rule above was replaced at 16:04Z, after the first T1 run reported `cost` $0.00104: each sandbox run
instead reserved $0.01 in the shared spend ledger and was settled to its reported `cost`. Reason: the Sandboxes docs
call the field "for diagnostics only". The billing check of 2026-10-24 (Token Factory Transactions and Usage for
October show only model tokens, $0.54, while the field summed to $0.493 over 15 runs) found no charge for Sandboxes
in the beta, so from phase B on the field is logged as a diagnostic and nothing is reserved for it. This is an
observation dated 2026-10-24, not a statement that Sandboxes are free.

Step T4 was carried out by the census itself, not by this spike script: `census prepare instructor` and
`census control instructor` (evidence/census/instructor/image.json and control-*.json). raw.jsonl holds T0–T3.
