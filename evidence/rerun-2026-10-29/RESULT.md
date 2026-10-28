# Recorded re-run of llm :1494: result (2026-10-28 UTC, not part of the census)

Plan: `PLAN.md` (pushed before the run). Attempt 2 ran 21:54:41Z → 21:56:21Z UTC, 1 min 40 s of commands, under
manifest amendment 8 (sha256 `32b356fe…`, commit 7b57d18, pushed 21:53:45Z). Recording: `rerun.cast` (asciinema,
real time, no cuts; play with `asciinema play rerun.cast`). Attempt 1 stopped on a quoting error before any call
(`attempt-1.txt`).

## Same outcome as the census

| Step | Census, 2026-10-24 (`ladder-20261024T115937Z-6e8505`) | Re-run, 2026-10-28 (`ladder-20261028T215443Z-765b1e`) |
|---|---|---|
| Nano, as requested | 0/5, `'\nYES' == 'YES'` | 0/5, same assertion |
| Brain diagnosis (Nemotron Super) | reasoning_off | reasoning_off |
| Nano, reasoning off | 5/5, $0.0297 per 1,000 calls | 5/5, $0.0297 per 1,000 calls |
| Lightning (next bigger, measured) | 5/5, $0.0378 | 5/5, $0.0378 |
| Decision | Nano + reasoning_off | Nano + reasoning_off |
| Branch written by the brain (Nemotron Super) | 13 steps, 51.1 s, $0.0115 | 8 steps, 21.2 s, $0.0065 |
| Confirm run of the patched code | 5/5 | 5/5 (`ladder-20261028T215612Z-c7390e`) |

The Nano "as requested" cell cost $0.277 per 1,000 calls in the re-run against $0.136 in the census: with reasoning on,
Nano's answers vary in length. The patch hunk is the same call wrapped in `nemotron_move.call(...)`; the brain wrote it
on one line this time, and the helper file is generated from the same template.

## Speed per call (new: measured in this re-run only)

| Cell | Seconds per model call |
|---|---|
| Nano, reasoning on (as requested) | 10.54 |
| Nano, reasoning off | 0.68 |
| Lightning | 0.47 |

The census ladder files did not keep seconds; amendment 8 added them, so there is no census figure to compare.

## Every model call, with its Token Factory response id

`calls.jsonl`: 68 calls, 68 with a Token Factory response id (`chatcmpl-…`): 44 from the ladder jobs, 15 from the
confirm job, 9 brain steps (1 diagnosis, 8 patch steps). Sum of their model cost: $0.0124. The project's spend ledger
moved by the same $0.0124. The Sandboxes `cost` field of the 4 sandbox runs (`sandbox-runs.jsonl`) is diagnostic and
not billed (checked on 2026-10-24).

## Files

`ladder-*.json` (both runs), `ladder-log.jsonl` (their lines), `agent-steps-ladder.jsonl`, `agent-steps-patch.jsonl`,
`patch/` (`patch.diff`, `plan.json`, `result.json`, `PR.md`), `calls.jsonl`, `sandbox-runs.jsonl`, `rerun.cast`.
The census files under `evidence/census/` are unchanged; this run's branch commit (da71ca6) exists only in the
re-run's copy of the llm clone.
