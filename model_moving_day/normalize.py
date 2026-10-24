"""Rewrite an OpenAI-style chat request into one Token Factory accepts for a given Nemotron rung.

Every change is recorded (kind, key, from, to, why) so the pull request can list it. Measured on 2026-10-23
(evidence/spikes): `chat_template_kwargs.enable_thinking=false` switches reasoning off on all four rungs, while
`reasoning_effort: "none"` on Nano leaves `content` empty and puts the answer in `reasoning`.
"""
import copy

ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
# ChatCompletionRequest properties in the Token Factory OpenAPI (v20261020), plus chat_template_kwargs, which the
# server forwards to the chat template (spike S1).
ALLOWED = {"model", "store", "messages", "max_tokens", "max_completion_tokens", "temperature", "top_p", "tools",
           "tool_choice", "reasoning_effort", "n", "stream", "stream_options", "stop", "presence_penalty",
           "frequency_penalty", "logit_bias", "logprobs", "top_logprobs", "user", "response_format", "service_tier",
           "chat_template_kwargs"}


def _change(changes, kind, key, before, after, why):
    changes.append({"kind": kind, "key": key, "from": before, "to": after, "why": why})


def normalize(request, model, reasoning="keep", min_max_tokens=None):
    """Return (body, changes). reasoning: "keep" (leave the app's choice), "off" or "on"."""
    body, changes = copy.deepcopy(request), []
    if body.get("model") != model:
        _change(changes, "model", "model", body.get("model"), model, "moved to a Nemotron model on Token Factory")
        body["model"] = model

    extra = body.pop("extra_body", None)
    if isinstance(extra, dict):  # the OpenAI SDK merges extra_body into the top level of the JSON body
        for k, v in extra.items():
            body.setdefault(k, v)

    for key in sorted(set(body) - ALLOWED):
        _change(changes, "dropped", key, body.pop(key), None, "not in the Token Factory chat schema")

    msgs = body.get("messages") or []
    if msgs and msgs[-1].get("role") == "assistant":
        _change(changes, "dropped", "prefill", msgs[-1].get("content"), None,
                "assistant prefill as the last message is not supported by the Nemotron chat template")
        body["messages"] = msgs[:-1]

    ctk = dict(body.get("chat_template_kwargs") or {})
    effort = body.get("reasoning_effort")
    if effort in ("none", "minimal"):
        body.pop("reasoning_effort")
        ctk["enable_thinking"] = False
        _change(changes, "reasoning", "reasoning_effort", effort, "chat_template_kwargs.enable_thinking=false",
                "reasoning_effort none leaves content empty on Nano; the template switch works on every rung")
    if reasoning == "off":
        body.pop("reasoning_effort", None)
        if ctk.get("enable_thinking") is not False:
            ctk["enable_thinking"] = False
            _change(changes, "reasoning", "enable_thinking", None, False, "reasoning off for this call site")
    elif reasoning == "on" and ctk.get("enable_thinking") is False:
        ctk.pop("enable_thinking")
        _change(changes, "reasoning", "enable_thinking", False, None, "reasoning on for this call site")
    if model == ULTRA and body.get("tools") and (ctk.get("enable_thinking") is not True or not ctk.get("force_nonempty_content")):
        before = dict(ctk)
        ctk.update(enable_thinking=True, force_nonempty_content=True)
        if reasoning == "off":  # the asked switch did not survive: say so instead of listing it as applied
            changes[:] = [c for c in changes if not (c["kind"] == "reasoning" and c["key"] == "enable_thinking")]
            _change(changes, "refused", "enable_thinking", False, True, "reasoning off is not possible on Ultra with tools")
        _change(changes, "reasoning", "chat_template_kwargs", before, dict(ctk),
                "Ultra with tools must run with enable_thinking and force_nonempty_content (NVIDIA model card)")
    if ctk:
        body["chat_template_kwargs"] = ctk

    if min_max_tokens:
        key = "max_tokens" if "max_tokens" in body or "max_completion_tokens" not in body else "max_completion_tokens"
        cur = body.get(key)
        if cur is None or cur < min_max_tokens:  # an absent budget is set too: otherwise the fix changes nothing
            body[key] = min_max_tokens
            _change(changes, "raised", key, cur, min_max_tokens, "reasoning tokens share the max_tokens budget")
    return body, changes
