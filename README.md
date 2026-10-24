# Model Moving Day

Which NVIDIA Nemotron model on Nebius Token Factory each LLM call site needs, decided by the repository's own tests.
First census: 4 call sites decided in 2 open-source libraries; 3 run on Lightning or Nano, 1 needed Super and costs about 3× more per call than OpenAI's replacement model, priced on the same tokens.
The coverage gate we set beforehand (3 repositories, 5 independent decisions) was not met. Every cell, failures included: [CENSUS.md](CENSUS.md).

Built for the Nebius × NVIDIA Global AI Hackathon (track: Coding and Agentic Engineering). Every number below comes
from CENSUS.md, which `census/report.py` recomputes from the evidence files.

## What was done

A census of three open-source LLM libraries and tools at pinned commits: `567-labs/instructor` (6cd771c, MIT),
`simonw/llm` (aebaca2, Apache-2.0) and `Future-House/paper-qa` (57e89f7, Apache-2.0, core install only; the AGPL
package paper-qa-pymupdf is not installed). Each repository's own tests ran live in Token Factory Sandboxes, every
model request went through a recording proxy, and a ladder picked a model per call site. For instructor and llm the
agent then wrote the migration as a local branch of the target, and a run of the patched code confirmed it.

instructor and llm are libraries, so the call sites that moved are library funnels: one call through which every user
request passes. A decision says that the library's own test prompts pass on that model. It does not say that an
application built on the library would.

Results (test traffic, not production traffic):

- 26 LLM call sites found; 8 could be measured with the repositories' own tests and went into the ladder; 18 were
  found but not measured, each with its reason in CENSUS.md.
- 4 call sites got a decision, in 2 repositories (instructor and llm); 3 of them moved to Lightning or Nano.
- Counted per call site that is 4 independent decisions, 3 on Lightning or Nano. If the sync and async faces of
  instructor's retry funnel count as one site (at the larger of their two models), it is 3 decisions, 2 on Lightning
  or Nano. The two faces share no test.
- Coverage gate (decisions in at least 3 repositories, at least 5 independent sites): NOT met, with 2 repositories
  and 3 or 4 decisions. The gate and the rule that a sync/async pair counts once were written on 2026-10-24 in our
  internal acceptance notes, before the ladder runs, but they are not in the pushed manifest. Counting a pair at its
  larger model was added to the report script after the runs.
- Each decision rests on few tests: 8, 23, 2 and 1 (table below). The two sites that moved to Nano rest on 3 tests
  between them, and retry.py:523 went to Super because of one test. Three hand-picked repositories are not a sample,
  so we give no share or interval as an estimate of how often call sites can move.

| Repository | Call site | What the call does | Flip tests | Model after the census | $/1k calls after / before |
|---|---|---|---|---|---|
| instructor | `instructor/v2/core/retry.py:224` | sync retry funnel for structured output | 8 | Lightning | $0.046 / $0.052: about the same |
| instructor | `instructor/v2/core/retry.py:523` | async face of the same funnel | 23 | Super | $0.170 / $0.058: 2.9× more |
| instructor | `instructor/v2/validation/llm_validators.py:34` | an LLM judges a field value | 2 | Nano | no saving (see below) |
| llm | `llm/default_plugins/openai_models.py:1494` | non-streaming chat call with tools | 1 | Nano + reasoning off | $0.030 / $0.052 |
| paper-qa | `src/paperqa/core.py:254`, `docs.py:234`, `docs.py:675`, `docs.py:703` | evidence summaries, citation parsing, the answer, the post prompt | 5, 5, 5, 1 | no decision (infrastructure) | - |

Flip tests are the repository's own tests that reach the call site, pass with a working answer and fail with a broken
one.

### The dollars

"Before" is a price calculation, not a run: the same calls, at the prompt and visible output token counts Nemotron
produced, priced at gpt-6-luna ($0.10 input / $0.50 output per 1M tokens). gpt-6-luna is the model OpenAI names as the
replacement for GPT-4o, GPT-4o mini, GPT-4.1 and GPT-4.1 mini, which shut down on 2027-01-14 and are the models these
tests name. The price and the replacement come from our notes of 2026-10-20 and were rechecked against OpenAI's live
pricing and deprecation pages on 2026-10-24 at 14:36 UTC (`evidence/platform/openai-pricing-check-2026-10-24.md`).
gpt-6-luna was never called, and its pass rate is assumed to be 1.0.

