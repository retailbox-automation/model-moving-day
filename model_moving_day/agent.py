"""The agent's brain: a Nemotron model on Token Factory that diagnoses red ladder cells and writes the migration
patch. Super for repos under ~200K tokens of source, Ultra above (Super has a 262K context on Token Factory).

Every model call is logged as one JSON line (step, model, latency, prompt/completion/reasoning tokens, $).
"""
import json, os, py_compile, time
from pathlib import Path

from model_moving_day import freeze, ladder, tf_client as tf

SUPER = "nvidia/nemotron-3-super-120b-a12b"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
SAMPLING = {"temperature": 1.0, "top_p": 0.95}
CTK = {SUPER: {"force_nonempty_content": True}, ULTRA: {"enable_thinking": True, "force_nonempty_content": True}}
FIXES = {  # fix kind -> (route params for the proxy, call-log diagnoses that justify it)
    "reasoning_off": ({"reasoning": "off"}, {"empty_content_length", "answer_in_reasoning_field", "empty_content",
                                             "leading_whitespace"}),
    "raise_max_tokens": ({"min_max_tokens": 8000}, {"empty_content_length"}),
}


def source_tokens(repo, skip=("tests", "test", "docs", "examples", ".git", ".venv")):
    chars = sum(p.stat().st_size for p in Path(repo).rglob("*.py") if not set(p.relative_to(repo).parts) & set(skip))
    return chars // 4


def pick_brain(repo):
    return SUPER if source_tokens(repo) < 200_000 else ULTRA


