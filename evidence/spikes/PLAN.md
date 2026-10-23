# Platform spikes: what we measure and how we judge it (written before any run)

This file is committed before the first spike call. Its git commit time is the proof that the pass/fail
rules below were fixed in advance. Results go to `evidence/spikes/RESULTS.md` and raw responses to
`evidence/spikes/raw/*.jsonl` (one JSON object per call: request params, raw response, latency, cost).

Models (IDs copied from Token Factory `models_info`, 2026-10-23):

| Rung | ID |
|---|---|
| Nano | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` |
| Lightning | `nvidia/Nemotron-3_5-Lightning` |
| Super | `nvidia/nemotron-3-super-120b-a12b` |
| Ultra | `nvidia/Nemotron-3-Ultra-550b-a55b` |

Every call goes through `model_moving_day/tf_client.py` (spend reserved in the shared ledger first).
Sampling unless stated: Nano `temperature 0.6, top_p 0.95`; the other three `temperature 1.0, top_p 0.95`
(NVIDIA model cards). No `seed`: Token Factory has none.

## S1. Reasoning switch (4 models x 4 forms x 2 repeats = 32 calls)

Prompt: "What is 17 x 23? Reply with the number only." `max_tokens 4096`.
Forms: `default` (no switch) · `effort_none` (`reasoning_effort: "none"`) · `ctk_top`
(top-level `chat_template_kwargs: {"enable_thinking": false}`) · `ctk_extra`
(`extra_body: {"chat_template_kwargs": {"enable_thinking": false}}`).

**Metric, per model x form:** the form "turns reasoning off" if in both repeats: HTTP 200, reasoning
tokens are 0 (or not reported), the message's `reasoning`/`reasoning_content` field is empty, `content`
is non-empty, and `finish_reason == "stop"`. Secondary: `content` contains `391`.
A form that returns HTTP 400 is recorded as "rejected", not as a failure of the model.

## S2. The 14.10 fact-extraction task, re-measured (tool calling, `write_fact`)

Same system prompt, user message and tool as `spike/tf_spike.py` (2026-10-14). Two facts expected:
moved to Austin, dentist is Dr. Lee.

- **S2a, reproduce the empty Nano.** Nano with the 14.10 parameters (`temperature 0`, `max_tokens 600`,
  reasoning default), 5 repeats. **Metric:** a run is "empty" if it has no tool call and empty `content`.
  Reproduced = at least 3 of 5 runs empty.
- **S2b, fix it.** Nano, `max_tokens 600`, `temperature 0.6, top_p 0.95`, reasoning switched off with the
  first form that passed S1 for Nano, 5 repeats. **Metric:** fixed = at least 4 of 5 runs make at least
  one parsable `write_fact` call and none ends with `finish_reason == "length"`.
- **S2c, recommended parameters, reasoning on.** Nano (`max_tokens 10000`), Lightning and Super
  (`max_tokens 12000`, `chat_template_kwargs.force_nonempty_content: true`), 5 repeats each.
  **Metric:** facts found out of 10 (2 per run; a fact counts if a `write_fact` call names Austin, resp.
  Dr. Lee / dentist). Pass bars from PLAYBOOK §5.2: Super >= 9/10, Lightning >= 8/10; Nano reported
  without a bar.

## S3. Structured output on the cheap rungs (Nano, Lightning; 3 methods x 20 calls each = 120 calls)

Task: extract `{"name": str, "age": int, "city": str}` from 20 fixed one-line sentences.
Reasoning off with the S1 form; `max_tokens 1024`.
Methods: `json_schema` (`response_format` strict schema) · `json_object` (`response_format`
`{"type": "json_object"}`, schema in the prompt) · `named_tool` (tool `record_person`,
`tool_choice` naming it).
**Metric per model x method:** valid = output parses as JSON, has exactly the three keys with the right
types. Pass = at least 19 of 20 valid (95 %). HTTP 400 on a method = "method rejected".
Secondary: all three values correct.

## S4. Tool calling on the brain models (Super, Ultra; 20 calls each)

Tools: `get_today()` with no parameters, `write_fact(subject, relation, object, source)`.
Prompt asks the model to call `get_today` first, then record the facts of the 14.10 message.
Super: `chat_template_kwargs: {"force_nonempty_content": true}`. Ultra (card says "must"):
`chat_template_kwargs: {"enable_thinking": true, "force_nonempty_content": true}`.
`max_tokens 8000`, `temperature 1.0, top_p 0.95`, `tool_choice: "auto"`.
**Metric:** a call is parsed if it returns at least one tool call and every tool call's arguments parse
as JSON (after the client's `"" -> "{}"` rule). Pass = at least 19 of 20 per model.
Also counted: how many raw tool calls arrived with `arguments: ""` (observed before the client
normalises them).

## S5. Where reasoning text arrives

From every raw response above: per model, count responses whose reasoning text sits in
`message.reasoning`, in `message.reasoning_content`, in both, or nowhere, and whether
`usage.completion_tokens_details.reasoning_tokens` is reported.
**Metric:** pass = the recorded responses of all 4 models, replayed through `tf_client._parse` in a
unit test, give either non-empty `text` or at least one tool call, and `truncated` exactly when
`finish_reason == "length"`.

## S6. Limits, headers and price (scaled down from the PLAYBOOK's 10 minutes)

- 2 minutes of load on Super with 8 concurrent small calls (reasoning off, `max_tokens 64`);
  read every `x-ratelimit-*` header and count HTTP 429s.
- One Lightning call with `n=3` vs the same call with `n=1`: does `prompt_tokens` count the input once?
- $ per call from `usage` x catalog prices for every call above.
**Metric:** pass = zero 429s at 8 concurrent calls on Super (the census concurrency); report the
measured calls per minute and the header values. `n=3` question answered yes/no from `usage`.

## Stop rules
- Any ledger total for tag `c1` above $1.00 during these spikes: stop and report.
- Any HTTP 402: stop and report.
