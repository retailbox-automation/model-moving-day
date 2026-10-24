# Model Moving Day

An agent that moves an application's LLM calls to the smallest NVIDIA Nemotron model on Nebius Token Factory that
still passes the application's own tests, call site by call site. Built for the Nebius × NVIDIA Global AI Hackathon
(track: Coding and Agentic Engineering). Every number below comes from [CENSUS.md](CENSUS.md), which
`census/report.py` recomputes from the evidence files; CENSUS.md lists every cell, the red ones included.

## What was done

A census of three open-source LLM applications at pinned commits: `567-labs/instructor` (6cd771c, MIT),
`simonw/llm` (aebaca2, Apache-2.0) and `Future-House/paper-qa` (57e89f7, Apache-2.0, core install only; the AGPL
package paper-qa-pymupdf is not installed). Each repository's own tests ran live in Token Factory Sandboxes, every
model request went through a recording proxy, and the ladder picked a model per call site. For instructor and llm the
agent then wrote the migration as a local branch of the target and a run of the patched app confirmed it.

Results (test traffic, not production traffic):

- 8 call sites went into the ladder, in 3 repositories. 4 got a decision (instructor and llm); 3 of those 4 moved to
  Lightning or Nano.
- Counted as independent decisions (the sync and async faces of one funnel count once): 3, of which 2 on Lightning or
  Nano, Wilson 95 % interval [0.21-0.94]. Three decisions are too few for a narrow interval.
- The coverage gate set before the runs (decisions in at least 3 repositories and at least 5 independent sites) was
  NOT met: 2 repositories, 3 independent decisions.
- Token spend of all decision ladder runs: $0.5182. Two sandbox jobs were lost to client timeouts; their spend is not
  in that sum (each was capped at $1).

| Repository | Call site | What the call does | Model after the census | $/1k calls after / before |
|---|---|---|---|---|
| instructor | `instructor/v2/core/retry.py:224` | sync retry funnel: every structured-output call of a sync client | Lightning | $0.046 / $0.052 |
| instructor | `instructor/v2/core/retry.py:523` | async twin of the same funnel | Super | $0.170 / $0.058 |
| instructor | `instructor/v2/validation/llm_validators.py:34` | an LLM judges a field value | Nano | $0.101 / $0.199 |
| llm | `llm/default_plugins/openai_models.py:1494` | non-streaming chat call with tools | Nano + reasoning off | $0.030 / $0.052 |
| paper-qa | `src/paperqa/core.py:254`, `docs.py:234`, `docs.py:675`, `docs.py:703` | evidence summaries, citation parsing, the answer, the post prompt | no decision (infrastructure) | - |

"Before" prices the same measured calls (same token counts) at gpt-6-luna, the replacement the vendor recommends for
the models these tests name. The old model was not called.

Points worth reading in CENSUS.md:

- `retry.py:523` landed on Super and costs more than the vendor's base: $0.170 against $0.058 per 1,000 calls
  ($0.00974 against $0.00333 per passed test run). Nano and Lightning both failed 0/5 there, on
  `test_nested_model_extraction`. Moving a call to Nemotron does not always save money, and the census says so per
  site.
- `openai_models.py:1494` failed 0/5 on Nano: the answer began with a newline (`'\nYES' == 'YES'`). The agent's brain
  (Super) read the call log, proposed turning reasoning off, the change showed on 15 of 15 requests, and Nano then
  passed 5/5. The patch carries that request parameter.
- paper-qa got three decision runs, each declared in the manifest before it ran (the third with K runs in sequence and
  a $0.66 token budget). In all three, Token Factory answered HTTP 503 or 500 on Nano calls in most runs, and by the
  pre-registered rule such runs are set aside whatever their outcome. After two re-runs per run the four sites stayed
  "infra" and did not climb. In the third run two sandbox jobs were also lost to client timeouts; a later read of
  those operations is kept in `evidence/census/paper-qa/lost-jobs-posthoc-*.json` and is not counted (one of them also
  had a 500, so the outcome would not change). Gate not taken for paper-qa.
- Migration branches: instructor, 3 sites moved, branch `mmd/move-to-nemotron` (2318ca4); llm, 1 site moved
  (a90319e). Both live only in local clones: nothing was pushed to the upstream projects and no fork was created. The
  PR texts are `evidence/census/instructor/patch/PR.md` and `evidence/census/llm/patch/PR.md`.

Found, not measured (each with its reason in CENSUS.md): Responses API call sites (2 in instructor, 4 in llm),
because the proxy does not route `/v1/responses`; llm `openai_models.py:1441`, whose tests assert the old model's exact
wording; sites the tests reach where no test fails on a wrong answer (instructor `retry.py:483`, paper-qa
`docs.py:635`); sites no kept test reaches; paper-qa's three embedding call sites, which use the one Token Factory
embedding model on every rung, so their model is not a ladder choice.

