# Model Moving Day: census of three open-source LLM apps

Every number below is recomputed by `census/report.py` from the evidence in this repository (`evidence/census/<repo>/ladder-*.json`, `coverage.json`, `census/MANIFEST.json`). Test traffic, not production traffic.

## Headline

- Call sites in the ladder: 8 in 3 repositories; decided: 4 (in 2 repositories); moved to Lightning or Nano: 3 of 4 decided.
- Independent decisions (one funnel's sync and async faces, or sites reached by the same tests, count once; a group counts at its largest chosen model): 3; on Lightning or Nano: 2 (Wilson 95 % [0.21-0.94]).
- Runs set aside as infrastructure (rate limits, 503, lost runs) and re-run: 55. Sites with no decision because of infrastructure: 4.
- Coverage gate (at least 3 repositories and 5 independent sites, decided): NOT met (2 repositories with a decision, 3 independent decisions).
- Model spend for all decision ladder runs: $0.4668 in tokens. The Sandboxes `cost` field summed to $4.84; it is diagnostic (the 2026-10-24 billing check found no Sandboxes charge).

## Which model does what

| Repository | Call site | What the call does | Model after the census | $/1k calls after | $/1k calls before (gpt-6-luna) | $ per passed test run after / before | Calls per test run |
|---|---|---|---|---|---|---|---|
| instructor | `instructor/v2/core/retry.py:224` | instructor's sync retry funnel: every structured-output call (tool calls, JSON) of a sync client | **Lightning** | $0.046 | $0.052 | $0.00051 / $0.00057 | 1.375 |
| instructor | `instructor/v2/core/retry.py:523` | the async twin of the same funnel | **Super** | $0.170 | $0.058 | $0.00974 / $0.00333 | 1.243 |
| instructor | `instructor/v2/validation/llm_validators.py:34` | llm_validator: an LLM judges a field value (accept or reject with a reason) | **Nano** | $0.101 | $0.199 | $0.00028 / $0.00056 | 1.167 |
| llm | `llm/default_plugins/openai_models.py:1494` | llm's non-streaming chat call (tools, chains of tool calls) | **Nano** + reasoning_off | $0.030 | $0.052 | $0.00009 / $0.00015 | 3.0 |
| paper-qa | `src/paperqa/core.py:254` | paper-qa: summarize one evidence chunk for the question (summary_llm, the high-volume step) | infra | - | - | - / - | - |
| paper-qa | `src/paperqa/docs.py:234` | paper-qa: parse a citation into structured fields | infra | - | - | - / - | - |
| paper-qa | `src/paperqa/docs.py:675` | paper-qa: write the answer from the evidence | infra | - | - | - / - | - |
| paper-qa | `src/paperqa/docs.py:703` | paper-qa: post-prompt after the answer | infra | - | - | - / - | - |

"Before" prices the same measured calls (prompt tokens and visible output tokens) at gpt-6-luna ($0.1/$0.5 per 1M tokens), the replacement the vendor recommends for the models these tests name (platform.openai.com/docs/pricing.md and /docs/deprecations.md, read 2026-10-20 (docs/kb/05-c1-domain.md)). Diagnosis and fixes: the brain (Super or Ultra by repo size) is asked only for a red cell whose call log shows a diagnosis; the embedding calls of paper-qa go to Qwen/Qwen3-Embedding-8B on every rung.

## Every cell

### instructor

- Decision run `20261024T114940Z-f6c56a`: complete, 3 of 3 sites decided, 9 sandbox jobs, $0.1829 in tokens (the headline uses this run)

#### Run `20261024T114940Z-f6c56a`

Decision run `20261024T114940Z-f6c56a` (2026-10-24T11:49:40Z to 2026-10-24T11:58:38Z), manifest `35365b0c8c68` pushed 2026-10-24T11:47:38Z, K=5, pass = at least 4 of 5 valid runs.

| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |
|---|---|---|---|---|---|---|---|---|
| `instructor/v2/core/retry.py:224` | ladder | Nano | - | fail | 2/5 | 0 (0, 0) | $0.081 | tests/llm/test_openai/test_patch.py::test_runmodel[gpt-4.1-mini-Mode.TOOLS]: AssertionError: assert 'userextract' == 'ja |
| `instructor/v2/core/retry.py:224` | ladder | Lightning | - | pass | 5/5 | 0 (0, 0) | $0.046 | - |
| `instructor/v2/core/retry.py:224` | fallback | Super | - | pass | 5/5 | 5 (0, 5) | $0.157 | - |
| `instructor/v2/core/retry.py:523` | ladder | Nano | - | fail | 0/5 | 0 (0, 0) | $0.106 | tests/llm/test_core_providers/test_basic_extraction.py::test_nested_model_extraction[openai]: AssertionError: assert 'Us |
| `instructor/v2/core/retry.py:523` | ladder | Lightning | - | fail | 0/5 | 0 (0, 0) | $0.056 | tests/llm/test_core_providers/test_basic_extraction.py::test_nested_model_extraction[openai]: instructor.v2.core.errors. |
| `instructor/v2/core/retry.py:523` | ladder | Super | - | pass | 5/5 | 5 (1, 4) | $0.170 | - |
| `instructor/v2/core/retry.py:523` | fallback | Ultra | - | pass | 5/5 | 0 (0, 0) | $0.659 | - |
| `instructor/v2/validation/llm_validators.py:34` | ladder | Nano | - | pass | 5/5 | 1 (0, 1) | $0.101 | - |
| `instructor/v2/validation/llm_validators.py:34` | fallback | Lightning | - | pass | 5/5 | 0 (0, 0) | $0.073 | - |

### llm

- Decision run `20261024T115937Z-6e8505`: complete, 1 of 1 sites decided, 3 sandbox jobs, $0.0030 in tokens (the headline uses this run)

#### Run `20261024T115937Z-6e8505`

Decision run `20261024T115937Z-6e8505` (2026-10-24T11:59:37Z to 2026-10-24T12:00:26Z), manifest `35365b0c8c68` pushed 2026-10-24T11:47:38Z, K=5, pass = at least 4 of 5 valid runs.

| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |
|---|---|---|---|---|---|---|---|---|
| `llm/default_plugins/openai_models.py:1494` | ladder | Nano | - | fail | 0/5 | 0 (0, 0) | $0.136 | tests/test_tools.py::test_tool_use_chain_of_two_calls: AssertionError: assert '\nYES' == 'YES'
  
  + 
    YES |
| `llm/default_plugins/openai_models.py:1494` | ladder | Nano | {'reasoning': 'off'} | pass | 5/5 | 0 (0, 0) | $0.030 | - |
| `llm/default_plugins/openai_models.py:1494` | fallback | Lightning | - | pass | 5/5 | 0 (0, 0) | $0.038 | - |

Fix at `llm/default_plugins/openai_models.py:1494`: **reasoning_off**, proposed by `nvidia/nemotron-3-super-120b-a12b` from the call log ("The leading newline appears because reasoning is on by default and can cause content to start with a newline, as seen in the assertion failure where the output began with '\nYES'."); the request change showed on 15 calls.

### paper-qa

- Decision run `20261024T122751Z-764b19`: complete, 0 of 4 sites decided, 3 sandbox jobs, $0.1612 in tokens, proxy error statuses logged {'500': 3, '503': 86}
- Decision run `20261024T124923Z-ef27e9`: complete, 0 of 4 sites decided, 3 sandbox jobs, $0.1195 in tokens, proxy error statuses logged {'500': 3, '503': 133} (the headline uses this run)

#### Run `20261024T122751Z-764b19`

Decision run `20261024T122751Z-764b19` (2026-10-24T12:27:51Z to 2026-10-24T12:42:49Z), manifest `fee85e44a3c3` pushed 2026-10-24T12:27:11Z, K=5, pass = at least 4 of 5 valid runs.

| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |
|---|---|---|---|---|---|---|---|---|
| `src/paperqa/core.py:254` | ladder | Nano | - | infra | 0/0 | 15 (8, 7) | $0.219 | - |
| `src/paperqa/docs.py:234` | ladder | Nano | - | infra | 0/0 | 15 (8, 7) | $0.082 | - |
| `src/paperqa/docs.py:675` | ladder | Nano | - | infra | 0/0 | 15 (8, 7) | $0.158 | - |
| `src/paperqa/docs.py:703` | ladder | Nano | - | infra | 0/0 | 15 (13, 2) | $0.011 | - |

#### Run `20261024T124923Z-ef27e9`

Decision run `20261024T124923Z-ef27e9` (2026-10-24T12:49:23Z to 2026-10-24T13:02:18Z), manifest `41981436bbd6` pushed 2026-10-24T12:45:59Z, K=5, pass = at least 4 of 5 valid runs.

| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |
|---|---|---|---|---|---|---|---|---|
| `src/paperqa/core.py:254` | ladder | Nano | - | infra | 1/2 | 11 (3, 8) | $0.224 | tests/test_paperqa.py::test_unrelated_context[default-unsure]: httpx.HTTPStatusError: Client error '429 Too Many Request |
| `src/paperqa/docs.py:234` | ladder | Nano | - | infra | 1/2 | 11 (3, 8) | $0.054 | tests/test_paperqa.py::test_unrelated_context[default-unsure]: httpx.HTTPStatusError: Client error '429 Too Many Request |
| `src/paperqa/docs.py:675` | ladder | Nano | - | infra | 1/2 | 11 (3, 8) | $0.108 | tests/test_paperqa.py::test_unrelated_context[default-unsure]: httpx.HTTPStatusError: Client error '429 Too Many Request |
| `src/paperqa/docs.py:703` | ladder | Nano | - | infra | 2/2 | 11 (7, 4) | $0.016 | - |

## Found, not measured

- instructor `instructor/v2/providers/openai/client.py:23`: excluded before its ladder: Responses API call site: 20 tests exist (tests/llm/test_openai/slow/test_response.py, sync and async) and are excluded because the proxy does not route /v1/responses (decision 2026-10-24: found, not measured)
- instructor `instructor/v2/providers/openai/client.py:29`: excluded before its ladder: Responses API call site: 20 tests exist (tests/llm/test_openai/slow/test_response.py, sync and async) and are excluded because the proxy does not route /v1/responses (decision 2026-10-24: found, not measured)
- instructor `instructor/v2/core/retry.py:483`: reached by the tests, but no test fails on an empty or wrong answer there
- llm `llm/default_plugins/openai_models.py:1441`: excluded before its ladder: not testable live: its 5 flip tests assert wording recorded from the old model (tests/test_tools.py:226, gpt-4o-mini's exact sentence) or the recorded tool output '0.fixed-version' (tests/test_tools_streaming.py :18, :28, :40, :53; a live run returns the installed llm version), and the variants expect different sentences, so no live model, the old one included, can pass them
- llm `llm/default_plugins/openai_models.py:2552`: excluded before its ladder: Responses API call site: tests/test_openai_responses.py is excluded because the proxy does not route /v1/responses; which of its recorded tests reach this site is not measured
- llm `llm/default_plugins/openai_models.py:2677`: excluded before its ladder: Responses API call site: tests/test_openai_responses.py is excluded because the proxy does not route /v1/responses; which of its recorded tests reach this site is not measured
- llm `llm/default_plugins/openai_models.py:2799`: excluded before its ladder: Responses API call site: tests/test_openai_responses.py is excluded because the proxy does not route /v1/responses; which of its recorded tests reach this site is not measured
- llm `llm/default_plugins/openai_models.py:2926`: excluded before its ladder: Responses API call site: tests/test_openai_responses.py is excluded because the proxy does not route /v1/responses; which of its recorded tests reach this site is not measured
- llm `llm/default_plugins/openai_models.py:1559`: found in the source, reached by none of the kept tests
- llm `llm/default_plugins/openai_models.py:1610`: found in the source, reached by none of the kept tests
- llm `llm/default_plugins/openai_models.py:2998`: found in the source, reached by none of the kept tests
- llm `llm/default_plugins/openai_models.py:3017`: found in the source, reached by none of the kept tests
- llm `llm/default_plugins/openai_models.py:519`: found in the source, reached by none of the kept tests
- paper-qa `src/paperqa/docs.py:374`: excluded before its ladder: embedding call site: served by the one Token Factory embedding model (Qwen3-Embedding-8B) on every rung and in every control, so its model is not a ladder choice; its tests flip only through the chat sites they also reach
- paper-qa `src/paperqa/docs.py:446`: excluded before its ladder: embedding call site: served by the one Token Factory embedding model (Qwen3-Embedding-8B) on every rung and in every control, so its model is not a ladder choice; its tests flip only through the chat sites they also reach
- paper-qa `src/paperqa/llms.py:251`: excluded before its ladder: embedding call site: served by the one Token Factory embedding model (Qwen3-Embedding-8B) on every rung and in every control, so its model is not a ladder choice; its tests flip only through the chat sites they also reach
- paper-qa `src/paperqa/docs.py:635`: reached by the tests, but no test fails on an empty or wrong answer there
- paper-qa `packages/paper-qa-nemotron/src/paperqa_nemotron/api.py:509`: found in the source, reached by none of the kept tests

## Smoke runs (not for decision)

- `instructor` smoke `20261024T114754Z-09131f` (S-instructor-super-k5, K=5, rungs Super): complete; **not for decision**

## Migration patches

- instructor: ladder run `20261024T114940Z-f6c56a`; moved 3 sites, kept 0; confirm {'instructor/v2/core/retry.py:224': 'pass', 'instructor/v2/core/retry.py:523': 'pass', 'instructor/v2/validation/llm_validators.py:34': 'pass'}; local branch `mmd/move-to-nemotron` commit `2318ca4be9b2`; PR text `evidence/census/instructor/patch/PR.md`

## What is not claimed

- baseline pass rate 1.0 is assumed (strictest), not measured by us
- K runs of one cell run in one sandbox at the same time, often with temperature 0: they are not independent, so the Wilson interval is optimistic
- covered = the test fails when the proxy answers with an empty message (must-differ control 1); tests that check only types or shapes pass any well-formed answer; a second control with well-formed wrong answers is run only where the manifest lists control-wrong
- streaming tests get server-sent events re-synthesized by the proxy from a non-streaming upstream call
- during the ladder the proxy normalizes requests per rung (logged per call); the patched app is confirmed separately without normalization
- per-call $ is priced on Nemotron's token counts; the baseline uses the same counts (another tokenizer differs)
- test traffic, not production traffic
- runs are set aside as infrastructure whenever the proxy logged 402/404/429/5xx/None at the site or at a site its flip tests reach, whatever their outcome, and re-run at most twice; each cell lists how many set-aside runs had passed and how many had failed (runs_with_retried_infra)
- after a run the hash check catches changed or missing frozen files; new files in test directories are recorded but do not stop the ladder
