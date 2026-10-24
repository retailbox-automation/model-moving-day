# Model Moving Day: census of three open-source LLM libraries and tools

Every number below is recomputed by `census/report.py` from the evidence in this repository (`evidence/census/<repo>/ladder-*.json`, `coverage.json`, `census/MANIFEST.json`). Test traffic, not production traffic.

## Headline

- LLM call sites found: 26 in 3 repositories. Measurable with the repositories' own tests and put into the ladder: 8. Found but not measured: 18, each with its reason under "Found, not measured".
- Decided: 4 call sites, in 2 repositories (instructor, llm); on Lightning or Nano: 3 of 4. Each decision rests on that site's flip tests: 8 at `instructor/v2/core/retry.py:224`, 23 at `instructor/v2/core/retry.py:523`, 2 at `instructor/v2/validation/llm_validators.py:34`, 1 at `llm/default_plugins/openai_models.py:1494`; the sites that moved to Nano rest on 3 tests between them.
- Independent decisions: 4 counted per call site (3 on Lightning or Nano); 3 if the sync and async faces of instructor's retry funnel count as one site at the larger of their two models (2 on Lightning or Nano). The two faces share no flip test.
- Coverage gate (at least 3 repositories and 5 independent decided sites): NOT met: 2 repositories with a decision, 3 or 4 independent decisions. The gate and the rule that a sync/async pair counts once were written on 2026-10-24 in our internal acceptance notes before the ladder runs; they are not in the pushed manifest. Counting a group at its largest model was added to this script after the runs.
- For reference only, Wilson 95 % intervals of the share on Lightning or Nano, which are not an estimate of any population (three hand-picked repositories): 3/4 per site [0.30-0.95], 2/3 grouped [0.21-0.94].
- Runs set aside as infrastructure (rate limits, 5xx, lost jobs) and re-run, counted per sandbox test run (sites that share a job share its runs): 22 in the runs the headline uses, 48 in all decision runs. Sites with no decision because of infrastructure: 4.
- Model tokens for all decision ladder runs: $0.5182. Controls, the smoke run, the brain, confirm runs and spikes are not in this sum, nor 2 sandbox jobs lost to client timeouts (each capped by a $1 envelope). The Sandboxes `cost` field summed to $5.33 over these runs; it is diagnostic: Token Factory billing showed no Sandboxes charge when checked on 2026-10-24 (last check at 13:47 UTC, with Usage data as of 12:11 UTC), so runs after 12:11 UTC are not covered by that check.

## Which model does what

| Repository | Call site | What the call does | Flip tests | Model after the census | $/1k calls after | $/1k calls before (gpt-6-luna, priced) | After / before | Spend per passed run, set-aside re-runs included, after / before | Calls per test run |
|---|---|---|---|---|---|---|---|---|---|
| instructor | `instructor/v2/core/retry.py:224` | instructor's sync retry funnel for structured output (its tests have no nested model) | 8 | **Lightning** | $0.046 | $0.052 | 0.89 (about the same: within tokenizer differences) | $0.00051 / $0.00057 | 1.375 |
| instructor | `instructor/v2/core/retry.py:523` | the async twin of the same funnel (its tests include a nested model) | 23 | **Super** | $0.170 | $0.058 | 2.92 | $0.00974 / $0.00333 | 1.243 |
| instructor | `instructor/v2/validation/llm_validators.py:34` | llm_validator: an LLM judges a field value (accept or reject with a reason) | 2 | **Nano** | $0.101 | $0.199 | not shown, see note 1 | $0.00028 / $0.00056 | 1.167 |
| llm | `llm/default_plugins/openai_models.py:1494` | llm's non-streaming chat call (tools, chains of tool calls) | 1 | **Nano** + reasoning_off | $0.030 | $0.052 | 0.58 | $0.00009 / $0.00015 | 3.0 |
| paper-qa | `src/paperqa/core.py:254` | paper-qa: summarize one evidence chunk for the question (summary_llm, the high-volume step) | 5 | infra | - | - | - | - / - | - |
| paper-qa | `src/paperqa/docs.py:234` | paper-qa: parse a citation into structured fields | 5 | infra | - | - | - | - / - | - |
| paper-qa | `src/paperqa/docs.py:675` | paper-qa: write the answer from the evidence | 5 | infra | - | - | - | - / - | - |
| paper-qa | `src/paperqa/docs.py:703` | paper-qa: post-prompt after the answer | 1 | infra | - | - | - | - / - | - |