- Tokenizers differ: on the same llm test with the same number of calls, Nano counted 409 prompt tokens per call and
  Lightning 545 (+33 %). The 11 % gap at retry.py:224 is within that, so Lightning there costs about the same as the
  base.
- Nano and Lightning cost the same per token on Token Factory ($0.06 / $0.24 per 1M). The smallest rung that passes
  is not cheaper by itself; the cost follows the tokens each model produces.
- At llm_validators.py:34 the table in CENSUS.md shows $0.101 against a base of $0.199, but that base is too high.
  Token Factory reports no reasoning tokens for Nano (no breakdown in 83 of 83 Nano responses in our spikes), so
  Nano's hidden reasoning is counted as visible output. Sized by Lightning's visible output at the same site, the base
  is about $0.09 per 1,000 successful calls against Nano's $0.128. Nano is not cheaper there, and the measured
  Lightning fallback ($0.073) is cheaper than Nano.
- At retry.py:523 Super costs $0.170 against $0.058 per 1,000 calls, 2.9× the base. Moving a call to Nemotron does not
  always save money.
- The per-1,000-call figures include error responses (503) that carried no tokens: 34 of 286 calls for Super at
  retry.py:523, 3 of 14 for Nano at llm_validators.py:34, 15 of 110 for the Super fallback at retry.py:224. Both sides
  are lowered alike, so the after/before ratios hold. CENSUS.md's "spend per passed run" includes set-aside re-runs.

### Call site by call site

- instructor's retry funnel. The sync face (retry.py:224) passed on Lightning 5/5 with its 8 tests, none of which uses
  a nested model. The async face (retry.py:523) has 23 tests. On Lightning, 22 of them passed in every run, but
  `test_nested_model_extraction` failed 0/5, so the site went up to Super. Nano failed there 0/5 by runs, with failures
  spread over 5 tests (nested extraction passed 2 of 5). Super passed 5/5 on its re-run. Its first five runs were set
  aside because Token Factory returned 503s during them (2 to 8 per run); underneath, 4 of those 5 had failed. The
  pre-registered rule sets such runs aside whatever their outcome, CENSUS.md shows both batches, and we did not tie
  each failed test to a particular 503. A separate smoke run, declared beforehand and not used for the decision, had
  Super at 5/5 on this site without 503s.
