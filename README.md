# Model Moving Day

Work in progress for the Nebius × NVIDIA Global AI Hackathon (track: Coding and Agentic Engineering).

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