## Real and synthetic

Real: the target repositories and their own tests at pinned commits, frozen by hash; live calls to Nemotron Nano,
Lightning, Super and Ultra and to Qwen3-Embedding-8B on Token Factory; the Sandboxes runs; the brain's diagnoses,
request fixes and patch edits; the patched branches and the confirm runs of the patched apps.

Synthetic or assumed:

- The two controls are made up by the proxy on purpose: an empty answer and a well-formed wrong answer. A test counts
  for a call site only if it fails on both.
- The baseline pass rate 1.0 is assumed (the strictest choice), not measured; the "before" dollars are a price
  calculation, not a run of the old model.
- Streaming tests get server-sent events re-synthesized by the proxy from a non-streaming upstream call.
- During the ladder the proxy normalizes requests per rung (logged per call); the patched app is confirmed without it.
- The offline tests and `python -m model_moving_day.fakes` use a fake model and a fake sandbox with scripted outcomes
  and illustrative prices; nothing from that dry run appears in CENSUS.md.

## How it works

1. `census prepare <repo>` installs the target repository at a pinned commit into a Token Factory Sandboxes image.
2. `census control <repo>` runs the repository's own tests three times through a recording proxy: with a real model,
   with an empty answer and with a well-formed wrong answer. A tracer tags every request with the call site and the
   test that sent it. A call site counts as covered only by tests that pass with the real model and fail with both
   broken answers.
3. `census manifest` writes `census/MANIFEST.json`: pinned commits, flip tests, frozen-file hashes, the ladder rules
   (K=5 runs per cell, at least 4 of 5 must pass, rungs Nano -> Lightning -> Super -> Ultra with model IDs and prices),
   what counts as an infrastructure error, budgets. It is pushed before the first ladder job.
4. `census ladder <repo>` climbs per call site and keeps the smallest rung that passes. Runs that hit 402/404/429/5xx
   are set aside and re-run, whatever their outcome. A red cell whose call log shows a diagnosis is shown to the
   brain (Nemotron Super or Ultra), which may propose one request fix; the fix counts only if the request really
   changed. The next rung up is measured as the fallback.
5. `census patch <repo>` writes the migration as a local branch of the target: an opt-in helper (`NEMOTRON_MOVE=1`)
   with the measured model and request parameters per call site, wrapped in by the brain, checked so that with the
   flag off the code is the original, and confirmed by a run of the patched app.

Requirements: Python 3.12, `pip install -e '.[dev,sandbox]'`, a Token Factory key in `NEBIUS_API_KEY` and a
Sandboxes project in `NEBIUS_PROJECT_ID`. Offline tests: `python -m pytest -q`. CENSUS.md:
`python census/report.py`.

## Limits

- The ladder measures behaviour with the repository's own tests only; tests that check only types or shapes are
  dropped by the wrong-answer control, and the remaining ones are listed per call site in the manifest.
- K runs of one cell are not independent (same sandbox, often temperature 0); the intervals are optimistic. With K=5
  a cell cannot tell a model that passes 80 % of the time from one that always passes.
- Per-call dollars use Nemotron's token counts for both sides; another tokenizer would count differently.
- Token Factory Sandboxes are in beta; their `cost` field is logged as a diagnostic (the 2026-10-24 billing check
  found no Sandboxes charge). Token spend has a hard cap in code; Token Factory itself has none.
- The Token Factory key used inside the sandboxes is uploaded as a file (deleted inside the sandbox at once, kept in
  the Sandboxes file store); it is revoked after the event.
- More in CENSUS.md, "What is not claimed".

## Pre-registration

- The platform spike plan (`evidence/spikes/PLAN.md`, commit d5fb0dd) and the Sandboxes spike plan
  (`evidence/sandbox/PLAN.md`, commit 8fac6be) were committed locally before their runs and pushed afterwards: the
  branch reached GitHub at 2026-10-23T16:42:37Z, after the runs. Local commit times are set by the author; the
  server-side `created` timestamps in `evidence/spikes/raw/*.jsonl` and the operation IDs in
  `evidence/sandbox/runs.jsonl` show the runs started after those commit times.
- The census manifest (`census/MANIFEST.json`: pinned commits, flip tests, frozen-file hashes, K, margin, rungs,
  models, prices, budgets, declared smoke runs) is pushed before the first ladder job. The ladder refuses to run
  when the manifest is not on the remote branch or does not match the code, the evidence or the clones, and every
  ladder file records the manifest's SHA-256 and the push. The manifest has seven amendments, each recording the
  previous version's SHA-256. The first two came before any ladder job. The five after it keep the ladder rules and the
  instructor and llm entries unchanged: amendment 4 adds paper-qa, amendment 7 changes only how paper-qa's runs
  execute (in sequence, with a $0.66 token budget), the others change code only.