- llm `openai_models.py:1494`. Nano failed 0/5 because the answer began with a newline (`'\nYES' == 'YES'`). This was
  expected: the same newline had shown up on Super in the earlier control runs. A proxy rule flags it as
  `leading_whitespace`. The pre-registered fix list allows only "reasoning off" for that diagnosis. The brain (Super)
  picked it from three options, and its system prompt says that with reasoning on, content can start with a newline.
  The change (`enable_thinking=false`) reached 15 of 15 requests, and Nano then passed 5/5. The decision rests on one
  test, a YES/NO answer at the end of a chain of two tool calls. Its good side is llm's recorded cassette of
  gpt-4o-mini; Super failed this test live. llm is moved only partly. Only the non-streaming branch moved. The
  streaming branch (`:1441`, llm's default for prompts and the CLI) was not measured, because its tests assert the old
  model's exact sentences. The async branches have no live tests, and our proxy does not route the Responses API
  sites.
- paper-qa. Three decision runs, each declared in the manifest before it ran; no decision. In the first two, the proxy
  logged 89 and 136 error responses (503/500) from Token Factory, almost all on Nano calls; almost every run was set
  aside, and the sites did not climb. The third run was changed after those two failures: K runs in sequence and a $0.66 token
  budget, declared in amendment 7 before the run. Of its 11 set-aside runs, 8 were lost by our own Sandboxes client,
  whose status polls timed out, and 3 had 503/500. One lost job had in fact finished; we read it afterwards
  (`evidence/census/paper-qa/lost-jobs-posthoc-*.json`) and did not count it, and it also had a 500. paper-qa's tests
  call Semantic Scholar live, and in the second run one Semantic Scholar 429 slipped through as a model failure: the
  infrastructure rule sees only the proxy's statuses. paper-qa also ran on a newer harness than instructor and llm:
  amendments 3 and 4 changed the proxy, the job runner and the tracer after those two ladders (embeddings, import
  stubs, install environment). Gate not taken for paper-qa.

### Migration branches

instructor: 3 sites moved, branch `mmd/move-to-nemotron` (2318ca4). It took four attempts. Two were cut off by 503s
from Ultra (the brain for that repository). In the third, the brain wrote a wrong import: every confirm run failed at
test collection and nothing was committed. We then gave the brain the exact import line and narrowed the check to
accept only that line (amendment 5). The fourth attempt passed confirm 5/5 on all three sites. All attempts are kept
under `evidence/census/instructor/patch-attempt*/`.

llm: 1 site moved (a90319e), confirm 5/5. Both branches live only in local clones: nothing was pushed to the upstream
projects and no fork was created. The PR texts are `evidence/census/instructor/patch/PR.md` and
`evidence/census/llm/patch/PR.md`. Two things in them are wrong. The "Models requested" column shows the count from
one test, not the total. The totals from the confirm runs are: instructor, 130 Super calls at the async funnel and
55 Lightning plus 10 Nano calls at the sync funnel (the validator's calls also pass through it); llm, 15 Nano calls.
Their "What is not claimed" line says the wrong-answer control runs only where the manifest lists it; it ran in all
three repositories.

### Found, not measured

Each with its reason in CENSUS.md: Responses API call sites (2 in instructor, 4 in llm), because the proxy does not
route `/v1/responses`; llm `openai_models.py:1441`, whose tests assert the old model's exact wording; sites the tests
reach where no test fails on a wrong answer (instructor `retry.py:483`, paper-qa `docs.py:635`); sites no kept test
reaches; paper-qa's three embedding call sites, which use the one Token Factory embedding model on every rung, so their
model is not a ladder choice.

## Real and synthetic

Real: the target repositories and their own tests at pinned commits, frozen by hash; live calls to Nemotron Nano,
Lightning, Super and Ultra and to Qwen3-Embedding-8B on Token Factory; the Sandboxes runs; the brain's fix choice and
patch edits; the patched branches and the confirm runs of the patched code.

Synthetic or assumed:

- Coverage comes from three control runs: one with Nemotron Super (for llm :1494, the repository's recorded cassette
  of gpt-4o-mini instead, since Super failed it live), one with an empty answer and one with a well-formed wrong
  answer. Both broken answers are made up by the proxy. A test counts only if it passes the first and fails the other
  two, so tests that Super failed live were dropped (3 in paper-qa).
- The baseline pass rate 1.0 is assumed, and the "before" dollars are a price calculation; gpt-6-luna was not called.
- Streaming tests get server-sent events re-synthesized by the proxy from a non-streaming upstream call.
- During the ladder the proxy normalizes requests per rung (logged per call); the patched code is confirmed without it.
- The '\nYES' fix was provided for in advance (proxy rule, pre-registered fix list, hint in the brain's prompt).
- The offline tests and `python -m model_moving_day.fakes` use a fake model and a fake sandbox with scripted outcomes
  and illustrative prices; nothing from that dry run appears in CENSUS.md.

## Spend

Model tokens for the decision ladders: $0.52. All C1 spend in our ledger (read 2026-10-24 14:41 UTC): $1.39
settled. $0.49 of that is the Sandboxes `cost` field logged on 2026-10-23, before we knew Sandboxes are not billed, so
model tokens come to about $0.90. On top of that, $0.03 for one lost job (read afterwards) and up to $1 unknown for
the other. Token Factory billing showed no Sandboxes charge when checked on 2026-10-24 (last check at 13:47 UTC, with
Usage data as of 12:11 UTC), so runs after 12:11 UTC are not covered by that check. The Sandboxes `cost` field ($5.33
over the decision runs) is a diagnostic, not spend.

## How it works

1. `census prepare <repo>` installs the target repository at a pinned commit into a Token Factory Sandboxes image.
2. `census control <repo>` runs the repository's own tests three times through a recording proxy: with a working
   model, with an empty answer and with a well-formed wrong answer. A tracer tags every request with the call site
   and the test that sent it. A call site counts as covered only by tests that pass the first run and fail the other
   two.
3. `census manifest` writes `census/MANIFEST.json`: pinned commits, flip tests, frozen-file hashes, the ladder rules
   (K=5 runs per cell, at least 4 of 5 must pass, rungs Nano -> Lightning -> Super -> Ultra with model IDs and prices),
   what counts as an infrastructure error, the fix list, budgets. It is pushed before the first ladder job.
4. `census ladder <repo>` climbs per call site and keeps the smallest rung that passes. Runs that hit 402/404/429/5xx
   are set aside and re-run, whatever their outcome. A red cell whose call log shows a diagnosis (set by a proxy rule)
   is shown to the brain (Nemotron Super or Ultra), which picks one fix from the pre-registered list or none; the fix
   counts only if the request really changed. The next rung up is measured as the fallback.
5. `census patch <repo>` writes the migration as a local branch of the target: an opt-in helper (`NEMOTRON_MOVE=1`)
   with the measured model and request parameters per call site, wrapped in by the brain, checked so that with the
   flag off the code is the original, and confirmed by a run of the patched code.

Requirements: Python 3.12, `pip install -e '.[dev,sandbox]'`, a Token Factory key in `NEBIUS_API_KEY` and a
Sandboxes project in `NEBIUS_PROJECT_ID`. Offline tests: `python -m pytest -q`. CENSUS.md:
`python census/report.py`.

## Limits

- The gate was not met: decisions in 2 of 3 repositories, 3 or 4 independent decisions depending on how instructor's
  sync and async faces are counted. The gate and the pairing rule are in our internal notes, not in the manifest.
- The repositories were picked by hand and the sites are few; the share on Lightning or Nano is not an estimate of how
  often LLM calls can move.
- instructor and llm are libraries: a decision covers the library's own test prompts, not the schemas of applications
  built on them.
- Each decision rests on few tests (8 / 23 / 2 / 1); the two Nano decisions on 3 tests between them; retry.py:523
  went to Super because of one test.
- K=5 runs of one cell are not independent (same sandbox, in parallel, often temperature 0). A cell cannot tell a
  model that passes 80 % of the time from one that always passes.
- "Before" is a price calculation at gpt-6-luna prices on Nemotron's token counts; gpt-6-luna was never called and
  its pass rate is assumed. Tokenizers differ by up to about 33 %, and for Nano the base is overstated because Token
  Factory reports no Nano reasoning tokens.
- Nano and Lightning cost the same per token; the smallest passing rung is not always the cheapest (at
  llm_validators.py:34 Lightning is cheaper than Nano).
- llm :1494 rests on one test whose good side is a recording; only llm's non-streaming branch moved.
- The '\nYES' fix was provided for in advance.
- retry.py:523's Super cell passed on its re-run; its first batch was set aside for 503s, and 4 of those 5 runs had
  failed underneath.
- paper-qa has no decision: 503/500 from Token Factory on Nano, two jobs lost to our Sandboxes client, one Semantic
  Scholar 429 counted as a model failure. Its third run used a newer harness and a changed execution (sequential runs,
  $0.66 budget), introduced after two failed runs.
- 18 of 26 found call sites were not measured; the reason is given for each.
- The controls are synthetic, the proxy re-synthesizes streaming, and it normalizes requests during the ladder; the
  patched code is confirmed without normalization.
- The instructor patch took four attempts. The branches are local only: upstream untouched, no forks.
- Spend as above. Token Factory Sandboxes are in beta; their `cost` field is a diagnostic, and the billing check covers
  data up to 12:11 UTC on 2026-10-24. Token spend has a hard cap in code; Token Factory itself has none.
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
  models, prices, the fix list, budgets, declared smoke runs) is pushed before the first ladder job. The ladder refuses
  to run when the manifest is not on the remote branch or does not match the code, the evidence or the clones, and
  every ladder file records the manifest's SHA-256 and the push. The manifest has seven amendments, each recording
  the previous version's SHA-256. The first two came before any ladder job. The five after it keep the ladder rules
  and the instructor and llm entries unchanged: amendment 4 adds paper-qa, amendment 7 changes only how paper-qa's
  runs execute (in sequence, with a $0.66 token budget; adaptive, since it came after two failed runs), the others
  change code only. Amendment 7's reason text reads "/bin/bash.66" where "$0.66" was meant (the shell expanded `$0`
  when the text was written); the binding field `token_budget_usd` is 0.66.
- Not in the manifest: the coverage gate and the rule for counting independent decisions (internal notes, written
  before the runs), and the rule that a pair counts at its larger model (report script, after the runs).
