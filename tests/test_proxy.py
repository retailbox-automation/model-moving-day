import json, subprocess, sys, textwrap, threading, urllib.request

import pytest

from model_moving_day import proxy
from model_moving_day import tf_client as tf

REAL_URLOPEN = urllib.request.urlopen  # the fixture patches the module-level one for tf_client
LIGHT = "nvidia/Nemotron-3_5-Lightning"
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


class FakeUpstream:
    def __init__(self):
        self.bodies = []

    def __call__(self, req, timeout=None):
        import io
        body = json.loads(req.data)
        self.bodies.append(body)
        msg = {"role": "assistant", "content": f"served by {body['model']}"}
        return io.BytesIO(json.dumps({"id": "x", "object": "chat.completion", "model": body["model"],
                                      "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode())


@pytest.fixture
def server(tmp_path, monkeypatch):
    up = FakeUpstream()
    monkeypatch.setattr(tf.urllib.request, "urlopen", up)
    routes = {"default": {"mode": "forward", "model": LIGHT, "reasoning": "off"},
              "sites": {"app/x.py:10": {"mode": "broken"}, "app/y.py:5": {"mode": "forward", "model": NANO}},
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
    assert rec["usage"]["prompt_tokens"] == 10 and rec["cost"] > 0


def test_chain_header_is_resolved_to_an_ast_site_before_routing(server):
    base, up, tmp, _ = server
    post_chain = {"x-mmd-site": "lib/funnel.py:20", "x-mmd-chain": "lib/funnel.py:20|app/y.py:6"}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps({"model": "m", "messages": []}).encode(),
                                 headers={"Content-Type": "application/json", **post_chain}, method="POST")
    REAL_URLOPEN(req, timeout=10).read()
    assert up.bodies[-1]["model"] == NANO  # app/y.py:6 lies inside the span of site app/y.py:5
    assert calls(tmp)[-1]["site"] == "app/y.py:5" and calls(tmp)[-1]["chain"] == ["lib/funnel.py:20", "app/y.py:6"]


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
           "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, str(repo / "tests" / "run.py"), str(repo)], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert seen["x-mmd-site"] == "pkg/llm.py:4" and seen["x-mmd-test"] == "tests/t.py::t (call)"
    assert seen["x-mmd-chain"] == "pkg/llm.py:4"  # repo frames only, innermost first; the test file is excluded
