> DRY RUN on a scripted fake model: every number below is illustrative, nothing was measured.

# DRY RUN: Move _dryrun's LLM calls to NVIDIA Nemotron on Nebius Token Factory

Why: `gpt-4.1-mini` is on the vendor's retirement list; the vendor's recommended replacement is `gpt-6-luna`. This change measures, call site by call site, the smallest Nemotron model whose runs pass this repository's own tests (frozen before the run) at least as often as the rule below requires.

- Repository: `example/app` at `000000000000` (MIT)
- Call sites reached by the repo's own tests and sensitive to the answer (must-differ control): 4; measured to a decision: 3; moved: 2; moved to Lightning or Nano: 2; lost to infrastructure errors: 1
- Ladder: Nano -> Lightning -> Super -> Ultra, K=3 runs per cell (decision run `20261024T103612Z-1c3f23`); a cell passes when at least K runs are valid and its pass rate is at least the baseline's minus 0.20. Baseline: pass rate assumed 1.0 (strictest), not measured by us. 
- Manifest SHA-256 `dry-run`

## Per call site

| Call site | Tests (flip) | Nano | Lightning | Super | Ultra | Outcome | Fallback | $/1k calls after | $/1k calls before (gpt-6-luna) | $ per passed run after / before | Request changes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `app/extract.py:40` | 1 | ❌ 0/3 [0.00-0.56] / ✅ 3/3 +reasoning_off [0.44-1.00] | - | - | - | Nano (+reasoning_off) | Lightning ✅ measured | 0.081 | 0.150 | 0.00000 / 0.00030 | model |
| `app/summary.py:12` | 1 | ❌ 0/3 [0.00-0.56] | ✅ 3/3 [0.44-1.00] | - | - | Lightning | Super ✅ measured | 0.081 | 0.100 | 0.00000 / 0.00020 | model |
| `app/plan.py:88` | 1 | ❌ 0/3 [0.00-0.56] | ❌ 0/3 [0.00-0.56] | ❌ 0/3 [0.00-0.56] | ❌ 0/3 [0.00-0.56] | **stays on the old model** | - | - | - | - | model |
| `app/flaky.py:7` | 1 | infra: 9 runs lost (0/0 valid) | - | - | - | infra: not measured (infrastructure errors persisted at Nano after 2 re-runs; not climbed) | - | - | - | - | model |
| `app/responses.py:3` | - | - | - | - | - | not measured: excluded before the run (example exclusion) | - | - | - | - | - |

Cell = passed runs / valid runs, Wilson 95 % interval in brackets; `+fix` marks a re-run after the agent's request fix; `infra` runs were lost to the sandbox, the network or the platform and are not counted.

## Receipts (every cell, including failures, infrastructure losses and fallback cells)

| Call site | Role | Rung | Params | Status | Passed / valid (infra) | $/1k calls | $ per passed run | Calls per test run | Reasoning tokens | Diagnoses | First failure |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `app/extract.py:40` | ladder | Nano | - | fail | 0/3 (0) | 0.081 | - | 2.0 | 600 | {'empty_content_length': 4} | tests/test_extract.py::test_names: AssertionError: scripted |
| `app/extract.py:40` | ladder | Nano | {'reasoning': 'off'} | pass | 3/3 (0) | 0.081 | 0.00000 | 2.0 | 0 | - | - |
| `app/extract.py:40` | fallback | Lightning | - | pass | 3/3 (0) | 0.081 | 0.00000 | 2.0 | 600 | - | - |
| `app/summary.py:12` | ladder | Nano | - | fail | 0/3 (0) | 0.081 | - | 2.0 | 600 | - | tests/test_summary.py::test_len: AssertionError: scripted |
| `app/summary.py:12` | ladder | Lightning | - | pass | 3/3 (0) | 0.081 | 0.00000 | 2.0 | 600 | - | - |
| `app/summary.py:12` | fallback | Super | - | pass | 3/3 (0) | 0.360 | 0.00000 | 2.0 | 600 | - | - |
| `app/plan.py:88` | ladder | Nano | - | fail | 0/3 (0) | 0.081 | - | 2.0 | 600 | - | tests/test_plan.py::test_steps: AssertionError: scripted |
| `app/plan.py:88` | ladder | Lightning | - | fail | 0/3 (0) | 0.081 | - | 2.0 | 600 | - | tests/test_plan.py::test_steps: AssertionError: scripted |
| `app/plan.py:88` | ladder | Super | - | fail | 0/3 (0) | 0.360 | - | 2.0 | 600 | - | tests/test_plan.py::test_steps: AssertionError: scripted |
| `app/plan.py:88` | ladder | Ultra | - | fail | 0/3 (0) | 1.200 | - | 2.0 | 600 | - | tests/test_plan.py::test_steps: AssertionError: scripted |
| `app/flaky.py:7` | ladder | Nano | - | infra | 0/0 (9) | 0.081 | - | 2.0 | 1800 | - | - |

## What is not proven

- Call sites where no Nemotron model passed (the old model stays): `app/plan.py:88`
- Call sites lost to infrastructure errors (no decision): `app/flaky.py:7`
- Excluded before the run, with the reason: `app/responses.py:3` (example exclusion)
- Reached by the tests, but no test fails on an empty answer there (not moved, not measured): none
- Found in the source but reached by none of the kept tests (not moved, not measured): `app/unused.py:7`
- Baseline cost is priced from token counts measured on Nemotron (prompt tokens and visible output tokens); another tokenizer differs, so treat it as an estimate. Prices: dry run, illustrative prices.
- K=3 runs per cell is a small sample; the intervals above say how small.

## How the move works

Each moved call site goes through `nemotron_move` (added by this change). It is off unless `NEMOTRON_MOVE=1`; then the site sends the Nemotron model chosen above with the request parameters measured for it. `NEMOTRON_MODEL_<SITE>` overrides one site. A moved call made inside another moved call keeps the outer site's choice. When Token Factory answers that the model is unknown (it does not reroute retired models), the call retries once on the fallback in the table, only if that fallback was measured and passed; otherwise the error is raised. With `NEMOTRON_MOVE_BASE_URL` and `NEMOTRON_MOVE_API_KEY` set, the first moved call lists the platform's models and warns about any configured model that is no longer listed.

## Reproduce

```
python -m model_moving_day.census ladder _dryrun
```
K, margin, rungs, flip tests and frozen hashes come from `census/MANIFEST.json`; the ladder refuses to run when the manifest does not match the code, the evidence or the clone.

## Disclosure

Written by the Model Moving Day agent (NVIDIA Nemotron on Nebius Token Factory). No human has reviewed this change yet. The repository's tests, conftest files and pytest configuration (SHA-256 list in the manifest) were hash-checked inside the sandbox before and after every ladder job and the confirm run, and before and after every agent step while the patch was written. This branch is local and has not been proposed upstream.
