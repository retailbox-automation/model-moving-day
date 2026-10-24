# Probe: what Token Factory answers for a model it does not serve (written before the probe, 2026-10-24)

Why: the migration helper falls back to a measured fallback model only on the status Token Factory returns for a
retired or unknown model ID (RETIRED_STATUSES). Until now that status was assumed (404), not measured.

What: two POST /v1/chat/completions calls with max_tokens 1, one message "ping":
1. `nvidia/Llama-3_1-Nemotron-Ultra-253B-v1`, retired from Serverless on 2026-08-31 (Token Factory deprecation notice);
2. `nvidia/mmd-no-such-model-2026`, an ID that never existed.

Record per call: HTTP status, the first 300 characters of the error body, time. No retries. Written by
`spikes/unknown_model_probe.py` to `evidence/platform/unknown-model.json`, with `statuses` = the set of statuses seen.

Rule: the helper's RETIRED_STATUSES becomes exactly that set. If a call returns 200 (the model is still served),
it is recorded and that ID is not used to set the rule. Cost: none expected (error responses are not billed); at
most 2 x 1 output token if a model answers.
