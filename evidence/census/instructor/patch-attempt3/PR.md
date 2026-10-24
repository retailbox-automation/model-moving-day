# Move instructor's LLM calls to NVIDIA Nemotron on Nebius Token Factory

Why: `gpt-4.1-mini` is on the vendor's retirement list; the vendor's recommended replacement is `gpt-6-luna`. This change measures, call site by call site, the smallest Nemotron model whose runs pass this repository's own tests (frozen before the run) at least as often as the rule below requires.

- Repository: `567-labs/instructor` at `6cd771c5254a` (MIT (LICENSE at the pinned commit))
- Call sites reached by the repo's own tests and sensitive to the answer (must-differ control): 3; measured to a decision: 3; moved: 3; moved to Lightning or Nano: 2; lost to infrastructure errors: 0
- Ladder: Nano -> Lightning -> Super -> Ultra, K=5 runs per cell (decision run `20261024T114940Z-f6c56a`); a cell passes when at least K runs are valid and its pass rate is at least the baseline's minus 0.20. Baseline: pass rate assumed 1.0 (strictest), not measured by us. Upstream CI run 39368977288 at the pinned 6cd771c: step 'Run core provider tests (OpenAI)' (tests/llm/test_core_providers -k openai, gpt-4.1-mini, one run) succeeded; the run's overall conclusion is failure from another job (checked with gh run view, 2026-10-24).
- Manifest SHA-256 `35365b0c8c68fe8a06270f9dbf6bd0715023effaaa66df047f7d125f2542e312`, pushed 2026-10-24T11:47:38Z (commit `4f78a82efd14` on `lane-c1`)

## Per call site

| Call site | Tests (flip) | Nano | Lightning | Super | Ultra | Outcome | Fallback | $/1k calls after | $/1k calls before (gpt-6-luna) | $ per passed run after / before | Request changes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `instructor/v2/core/retry.py:224` | 8 | ❌ 2/5 [0.12-0.77] | ✅ 5/5 [0.57-1.00] | - | - | Lightning | Super ✅ measured | 0.046 | 0.052 | 0.00051 / 0.00057 | model |
| `instructor/v2/core/retry.py:523` | 23 | ❌ 0/5 [0.00-0.43] | ❌ 0/5 [0.00-0.43] | ✅ 5/5 [0.57-1.00], 5 infra | - | Super | Ultra ✅ measured | 0.170 | 0.058 | 0.00974 / 0.00333 | model |
| `instructor/v2/validation/llm_validators.py:34` | 2 | ✅ 5/5 [0.57-1.00], 1 infra | - | - | - | Nano | Lightning ✅ measured | 0.101 | 0.199 | 0.00028 / 0.00056 | model |
| `instructor/v2/providers/openai/client.py:23` | - | - | - | - | - | not measured: excluded before the run (Responses API call site: 20 tests exist (tests/llm/test_openai/slow/test_response.py, sync and async) and are excluded because the proxy does not route /v1/responses (decision 2026-10-24: found, not measured)) | - | - | - | - | - |
| `instructor/v2/providers/openai/client.py:29` | - | - | - | - | - | not measured: excluded before the run (Responses API call site: 20 tests exist (tests/llm/test_openai/slow/test_response.py, sync and async) and are excluded because the proxy does not route /v1/responses (decision 2026-10-24: found, not measured)) | - | - | - | - | - |

Cell = passed runs / valid runs, Wilson 95 % interval in brackets; `+fix` marks a re-run after the agent's request fix; `infra` runs were lost to the sandbox, the network or the platform and are not counted.

## Receipts (every cell, including failures, infrastructure losses and fallback cells)

| Call site | Role | Rung | Params | Status | Passed / valid (infra) | $/1k calls | $ per passed run | Calls per test run | Reasoning tokens | Diagnoses | First failure |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `instructor/v2/core/retry.py:224` | ladder | Nano | - | fail | 2/5 (0) | 0.081 | 0.00234 | 1.45 | 0 | - | tests/llm/test_openai/test_patch.py::test_runmodel[gpt-4.1-mini-Mode.TOOLS]: AssertionError: assert 'userextract' == 'jason'
  
  - jason
  + userextract |
| `instructor/v2/core/retry.py:224` | ladder | Lightning | - | pass | 5/5 (0) | 0.046 | 0.00051 | 1.375 | 3893 | - | - |
| `instructor/v2/core/retry.py:224` | fallback | Super | - | pass | 5/5 (5) | 0.157 | 0.00344 | 1.375 | 2144 | - | - |
| `instructor/v2/core/retry.py:523` | ladder | Nano | - | fail | 0/5 (0) | 0.106 | - | 1.139 | 0 | - | tests/llm/test_core_providers/test_basic_extraction.py::test_nested_model_extraction[openai]: AssertionError: assert 'UserWithAddress' == 'John Doe'
  
  - John |
| `instructor/v2/core/retry.py:523` | ladder | Lightning | - | fail | 0/5 (0) | 0.056 | - | 1.261 | 12178 | - | tests/llm/test_core_providers/test_basic_extraction.py::test_nested_model_extraction[openai]: instructor.v2.core.errors.InstructorRetryException: <failed_attemp |
| `instructor/v2/core/retry.py:523` | ladder | Super | - | pass | 5/5 (5) | 0.170 | 0.00974 | 1.243 | 5842 | - | - |
| `instructor/v2/core/retry.py:523` | fallback | Ultra | - | pass | 5/5 (0) | 0.659 | 0.01714 | 1.13 | 7727 | - | - |
| `instructor/v2/validation/llm_validators.py:34` | ladder | Nano | - | pass | 5/5 (1) | 0.101 | 0.00028 | 1.167 | 0 | - | - |
| `instructor/v2/validation/llm_validators.py:34` | fallback | Lightning | - | pass | 5/5 (0) | 0.073 | 0.00015 | 1.0 | 953 | - | - |

