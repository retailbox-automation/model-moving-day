import json, subprocess, sys, textwrap, threading, urllib.request

import pytest

from model_moving_day import proxy
from model_moving_day import tf_client as tf

REAL_URLOPEN = urllib.request.urlopen  # the fixture patches the module-level one for tf_client
LIGHT = "nvidia/Nemotron-3_5-Lightning"
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


class FakeUpstream:
    def __init__(self):
        self.bodies, self.message, self.urls = [], None, []

    def __call__(self, req, timeout=None):
        import io
        body = json.loads(req.data)
        self.bodies.append(body)
        self.urls.append(req.full_url)
        if req.full_url.endswith("/embeddings"):
            return io.BytesIO(json.dumps({"data": [{"embedding": [0.0, 1.0]}], "model": body["model"],
                                          "usage": {"prompt_tokens": 7, "total_tokens": 7}}).encode())
        msg = self.message or {"role": "assistant", "content": f"served by {body['model']}"}
        return io.BytesIO(json.dumps({"id": "x", "object": "chat.completion", "model": body["model"],
                                      "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode())


@pytest.fixture
def server(tmp_path, monkeypatch):
    up = FakeUpstream()
    monkeypatch.setattr(tf.urllib.request, "urlopen", up)
    routes = {"default": {"mode": "forward", "model": LIGHT, "reasoning": "off"},
              "sites": {"app/x.py:10": {"mode": "broken"}, "app/y.py:5": {"mode": "forward", "model": NANO},
                        "app/as_req.py:1": {"mode": "as_requested"}, "app/pass.py:1": {"mode": "passthrough"},
                        "app/wrong.py:1": {"mode": "wrong"}},
              "spans": {"app/y.py": [[5, 7, "app/y.py:5"]]}}
    ledger = tf.Ledger(tmp_path / "led.jsonl", caps={"total": 0.05, "c1": 0.05})
    srv = proxy.make_server(0, routes, tmp_path / "calls.jsonl", tf.Client("c1", ledger=ledger, key="k", log=lambda m: None))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", up, tmp_path, ledger
    srv.shutdown()


def post(base, body, site=None, test=None, path="/v1/chat/completions"):
    headers = {"Content-Type": "application/json"}
    if site:
        headers["x-mmd-site"] = site
    if test:
        headers["x-mmd-test"] = test
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    with REAL_URLOPEN(req, timeout=10) as r:
        return r.status, r.read().decode()


def calls(tmp):
    return [json.loads(x) for x in (tmp / "calls.jsonl").read_text().splitlines()]


def test_default_route_normalizes_and_forwards(server):
    base, up, tmp, _ = server
    status, text = post(base, {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "hi"}], "seed": 3},
                        site="app/z.py:1", test="tests/test_a.py::test_a (call)")
    assert status == 200 and json.loads(text)["choices"][0]["message"]["content"] == f"served by {LIGHT}"
    assert up.bodies[0]["model"] == LIGHT and "seed" not in up.bodies[0]
    assert up.bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}
    rec = calls(tmp)[0]
    assert rec["site"] == "app/z.py:1" and rec["test"] == "tests/test_a.py::test_a" and rec["model"] == LIGHT
    assert rec["requested_model"] == "gpt-4.1-mini" and {c["key"] for c in rec["changes"]} >= {"seed", "model"}
    assert rec["usage"]["prompt_tokens"] == 10 and rec["cost"] > 0 and rec["content_lead_ws"] is False


def test_chain_header_is_resolved_to_an_ast_site_before_routing(server):
    base, up, tmp, _ = server
    post_chain = {"x-mmd-site": "lib/funnel.py:20", "x-mmd-chain": "lib/funnel.py:20|app/y.py:6"}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps({"model": "m", "messages": []}).encode(),
                                 headers={"Content-Type": "application/json", **post_chain}, method="POST")
    REAL_URLOPEN(req, timeout=10).read()
    assert up.bodies[-1]["model"] == NANO  # app/y.py:6 lies inside the span of site app/y.py:5
    assert calls(tmp)[-1]["site"] == "app/y.py:5" and calls(tmp)[-1]["chain"] == ["lib/funnel.py:20", "app/y.py:6"]


