# Model Moving Day

An agent that moves an application's LLM calls to the smallest NVIDIA Nemotron model on Nebius Token Factory that
still passes the application's own tests, call site by call site. Built for the Nebius × NVIDIA Global AI Hackathon
(track: Coding and Agentic Engineering). Results: [CENSUS.md](CENSUS.md), every cell including the red ones.

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
Sandboxes project in `NEBIUS_PROJECT_ID`. Offline tests: `python -m pytest -q`.

## Limits

- The ladder measures behaviour with the repository's own tests only; tests that check only types or shapes are
  dropped by the wrong-answer control, and the remaining ones are listed per call site in the manifest.
- K runs of one cell are not independent (same sandbox, often temperature 0); the intervals are optimistic.
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
  ladder file records the manifest's SHA-256 and the push.