## Ladder runs of this repository

| Run | Started (UTC) | Used for this change |
|---|---|---|
| `20261024T114940Z-f6c56a` | 2026-10-24T11:49:40Z | yes |

## Confirm run of the patched app

The helper's endpoint pointed at the recording proxy, which sent each moved call to Token Factory exactly as the app built it; the app's own endpoint pointed at a route that records the call and answers empty, so a call the helper did not move shows up and fails.

| Call site | Result | Status | Models requested | Routing problems |
|---|---|---|---|---|
| `instructor/v2/core/retry.py:224` | infra: 15 runs lost (0/0 valid) | infra | - | no calls recorded for its flip tests |
| `instructor/v2/core/retry.py:523` | infra: 15 runs lost (0/0 valid) | infra | - | no calls recorded for its flip tests |
| `instructor/v2/validation/llm_validators.py:34` | infra: 15 runs lost (0/0 valid) | infra | - | no calls recorded for its flip tests |

## What this change moves

- Moved (0): none
- Wrapped but not moved (3): `instructor/v2/core/retry.py:224` (infra), `instructor/v2/core/retry.py:523` (infra), `instructor/v2/validation/llm_validators.py:34` (infra)

## What is not proven

- Call sites where no Nemotron model passed (the old model stays): none
- Call sites lost to infrastructure errors (no decision): none
- Excluded before the run, with the reason: `instructor/v2/providers/openai/client.py:23` (Responses API call site: 20 tests exist (tests/llm/test_openai/slow/test_response.py, sync and async) and are excluded because the proxy does not route /v1/responses (decision 2026-10-24: found, not measured)); `instructor/v2/providers/openai/client.py:29` (Responses API call site: 20 tests exist (tests/llm/test_openai/slow/test_response.py, sync and async) and are excluded because the proxy does not route /v1/responses (decision 2026-10-24: found, not measured))
- Reached by the tests, but no test fails on an empty answer there (not moved, not measured): `instructor/v2/core/retry.py:483`
- Found in the source but reached by none of the kept tests (not moved, not measured): none
- Baseline cost is priced from token counts measured on Nemotron (prompt tokens and visible output tokens); another tokenizer differs, so treat it as an estimate. Prices: platform.openai.com/docs/pricing.md and /docs/deprecations.md, read 2026-10-20 (docs/kb/05-c1-domain.md).
- K=5 runs per cell is a small sample; the intervals above say how small.
- baseline pass rate 1.0 is assumed (strictest), not measured by us
- K runs of one cell run in one sandbox at the same time, often with temperature 0: they are not independent, so the Wilson interval is optimistic
- covered = the test fails when the proxy answers with an empty message (must-differ control 1); tests that check only types or shapes pass any well-formed answer; a second control with well-formed wrong answers is run only where the manifest lists control-wrong
- streaming tests get server-sent events re-synthesized by the proxy from a non-streaming upstream call
- during the ladder the proxy normalizes requests per rung (logged per call); the patched app is confirmed separately without normalization
- per-call $ is priced on Nemotron's token counts; the baseline uses the same counts (another tokenizer differs)
- test traffic, not production traffic
- runs are set aside as infrastructure whenever the proxy logged 402/404/429/5xx/None at the site or at a site its flip tests reach, whatever their outcome, and re-run at most twice; each cell lists how many set-aside runs had passed and how many had failed (runs_with_retried_infra)
- after a run the hash check catches changed or missing frozen files; new files in test directories are recorded but do not stop the ladder

## How the move works

Each wrapped call site goes through `nemotron_move` (added by this change). It is off unless `NEMOTRON_MOVE=1`; then a moved site sends the Nemotron model chosen above, with the request parameters measured for it, to Token Factory, and only for the call shape the census measured (other calls through the same site keep their model and endpoint). `NEMOTRON_MODEL_<SITE>` overrides one site. The outermost wrapped site decides for the calls below it; a site marked as not moved keeps a moved library funnel from moving its calls. When Token Factory answers that the model is unknown (it does not reroute retired models), the call retries once on the fallback in the table, only if that fallback was measured and passed; otherwise the error is raised.

## Configuration

```
NEMOTRON_MOVE=1
NEMOTRON_MOVE_BASE_URL=https://api.tokenfactory.nebius.com/v1
NEMOTRON_MOVE_API_KEY=<your Token Factory key>
```
Moved calls go to `NEMOTRON_MOVE_BASE_URL` with that key; every other call keeps the app's own endpoint and key. Without both variables nothing is moved and the helper logs one warning. With them, the first moved call also lists the platform's models and warns about any configured model that is no longer listed.

## Reproduce

```
python -m model_moving_day.census ladder instructor
```
K, margin, rungs, flip tests and frozen hashes come from `census/MANIFEST.json`; the ladder refuses to run when the manifest does not match the code, the evidence or the clone.

## Disclosure

Written by the Model Moving Day agent (NVIDIA Nemotron on Nebius Token Factory). No human has reviewed this change yet. The repository's tests, conftest files and pytest configuration (SHA-256 list in the manifest) were hash-checked inside the sandbox before and after every ladder job and the confirm run, and before and after every agent step while the patch was written. This branch is local and has not been proposed upstream.

## Edits

- `instructor/v2/core/retry.py`
- `instructor/v2/core/retry.py`
- `instructor/v2/core/retry.py`
- `instructor/v2/validation/llm_validators.py`
- `instructor/v2/validation/llm_validators.py`
