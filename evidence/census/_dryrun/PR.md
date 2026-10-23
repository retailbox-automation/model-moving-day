# Move _dryrun's LLM calls to NVIDIA Nemotron on Nebius Token Factory

Why: `gpt-4.1-mini` is on the vendor's retirement list; the vendor's recommended replacement is `gpt-6-luna`. This change measures, call site by call site, the smallest Nemotron model that still passes this repository's own tests, frozen before the run.

- Repository: `example/app` at `000000000000` (MIT)
- Call sites reached by the repo's own tests and sensitive to the answer (must-differ control): 3; moved: 2; moved to Lightning or Nano: 2
- Call sites found but not covered by any test that checks the answer: 1
- Ladder: Nano -> Lightning -> Super -> Ultra, K=3 runs per cell; a cell passes when its pass rate is at least the baseline's minus 0.20 (baseline: assumed 1.0: upstream CI runs these tests live)
- Manifest SHA-256 (committed before the run): `dry-run`

## Per call site

| Call site | Tests (flip) | Nano | Lightning | Super | Ultra | Chosen | Fallback | $/1k calls after | $/1k calls before (gpt-6-luna) | Request changes |
|---|---|---|---|---|---|---|---|---|---|---|
| `app/extract.py:40` | 1 | ❌ 0/3 [0.00-0.56] / ✅ 3/3 +reasoning_off [0.44-1.00] | - | - | - | Nano | Lightning | 0.081 | 0.150 | model |
| `app/summary.py:12` | 1 | ❌ 0/3 [0.00-0.56] | ✅ 3/3 [0.44-1.00] | - | - | Lightning | Super | 0.081 | 0.100 | model |
| `app/plan.py:88` | 1 | ❌ 0/3 [0.00-0.56] | ❌ 0/3 [0.00-0.56] | ❌ 0/3 [0.00-0.56] | ❌ 0/3 [0.00-0.56] | **stays on the old model** | - | - | - | model |

Cell = passed runs / valid runs, Wilson 95 % interval in brackets; `+fix` marks a re-run after the agent's request fix.

## Receipts per rung (every cell, including failures)

| Call site | Rung | Params | Passed | $/1k calls | Calls | Reasoning tokens | Diagnoses | First failure |
|---|---|---|---|---|---|---|---|---|
| `app/extract.py:40` | Nano | - | 0/3 | 0.081 | 6 | 600 | {'empty_content_length': 4} | tests/test_extract.py::test_names: AssertionError: scripted |
| `app/extract.py:40` | Nano | {'reasoning': 'off'} | 3/3 | 0.081 | 6 | 0 | - | - |
| `app/summary.py:12` | Nano | - | 0/3 | 0.081 | 6 | 600 | - | tests/test_summary.py::test_len: AssertionError: scripted |
| `app/summary.py:12` | Lightning | - | 3/3 | 0.081 | 6 | 600 | - | - |
| `app/plan.py:88` | Nano | - | 0/3 | 0.081 | 6 | 600 | - | tests/test_plan.py::test_steps: AssertionError: scripted |
| `app/plan.py:88` | Lightning | - | 0/3 | 0.081 | 6 | 600 | - | tests/test_plan.py::test_steps: AssertionError: scripted |
| `app/plan.py:88` | Super | - | 0/3 | 0.360 | 6 | 600 | - | tests/test_plan.py::test_steps: AssertionError: scripted |
| `app/plan.py:88` | Ultra | - | 0/3 | 1.200 | 6 | 600 | - | tests/test_plan.py::test_steps: AssertionError: scripted |

## What is not proven

- Call sites where no Nemotron model passed (the old model stays): `app/plan.py:88`
- Call sites with no test that checks the answer (not moved, not measured): `app/unused.py:7`
- Baseline cost is priced from token counts measured on Nemotron (prompt tokens and visible output tokens); OpenAI's tokenizer differs, so treat it as an estimate. Prices: dry run, illustrative prices.
- K=3 runs per cell is a small sample; the intervals above say how small.

## How the move works

Each moved call site goes through `nemotron_move` (added by this change). It is off unless `NEMOTRON_MOVE=1`; then the site sends the Nemotron model chosen above with the measured request fixes. `NEMOTRON_MODEL_<SITE>` overrides one site. On HTTP 404 (Token Factory does not reroute retired models) the call retries once on the next rung up and logs it; on first use the helper lists Token Factory's /models and warns about configured models that are no longer listed.

## Reproduce

```
python -m model_moving_day.census ladder _dryrun --k 3 --margin 0.2
```

## Disclosure

Written by the Model Moving Day agent (NVIDIA Nemotron on Nebius Token Factory). A human approved the migration plan; tests and fixtures were hash-checked before and after every agent step. This branch is local and has not been proposed upstream.