def test_as_requested_mode_serves_the_model_the_patched_app_asks_for(server):
    base, up, tmp, _ = server
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps({"model": NANO, "messages": []}).encode(),
                                 headers={"Content-Type": "application/json", "x-mmd-site": "app/as_req.py:1"}, method="POST")
    REAL_URLOPEN(req, timeout=10).read()
    assert up.bodies[-1]["model"] == NANO and calls(tmp)[-1]["mode"] == "as_requested"
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps({"model": "gpt-4.1-mini", "messages": []}).encode(),
                                 headers={"Content-Type": "application/json", "x-mmd-site": "app/as_req.py:1"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        REAL_URLOPEN(req, timeout=10)
    assert e.value.code == 400 and "not a Token Factory model" in calls(tmp)[-1]["error"]


def test_site_route_overrides_the_model(server):
    base, up, *_ = server
    post(base, {"model": "gpt-4.1-mini", "messages": []}, site="app/y.py:5")
    assert up.bodies[-1]["model"] == NANO


def test_broken_mode_returns_an_empty_answer_without_calling_upstream(server):
    base, up, tmp, _ = server
    status, text = post(base, {"model": "gpt-4.1-mini", "messages": []}, site="app/x.py:10")
    body = json.loads(text)
    assert status == 200 and body["choices"][0]["message"]["content"] == "" and up.bodies == []
    assert calls(tmp)[0]["mode"] == "broken" and calls(tmp)[0]["cost"] == 0


def test_streaming_requests_get_server_sent_events(server):
    base, *_ = server
    status, text = post(base, {"model": "m", "messages": [], "stream": True, "stream_options": {"include_usage": True}})
    events = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"]) == f"served by {LIGHT}"
    assert any(c.get("usage") for c in chunks)


def test_budget_exhaustion_is_returned_as_an_error_and_logged(server, tmp_path):
    base, up, tmp, ledger = server
    ledger.reserve("c1", 0.0499, "fill")
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, {"model": "m", "messages": [], "max_tokens": 16000})
    assert e.value.code == 402 and calls(tmp)[-1]["error"].startswith("BudgetExceeded")


def test_models_listing_is_passed_through_for_the_deprecation_check(server, monkeypatch):
    import io
    base, up, tmp, _ = server
    monkeypatch.setattr(tf.urllib.request, "urlopen", lambda req, timeout=None: io.BytesIO(b'{"data": [{"id": "a"}]}'))
    with REAL_URLOPEN(base + "/v1/models", timeout=10) as r:
        assert json.loads(r.read())["data"] == [{"id": "a"}]
    assert calls(tmp)[-1] == {**calls(tmp)[-1], "method": "GET", "path": "/v1/models", "status": 200}


def test_unsupported_endpoint_is_logged(server):
    base, up, tmp, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, {"model": "m", "input": "x"}, path="/v1/embeddings")
    assert e.value.code == 501 and calls(tmp)[-1]["path"] == "/v1/embeddings"