"Before" is a price calculation, not a run: the same calls, at the prompt tokens and visible output tokens Nemotron produced, priced at gpt-6-luna ($0.1/$0.5 per 1M tokens), the model OpenAI names as the replacement for the models these tests name (platform.openai.com/docs/pricing.md and /docs/deprecations.md, read 2026-10-20 (docs/kb/05-c1-domain.md); rechecked against OpenAI's live pages on 2026-10-24, `evidence/platform/openai-pricing-check-2026-10-24.md`). gpt-6-luna was never called, and its pass rate is assumed to be 1.0.

1. `instructor/v2/validation/llm_validators.py:34`: Token Factory reports no reasoning tokens for Nano (none in this cell, and none in 83 of 83 Nano responses of our spikes), so the base $0.199 counts Nano's hidden reasoning as visible output and is too high. Sized by the visible output of Lightning at the same site (77 tokens per call), the base is $0.090-$0.091 per 1,000 successful calls, against Nano's $0.128: on this site Nano is not cheaper than the base, and the measured Lightning fallback cell ($0.073 per 1,000 successful calls) is cheaper than Nano. The "before" spend per passed run in this row is too high for the same reason.

Notes on the dollar columns:

- Nano and Lightning cost the same per token on Token Factory ($0.06 in / $0.24 out per 1M), so a smaller rung is not cheaper by itself: the cost follows the tokens each model produces.
- Tokenizers differ: at `llm/default_plugins/openai_models.py:1494` (same tests, same number of calls) Nano counted 409 prompt tokens per call and Lightning 545 (+33 %). A difference of 10-15 % against the base is within that.
- `instructor/v2/core/retry.py:224` Super (fallback): 15 of 110 calls were error responses (503) with no tokens; they are in the denominator of $/1k calls, which lowers both sides alike and leaves after / before unchanged.
- `instructor/v2/core/retry.py:523` Super (ladder): 34 of 286 calls were error responses (503) with no tokens; they are in the denominator of $/1k calls, which lowers both sides alike and leaves after / before unchanged.
- `instructor/v2/validation/llm_validators.py:34` Nano (ladder): 3 of 14 calls were error responses (503) with no tokens; they are in the denominator of $/1k calls, which lowers both sides alike and leaves after / before unchanged.
- "Spend per passed run" divides everything the cell spent, set-aside runs included, by its passed runs; where a batch was set aside it is higher than the cost of one clean run.

Diagnosis and fixes: a proxy rule sets the diagnosis from the call log; the brain (Super or Ultra by repo size) is asked only for a red cell with a diagnosis and picks from the pre-registered fix list; a fix counts only if the request really changed. The embedding calls of paper-qa go to Qwen/Qwen3-Embedding-8B on every rung.

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

Set aside: runs set aside as infrastructure; in brackets how many of them had passed and how many had failed underneath (runs of a lost job have no outcome).

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

Set aside: runs set aside as infrastructure; in brackets how many of them had passed and how many had failed underneath (runs of a lost job have no outcome).

Fix at `llm/default_plugins/openai_models.py:1494`: **reasoning_off**, picked by `nvidia/nemotron-3-super-120b-a12b` ("The leading newline appears because reasoning is on by default and can cause content to start with a newline, as seen in the assertion failure where the output began with '\nYES'."); the request change showed on 15 calls. How much of this the brain found by itself: the diagnosis comes from a proxy rule on the call log, the pre-registered fix list allows only this fix for that diagnosis, the brain chose from three options (reasoning_off, raise_max_tokens, none), and its system prompt states that with reasoning on, content can start with a newline.

### paper-qa

- Decision run `20261024T122751Z-764b19`: complete, 0 of 4 sites decided, 3 sandbox jobs, $0.1612 in tokens, proxy error statuses logged {'500': 3, '503': 86}
- Decision run `20261024T124923Z-ef27e9`: complete, 0 of 4 sites decided, 3 sandbox jobs, $0.1195 in tokens, proxy error statuses logged {'500': 3, '503': 133}
- Decision run `20261024T131312Z-8fa47f`: complete, 0 of 4 sites decided, 3 sandbox jobs (2 lost to client timeouts while polling the Sandboxes API: no summary came back, so their 8 runs were set aside), $0.0514 in tokens, proxy error statuses logged {'500': 1, '503': 20} (the headline uses this run)
- Run `20261024T124923Z-ef27e9`: a run counted as a model failure at `src/paperqa/core.py:254` failed on an external service the tests call live (`api.semanticscholar.org`), not on the model; the infrastructure rule sees only the proxy's statuses
- Post-hoc read of lost jobs, not counted (summary lost = infrastructure by the pre-registered rule): `evidence/census/paper-qa/lost-jobs-posthoc-20261024T131312Z-8fa47f.json`

#### Run `20261024T122751Z-764b19`

Decision run `20261024T122751Z-764b19` (2026-10-24T12:27:51Z to 2026-10-24T12:42:49Z), manifest `fee85e44a3c3` pushed 2026-10-24T12:27:11Z, K=5, pass = at least 4 of 5 valid runs.

| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |
|---|---|---|---|---|---|---|---|---|
| `src/paperqa/core.py:254` | ladder | Nano | - | infra | 0/0 | 15 (8, 7) | $0.219 | - |
| `src/paperqa/docs.py:234` | ladder | Nano | - | infra | 0/0 | 15 (8, 7) | $0.082 | - |
| `src/paperqa/docs.py:675` | ladder | Nano | - | infra | 0/0 | 15 (8, 7) | $0.158 | - |
| `src/paperqa/docs.py:703` | ladder | Nano | - | infra | 0/0 | 15 (13, 2) | $0.011 | - |

Set aside: runs set aside as infrastructure; in brackets how many of them had passed and how many had failed underneath (runs of a lost job have no outcome).

#### Run `20261024T124923Z-ef27e9`

Decision run `20261024T124923Z-ef27e9` (2026-10-24T12:49:23Z to 2026-10-24T13:02:18Z), manifest `41981436bbd6` pushed 2026-10-24T12:45:59Z, K=5, pass = at least 4 of 5 valid runs.

| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |
|---|---|---|---|---|---|---|---|---|
| `src/paperqa/core.py:254` | ladder | Nano | - | infra | 1/2 | 11 (3, 8) | $0.224 | tests/test_paperqa.py::test_unrelated_context[default-unsure]: httpx.HTTPStatusError: Client error '429 Too Many Request |
| `src/paperqa/docs.py:234` | ladder | Nano | - | infra | 1/2 | 11 (3, 8) | $0.054 | tests/test_paperqa.py::test_unrelated_context[default-unsure]: httpx.HTTPStatusError: Client error '429 Too Many Request |
| `src/paperqa/docs.py:675` | ladder | Nano | - | infra | 1/2 | 11 (3, 8) | $0.108 | tests/test_paperqa.py::test_unrelated_context[default-unsure]: httpx.HTTPStatusError: Client error '429 Too Many Request |
| `src/paperqa/docs.py:703` | ladder | Nano | - | infra | 2/2 | 11 (7, 4) | $0.016 | - |

Set aside: runs set aside as infrastructure; in brackets how many of them had passed and how many had failed underneath (runs of a lost job have no outcome).

#### Run `20261024T131312Z-8fa47f`

Decision run `20261024T131312Z-8fa47f` (2026-10-24T13:13:12Z to 2026-10-24T14:06:42Z), manifest `273943f70ab5` pushed 2026-10-24T13:13:02Z, K=5, pass = at least 4 of 5 valid runs.

| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |
|---|---|---|---|---|---|---|---|---|
| `src/paperqa/core.py:254` | ladder | Nano | - | infra | 2/2 | 11 (2, 1) | $0.203 | - |
| `src/paperqa/docs.py:234` | ladder | Nano | - | infra | 2/2 | 11 (2, 1) | $0.073 | - |
| `src/paperqa/docs.py:675` | ladder | Nano | - | infra | 2/2 | 11 (2, 1) | $0.186 | - |
| `src/paperqa/docs.py:703` | ladder | Nano | - | infra | 2/2 | 11 (3, 0) | $0.023 | - |

Set aside: runs set aside as infrastructure; in brackets how many of them had passed and how many had failed underneath (runs of a lost job have no outcome).

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

- instructor: ladder run `20261024T114940Z-f6c56a`; moved 3 sites, kept 0; confirm {'instructor/v2/core/retry.py:224': 'pass', 'instructor/v2/core/retry.py:523': 'pass', 'instructor/v2/validation/llm_validators.py:34': 'pass'}; local branch `mmd/move-to-nemotron` commit `2318ca4be9b2`; PR text `evidence/census/instructor/patch/PR.md`; 3 earlier attempts kept under `instructor/patch-attempt*/` (nothing committed by them)
  - confirm run `20261024T123251Z-c41f4d`: sites `instructor/v2/core/retry.py:224` confirm 0/0 infra; `instructor/v2/core/retry.py:523` confirm 0/0 infra; `instructor/v2/validation/llm_validators.py:34` confirm 0/0 infra; model calls by the call site the tracer recorded in the patched code: none
  - confirm run `20261024T124642Z-2a6fd0`: sites `instructor/v2/core/retry.py:224` confirm 5/5 pass; `instructor/v2/core/retry.py:523` confirm 5/5 pass; `instructor/v2/validation/llm_validators.py:34` confirm 5/5 pass; model calls by the call site the tracer recorded in the patched code: {'instructor/v2/core/retry.py:525': {'Super': 130}, 'instructor/v2/core/retry.py:226': {'Lightning': 55, 'Nano': 10}}
- llm: ladder run `20261024T115937Z-6e8505`; moved 1 sites, kept 0; confirm {'llm/default_plugins/openai_models.py:1494': 'pass'}; local branch `mmd/move-to-nemotron` commit `a90319e522c0`; PR text `evidence/census/llm/patch/PR.md`
  - confirm run `20261024T130831Z-9d0fdb`: sites `llm/default_plugins/openai_models.py:1494` confirm 5/5 pass; model calls by the call site the tracer recorded in the patched code: {'llm/default_plugins/openai_models.py:1495': {'Nano': 15}}

The "Models requested" column in the PR texts shows the count from one test, not the total; the totals per recorded site are listed above.

## What is not claimed

- baseline pass rate 1.0 is assumed (strictest), not measured by us
- K runs of one cell run in one sandbox at the same time, often with temperature 0: they are not independent, so the Wilson interval is optimistic
- covered = the test fails when the proxy answers with an empty message (must-differ control 1); tests that check only types or shapes pass any well-formed answer; a second control with well-formed wrong answers is run only where the manifest lists control-wrong
  - Correction: this line dates from the first manifest and is kept so the manifest is not changed after the runs. The wrong-answer control ran in all three repositories (`evidence/census/*/control-wrong.json`).
- streaming tests get server-sent events re-synthesized by the proxy from a non-streaming upstream call
- during the ladder the proxy normalizes requests per rung (logged per call); the patched app is confirmed separately without normalization
- per-call $ is priced on Nemotron's token counts; the baseline uses the same counts (another tokenizer differs)
- test traffic, not production traffic
- runs are set aside as infrastructure whenever the proxy logged 402/404/429/5xx/None at the site or at a site its flip tests reach, whatever their outcome, and re-run at most twice; each cell lists how many set-aside runs had passed and how many had failed (runs_with_retried_infra)
- after a run the hash check catches changed or missing frozen files; new files in test directories are recorded but do not stop the ladder

## Notes on the manifest

- The reason text of the amendment written at 2026-10-24T13:12:39Z reads "/bin/bash.66" where "$0.66" was meant (the shell expanded `$0` when the text was written). The binding field is correct: token_budget_usd {'paper-qa': 0.66}.