class Brain:
    def __init__(self, client, model, log_path, log=print):
        self.client, self.model, self.log_path, self.log = client, model, Path(log_path), log

    def ask(self, step, messages, tools=None, tool_choice=None, max_tokens=8000):
        params = {"max_tokens": max_tokens, **SAMPLING, "chat_template_kwargs": dict(CTK[self.model])}
        if tools:
            params.update(tools=tools, tool_choice=tool_choice or "auto")
        t0 = time.time()
        try:
            r = self.client.chat(self.model, messages, **params)
        except tf.TFError as e:  # logged, then raised: the caller decides; nothing is swallowed
            self._log({"step": step, "model": self.model, "error": str(e)[:300], "seconds": round(time.time() - t0, 3)})
            raise
        self._log({"step": step, "model": r.model, "seconds": r.seconds, "prompt_tokens": r.usage.get("prompt_tokens"),
                   "completion_tokens": r.usage.get("completion_tokens"), "reasoning_tokens": r.reasoning_tokens,
                   "cost": r.cost, "finish": r.finish_reason, "tool_calls": [tc["function"]["name"] for tc in r.tool_calls]})
        return r

    def _log(self, rec):
        rec = {"ts": round(time.time(), 3), **rec}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        self.log(f"[agent] {rec.get('step')}: {rec.get('model')} {rec.get('seconds')} s ${rec.get('cost', 0):.5f}")

    # --- diagnosis of a red cell ---------------------------------------------------------------------------
    DIAG_TOOL = {"type": "function", "function": {
        "name": "propose_fix", "description": "Propose one request-parameter fix for this call site, or none.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["reasoning_off", "raise_max_tokens", "none"]},
            "cause": {"type": "string", "description": "the cause, in one sentence, citing the evidence"}},
            "required": ["kind", "cause"]}}}

    def diagnose(self, evidence):
        """evidence: dict with site, rung, passes, runs, failing tests + messages, call-log stats. Returns ladder.Fix or None."""
        msgs = [{"role": "system", "content": (
            "You move an application's LLM call sites to smaller NVIDIA Nemotron models on Token Factory. A call "
            "site failed the application's own tests on one model. Read the evidence and call propose_fix once. "
            "Facts about Nemotron on Token Factory: reasoning is on by default and its tokens share max_tokens; when "
            "reasoning uses the whole budget the reply has empty content and finish_reason 'length'; with reasoning "
            "on, content can start with a newline; chat_template_kwargs.enable_thinking=false switches reasoning off "
            "on every size. Choose 'none' when the failures are about answer quality rather than the request.")},
            {"role": "user", "content": json.dumps(evidence)[:12000]}]
        r = self.ask(f"diagnose {evidence.get('site')} @ {evidence.get('rung')}", msgs, tools=[self.DIAG_TOOL],
                     tool_choice={"type": "function", "function": {"name": "propose_fix"}}, max_tokens=4000)
        if not r.tool_calls:
            return None
        args = tf.tool_args(r.tool_calls[0])
        kind = args.get("kind")
        if kind not in FIXES:
            return None
        params, justified_by = FIXES[kind]
        seen = set((evidence.get("diagnoses") or {}).keys())
        if not seen & justified_by:  # the model's proposal must be backed by what the call log recorded
            self._log({"step": "diagnose rejected", "model": self.model, "kind": kind, "seen": sorted(seen)})
            return None
        return ladder.Fix(kind, params, args.get("cause", "")[:400], by=self.model)

    # --- patch writing ---------------------------------------------------------------------------------------
    PATCH_TOOLS = [
        {"type": "function", "function": {"name": "read_file", "description": "Read numbered lines of a source file.",
                                          "parameters": {"type": "object", "properties": {
                                              "path": {"type": "string"}, "start": {"type": "integer"},
                                              "end": {"type": "integer"}}, "required": ["path", "start", "end"]}}},
        {"type": "function", "function": {"name": "replace", "description": (
            "Replace one exact, unique snippet of a source file with new text. Tests and fixtures are frozen and "
            "cannot be edited."), "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                "required": ["path", "old", "new"]}}},
        {"type": "function", "function": {"name": "finish", "description": "Stop when every planned site is wrapped.",
                                          "parameters": {"type": "object", "properties": {
                                              "summary": {"type": "string"}}, "required": ["summary"]}}},
    ]

    def write_patch(self, repo, frozen, plan, helper_import, feedback="", max_steps=24):
        """Let the brain wrap each planned call site with the helper; tests stay frozen (hash-checked every step)."""
        repo = Path(repo)
        sites = "\n".join(f"- site '{s['slug']}' at {s['site']} ({'async' if s['is_async'] else 'sync'}) -> "
                          f"{s['model']} (fallback {s['fallback']})" for s in plan["sites"])
        msgs = [{"role": "system", "content": (
            "You edit a Python repository so that each planned LLM call site goes through the Nemotron move helper. "
            f"The helper is importable as `{helper_import}` and provides call(site, create, *args, **kwargs) for "
            "sync calls and `await acall(site, create, *args, **kwargs)` for async calls; it leaves the call unchanged "
            "unless NEMOTRON_MOVE=1. Wrap exactly the call on the given line: e.g. `response = func(*args, **kwargs)` "
            f"becomes `response = {helper_import.split('.')[-1]}.call(\"<site>\", func, *args, **kwargs)`; for a method call "
            "`client.chat.completions.create(model=m, ...)` pass the bound method as `create`. Add the import at the "
            "top of each edited module. Change nothing else. Use read_file first, then replace, then finish.")},
            {"role": "user", "content": f"Planned sites:\n{sites}\n{feedback}"}]
        edits = []
        for step in range(max_steps):
            with freeze.guard(repo, frozen):
                r = self.ask(f"patch step {step}", msgs, tools=self.PATCH_TOOLS)
                msgs.append({"role": "assistant", "content": r.content or "", "tool_calls": r.tool_calls})
                if not r.tool_calls:
                    msgs.append({"role": "user", "content": "Use the tools: read_file, replace, then finish."})
                    continue
                done = False
                for tc in r.tool_calls:
                    name, args = tc["function"]["name"], tf.tool_args(tc)
                    result = self._tool(repo, frozen, name, args, edits)
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})
                    done = done or name == "finish"
                if done:
                    return edits
        return edits

    def _tool(self, repo, frozen, name, args, edits):
        path = (repo / args.get("path", "")).resolve()
        if name == "finish":
            return "ok"
        if repo.resolve() not in path.parents or not path.is_file():
            return f"error: {args.get('path')} is not a file in the repository"
        rel = path.relative_to(repo.resolve()).as_posix()
        if name == "read_file":
            lines = path.read_text().splitlines()
            s, e = max(1, int(args.get("start", 1))), min(len(lines), int(args.get("end", 1)), int(args.get("start", 1)) + 200)
            return "\n".join(f"{i}: {lines[i - 1]}" for i in range(s, e + 1))
        if name == "replace":
            if rel in frozen.hashes:
                return f"error: {rel} is a frozen behaviour check and cannot be edited"
            text, old = path.read_text(), args.get("old", "")
            if not old or text.count(old) != 1:
                return f"error: the snippet occurs {text.count(old) if old else 0} times; it must occur exactly once"
            new_text = text.replace(old, args.get("new", ""))
            path.write_text(new_text)
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as e:
                path.write_text(text)
                return f"error: the edit does not compile and was reverted: {str(e)[:300]}"
            edits.append({"path": rel, "old": old, "new": args.get("new", "")})
            return "ok"
        return f"error: unknown tool {name}"