def test_tracer_tags_requests_with_the_innermost_repo_frame(tmp_path):
    """Run a tiny 'repo' in a subprocess with the tracer on PYTHONPATH and capture the headers httpx would send."""
    import httpx  # noqa: F401  (installed with contree-sdk)
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "llm.py").write_text(textwrap.dedent("""
        import httpx
        def ask(client):
            return client.send(client.build_request("POST", "http://127.0.0.1:9/v1/chat/completions"))
    """))
    (repo / "tests").mkdir()
    (repo / "tests" / "run.py").write_text(textwrap.dedent("""
        import json, httpx, sys
        sys.path.insert(0, sys.argv[1])
        from pkg.llm import ask
        seen = {}
        class T(httpx.BaseTransport):
            def handle_request(self, request):
                seen.update({k: v for k, v in request.headers.items() if k.startswith("x-mmd-")})
                return httpx.Response(200, json={})
        ask(httpx.Client(transport=T()))
        print(json.dumps(seen))
    """))
    env = {"PYTHONPATH": str(proxy.TRACER_DIR), "MMD_REPO_ROOT": str(repo), "PYTEST_CURRENT_TEST": "tests/t.py::t (call)",
           "PATH": "/usr/bin:/bin", "MMD_RUN": "4"}
    out = subprocess.run([sys.executable, str(repo / "tests" / "run.py"), str(repo)], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert seen["x-mmd-site"] == "pkg/llm.py:4" and seen["x-mmd-test"] == "tests/t.py::t (call)" and seen["x-mmd-run"] == "4"
    assert seen["x-mmd-chain"] == "pkg/llm.py:4"  # repo frames only, innermost first; the test file is excluded


def test_tracer_tags_requests_made_by_the_real_openai_sdk(server):
    """openai 3.x sends through httpx2, not httpx; the tracer must see both."""
    pytest.importorskip("openai")
    base, up, tmp, _ = server
    repo = tmp / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "ask.py").write_text(textwrap.dedent("""
        from openai import OpenAI
        def ask():
            return OpenAI().chat.completions.create(model="gpt-4.1-mini", messages=[{"role": "user", "content": "hi"}])
    """))
    script = "import sys; sys.path.insert(0, sys.argv[1]); from pkg.ask import ask; print(ask().choices[0].message.content)"
    env = {"PYTHONPATH": str(proxy.TRACER_DIR), "MMD_REPO_ROOT": str(repo), "PYTEST_CURRENT_TEST": "tests/t.py::t (call)",
           "OPENAI_BASE_URL": base + "/v1", "OPENAI_API_KEY": "dummy", "PATH": "/usr/bin:/bin", "MMD_RUN": "1"}
    out = subprocess.run([sys.executable, "-c", script, str(repo)], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    rec = calls(tmp)[-1]
    assert rec["site"] == "pkg/ask.py:4" and rec["test"] == "tests/t.py::t" and rec["run"] == 1


def test_any_unexpected_proxy_error_is_logged_with_status_500_and_answered(server):
    """Before: an exception inside do_POST dropped the connection and left no line in calls.jsonl."""
    base, up, tmp, _ = server
    req = urllib.request.Request(base + "/v1/chat/completions", data=b"{not json", method="POST",
                                 headers={"Content-Type": "application/json", "x-mmd-site": "app/z.py:1", "x-mmd-run": "2"})
    with pytest.raises(urllib.error.HTTPError) as e:
        REAL_URLOPEN(req, timeout=10)
    rec = calls(tmp)[-1]
    assert e.value.code == 500 and rec["status"] == 500 and rec["proxy_error"].startswith("JSONDecodeError")
    assert rec["site"] == "app/z.py:1" and rec["run"] == 2 and up.bodies == []


def test_null_max_tokens_is_served_and_logged(server):
    base, up, tmp, _ = server
    status, _ = post(base, {"model": "m", "messages": [{"role": "user", "content": "x"}], "max_tokens": None})
    assert status == 200 and calls(tmp)[-1]["status"] == 200 and len(up.bodies) == 1


def test_the_app_gets_the_servers_raw_answer_and_empty_arguments_are_only_logged(server):
    base, up, tmp, _ = server
    up.message = {"role": "assistant", "content": None,
                  "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": ""}}]}
    _, text = post(base, {"model": "m", "messages": [], "tools": [{"type": "function", "function": {"name": "f"}}]})
    assert json.loads(text)["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == ""
    assert calls(tmp)[-1]["repaired_empty_args"] == 1


def test_passthrough_sends_the_body_as_the_app_built_it(server):
    base, up, tmp, _ = server
    body = {"model": NANO, "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "{"}],
            "seed": 3, "stream": True, "stream_options": {"include_usage": True}}
    post(base, body, site="app/pass.py:1")
    sent = up.bodies[-1]
    assert sent == {"model": NANO, "messages": body["messages"], "seed": 3}  # prefill and seed kept, stream removed
    assert calls(tmp)[-1]["mode"] == "passthrough" and calls(tmp)[-1]["changes"] == []


def test_run_index_header_is_logged(server):
    base, up, tmp, _ = server
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps({"model": "m", "messages": []}).encode(),
                                 headers={"Content-Type": "application/json", "x-mmd-run": "3"}, method="POST")
    REAL_URLOPEN(req, timeout=10).read()
    assert calls(tmp)[-1]["run"] == 3


def test_embeddings_are_refused_when_the_routes_name_no_embedding_model(server):
    base, up, tmp, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, {"model": "text-embedding-3-small", "input": "x"}, path="/v1/embeddings")
    assert e.value.code == 501 and up.bodies == []


