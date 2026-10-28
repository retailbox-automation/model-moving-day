# Recorded re-run of one call site, 2026-10-29 (not part of the census)

Written and pushed before the run. The census results (CENSUS.md, the ladder files under `evidence/census/`) do not
change: this run happens in a separate checkout (branch `rerun-2026-10-29`), and its files are copied here afterwards.

## Why

The video and the demo replay the census of 2026-10-24. This run shows the same agent doing the same work again,
live and on screen, with every model request traceable: each call carries the Token Factory response id.

## What runs

Target `llm` (simonw/llm at the commit in `census/MANIFEST.json`), call site `llm/default_plugins/openai_models.py:1494`,
under manifest amendment 8 (written on this branch before the run; the census used amendments 1-7, the last one
commit ac9c8f1, pushed 2026-10-24T13:13:02Z). Amendment 8 changes only the hashes of four logging-only code files
listed below; unchanged rules: K=5 runs per
cell, at least 4 of 5 must pass, rungs Nano -> Lightning -> Super -> Ultra, runs that hit 402/404/429/5xx set aside.

1. `python -m model_moving_day.census ladder llm`: the ladder for this site, the brain (Nemotron Super) on any red cell
   that has a diagnosis, the fallback rung measured.
2. `python -m model_moving_day.census patch llm --ladder-run <run of step 1>`: the brain writes the migration branch,
   the flag-off check, and a confirm run of the patched code.

The target clone is a copy of the census clone (`MMD_CLONES` points to it), so the census branch
`mmd/move-to-nemotron` at a90319e in our clone of llm is not touched; the re-run's branch lives only in the copy, and
its patch is published here.

The whole terminal session is recorded with asciinema in real time, without cuts (`rerun.cast`). The video may play
it faster; it then says so on screen.

## Code changes since the census

Only logging: the proxy, the job summary, the ladder job records and the brain steps now keep the Token Factory
response id of each call (commit 6b13a6f), and ladder cells keep model seconds (commit 29ca28f). No rule, route,
fix list or model changed.

## Limits

- Token Factory: the manifest's per-job cap ($1); expected spend about $0.02 (the 2026-10-24 run of these steps cost
  $0.0034 for the ladder and $0.0115 for the brain). Stop at $0.10.
- Sandboxes: at most 8 runs for this re-run (`MMD_SANDBOX_MAX_RUNS` set to the logged count plus 8).
- One attempt. If infrastructure fails (503, lost job), the recording and the files are published as they are, and the
  failure is said where the run is shown. A second attempt, if any, is a new recording with its own note here.

## What is published here after the run

`rerun.cast` (the recording), `ladder-<run>.json` and `ladder-log.jsonl` lines of this run, `agent-steps.jsonl` lines of
this run, `patch/` (diff, plan, result, PR text), `calls.jsonl` (every model call: test, site, model, Token Factory
response id, status, seconds, $), and `RESULT.md` comparing the result with the census run of 2026-10-24
(`ladder-20261024T115937Z-6e8505`).

## Attempts

- Attempt 1, 2026-10-28T21:54:03Z: stopped at the first command because `run.sh` did not quote the interpreter path,
  which has a space in it. No model call and no sandbox run were made. Its terminal output is `attempt-1.txt`, with the
  local path replaced by `<local path>`. The branch and folder keep the planned date (2026-10-29); the runs happened on
  2026-10-28 UTC.
- Attempt 2: the same commands, with the quoting fixed in `run.sh`. Recorded as `rerun.cast`.