def test_embeddings_served_when_the_routes_name_a_model(tmp_path, monkeypatch):
    up = FakeUpstream()
    monkeypatch.setattr(tf.urllib.request, "urlopen", up)
    ledger = tf.Ledger(tmp_path / "led.jsonl", caps={"total": 0.05, "c1": 0.05})
    routes = {"default": {"mode": "broken"}, "embedding_model": tf.EMBEDDING_MODEL}
    srv = proxy.make_server(0, routes, tmp_path / "calls.jsonl", tf.Client("c1", ledger=ledger, key="k", log=lambda m: None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, text = post(f"http://127.0.0.1:{srv.server_address[1]}",
                            {"model": "text-embedding-3-small", "input": ["a"], "encoding_format": "float"}, path="/v1/embeddings")
    finally:
        srv.shutdown()
    assert status == 200 and json.loads(text)["data"][0]["embedding"] == [0.0, 1.0]
    assert up.bodies[-1] == {"model": tf.EMBEDDING_MODEL, "input": ["a"], "encoding_format": "float"}
    rec = calls(tmp_path)[-1]
    assert rec["mode"] == "embedding" and rec["model"] == tf.EMBEDDING_MODEL and rec["cost"] > 0


VALIDATOR_TOOL = {"type": "function", "function": {"name": "Validator", "parameters": {
    "type": "object", "properties": {"is_valid": {"type": "boolean"}, "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                     "fixed_value": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
    "required": ["is_valid"]}}}
USER_TOOL = {"type": "function", "function": {"name": "User", "parameters": {
    "$defs": {"Role": {"enum": ["admin", "user"], "type": "string"}},
    "type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer", "maximum": 120},
                                     "role": {"$ref": "#/$defs/Role"}, "tags": {"type": "array", "items": {"type": "string"}}},
    "required": ["name", "age"]}}}


def test_wrong_mode_answers_with_a_wellformed_wrong_tool_call_without_calling_a_model(server):
    base, up, tmp, _ = server
    _, text = post(base, {"model": "m", "messages": [], "tools": [USER_TOOL, VALIDATOR_TOOL],
                          "tool_choice": {"type": "function", "function": {"name": "Validator"}}}, site="app/wrong.py:1")
    tc = json.loads(text)["choices"][0]["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "Validator" and json.loads(tc["function"]["arguments"])["is_valid"] is True
    _, text = post(base, {"model": "m", "messages": [], "tools": [USER_TOOL]}, site="app/wrong.py:1")
    args = json.loads(json.loads(text)["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
    assert args == {"name": "mmd-wrong", "age": 120, "role": "user", "tags": ["mmd-wrong"]}
    assert up.bodies == [] and calls(tmp)[-1]["mode"] == "wrong" and calls(tmp)[-1]["tool_calls"] == 1


def test_wrong_mode_json_schema_and_plain_text(server):
    base, up, tmp, _ = server
    schema = {"type": "object", "properties": {"answer": {"type": "number"}}}
    _, text = post(base, {"model": "m", "messages": [], "response_format": {"type": "json_schema", "json_schema": {
        "name": "A", "schema": schema}}}, site="app/wrong.py:1")
    assert json.loads(json.loads(text)["choices"][0]["message"]["content"]) == {"answer": 7919.5}
    _, text = post(base, {"model": "m", "messages": [], "stream": True}, site="app/wrong.py:1")
    assert proxy.WRONG_TEXT in text and up.bodies == []


def test_proxy_reads_its_key_from_stdin_not_from_the_environment(tmp_path):
    import os, socket, time as _t
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({"default": {"mode": "broken"}}))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k != "NEBIUS_API_KEY"}
    p = subprocess.Popen([sys.executable, "-m", "model_moving_day.proxy", "--port", str(port), "--routes", str(routes),
                          "--log", str(tmp_path / "calls.jsonl"), "--ledger", str(tmp_path / "led.jsonl"), "--cap", "0.01",
                          "--key-stdin"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                         cwd=str(proxy.TRACER_DIR.parents[1]))
    try:
        p.stdin.write(b"secret-from-stdin\n")
        p.stdin.close()
        for _ in range(100):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            _t.sleep(0.05)
        status, _ = post(f"http://127.0.0.1:{port}", {"model": "m", "messages": []})
        assert status == 200 and p.poll() is None
    finally:
        p.terminate()
        p.wait(timeout=5)


def test_wrong_mode_ends_an_unforced_tool_chain_with_wrong_text_but_keeps_forced_calls(server):
    """llm, 2026-10-24: answering every turn with a tool call made its tool chain loop for 1112 s."""
    base, up, tmp, _ = server
    msgs = [{"role": "user", "content": "x"}, {"role": "assistant", "content": None, "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "User", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "0.36"}]
    _, text = post(base, {"model": "m", "messages": msgs, "tools": [USER_TOOL]}, site="app/wrong.py:1")
    msg = json.loads(text)["choices"][0]["message"]
    assert msg["content"] == proxy.WRONG_TEXT and not msg.get("tool_calls")
    _, text = post(base, {"model": "m", "messages": msgs, "tools": [USER_TOOL],
                          "tool_choice": {"type": "function", "function": {"name": "User"}}}, site="app/wrong.py:1")
    assert json.loads(text)["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "User"


def test_wrong_answers_meet_length_pattern_and_exclusive_bounds_or_say_they_cannot():
    """N13: a 'wrong' answer that breaks the schema fails like the empty answer and proves nothing about values."""
    bad = []
    v = proxy._instance({"type": "object", "properties": {
        "code": {"type": "string", "pattern": "^[0-9]+$"}, "short": {"type": "string", "maxLength": 5},
        "long": {"type": "string", "minLength": 12}, "pct": {"type": "number", "exclusiveMaximum": 100},
        "n": {"type": "integer", "exclusiveMinimum": 7919}, "none": {"type": "array", "maxItems": 0, "items": {"type": "string"}}}},
        {}, 0, bad)
    assert v == {"code": "0", "short": "mmd-w", "long": "mmd-wrongxxx", "pct": 99.5, "n": 7920, "none": []} and bad == []
    bad = []
    proxy._instance({"type": "string", "pattern": "^ZZ-[0-9]{9}$"}, {}, 0, bad)
    assert bad and "pattern" in bad[0]


def test_wrong_mode_logs_whether_the_answer_was_well_formed(server):
    base, up, tmp, _ = server
    tool = {"type": "function", "function": {"name": "Id", "parameters": {"type": "object", "properties": {
        "id": {"type": "string", "pattern": "^ZZ-[0-9]{9}$"}}}}}
    post(base, {"model": "m", "messages": [], "tools": [tool]}, site="app/wrong.py:1")
    assert calls(tmp)[-1]["wrong_wellformed"] is False and calls(tmp)[-1]["wrong_unmet"]
    post(base, {"model": "m", "messages": [], "tools": [USER_TOOL]}, site="app/wrong.py:1")
    assert calls(tmp)[-1]["wrong_wellformed"] is True


def test_moved_path_is_served_as_sent_and_the_apps_own_endpoint_is_the_old_model_route(tmp_path, monkeypatch):
    up = FakeUpstream()
    monkeypatch.setattr(tf.urllib.request, "urlopen", up)
    ledger = tf.Ledger(tmp_path / "led.jsonl", caps={"total": 0.05, "c1": 0.05})
    routes = {"default": {"mode": "old-model"}, "moved": {"mode": "passthrough"}}
    srv = proxy.make_server(0, routes, tmp_path / "calls.jsonl", tf.Client("c1", ledger=ledger, key="k", log=lambda m: None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        _, moved = post(base, {"model": LIGHT, "messages": [], "seed": 1}, path="/moved/v1/chat/completions")
        _, old = post(base, {"model": "gpt-4.1-mini", "messages": []})
    finally:
        srv.shutdown()
    assert json.loads(moved)["choices"][0]["message"]["content"] == f"served by {LIGHT}" and up.bodies == [
        {"model": LIGHT, "messages": [], "seed": 1}]
    assert json.loads(old)["choices"][0]["message"]["content"] == ""
    recs = calls(tmp_path)
    assert (recs[0]["mode"], recs[0]["moved"]) == ("passthrough", True) and recs[1]["mode"] == "old-model"
    assert recs[1]["requested_model"] == "gpt-4.1-mini" and "moved" not in recs[1]


def test_embeddings_use_their_own_client_with_more_attempts(tmp_path, monkeypatch):
    """A burst of 503s from the one embedding model must not fail a run that chat calls would pass."""
    import io, email.message
    seen = []

    def flaky(req, timeout=None):
        seen.append(req.full_url)
        if len(seen) < 4:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", email.message.Message(), io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps({"data": [{"embedding": [1.0]}], "usage": {"prompt_tokens": 2}}).encode())
    monkeypatch.setattr(tf.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(tf.time, "sleep", lambda s: None)
    ledger = tf.Ledger(tmp_path / "led.jsonl", caps={"total": 0.05, "c1": 0.05})
    chat_client = tf.Client("c1", ledger=ledger, key="k", max_attempts=proxy.UPSTREAM_ATTEMPTS, log=lambda m: None)
    embed_client = tf.Client("c1", ledger=ledger, key="k", max_attempts=proxy.EMBED_ATTEMPTS, log=lambda m: None)
    srv = proxy.make_server(0, {"default": {"mode": "broken"}, "embedding_model": tf.EMBEDDING_MODEL}, tmp_path / "calls.jsonl",
                            chat_client, embed_client=embed_client)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, _ = post(f"http://127.0.0.1:{srv.server_address[1]}", {"model": "e", "input": ["a"]}, path="/v1/embeddings")
    finally:
        srv.shutdown()
    assert status == 200 and len(seen) == 4 and proxy.EMBED_ATTEMPTS > proxy.UPSTREAM_ATTEMPTS
