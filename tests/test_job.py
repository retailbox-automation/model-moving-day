import json

from model_moving_day import job

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="tests.llm.test_a" name="test_ok" file="tests/llm/test_a.py"/>
<testcase classname="tests.llm.test_a" name="test_bad[gpt-4.1-mini-Mode.TOOLS]">
  <failure message="AssertionError: assert 'jason' == 'Jason'">long traceback</failure></testcase>
<testcase classname="tests.llm.sub.test_b" name="test_err"><error message="ValidationError: 1 validation error">tb</error></testcase>
<testcase classname="tests.llm.test_a" name="test_skip"><skipped message="needs key"/></testcase>
<testcase classname="tests.llm.test_c.TestGroup" name="test_in_class"/>
</testsuite></testsuites>"""


def test_junit_outcomes_and_node_ids(tmp_path):
    p = tmp_path / "j.xml"
    p.write_text(JUNIT)
    res = job.parse_junit(p)
    assert res["tests/llm/test_a.py::test_ok"] == {"outcome": "passed", "message": ""}
    bad = res["tests/llm/test_a.py::test_bad[gpt-4.1-mini-Mode.TOOLS]"]
    assert bad["outcome"] == "failed" and "Jason" in bad["message"]
    assert res["tests/llm/sub/test_b.py::test_err"]["outcome"] == "error"
    assert res["tests/llm/test_a.py::test_skip"]["outcome"] == "skipped"
    assert res["tests/llm/test_c.py::TestGroup::test_in_class"]["outcome"] == "passed"


def test_call_log_summary_per_test_and_site_with_diagnoses():
    calls = [
        {"site": "pkg/a.py:10", "test": "t1", "mode": "forward", "status": 200, "model": "M",
         "usage": {"prompt_tokens": 100, "completion_tokens": 600, "completion_tokens_details": {"reasoning_tokens": 600}},
         "cost": 0.001, "finish": "length", "content_empty": True, "text_in_reasoning": True, "tool_calls": 0, "seconds": 2.0},
        {"site": "pkg/a.py:10", "test": "t1", "mode": "forward", "status": 200, "model": "M",
         "usage": {"prompt_tokens": 100, "completion_tokens": 50}, "cost": 0.0005, "finish": "stop",
         "content_empty": False, "content_lead_ws": True, "tool_calls": 1, "seconds": 1.0},
        {"site": "pkg/b.py:3", "test": "t2", "mode": "broken", "status": 200, "cost": 0.0, "content_empty": True},
        {"site": "unknown", "test": "t2", "path": "/v1/embeddings", "status": 501},
    ]
    s = job.summarize_calls(calls)
    assert s["tests"]["t1"] == ["pkg/a.py:10"] and set(s["tests"]["t2"]) == {"pkg/b.py:3", "unknown"}
    a = s["sites"]["pkg/a.py:10"]
    assert (a["calls"], a["prompt_tokens"], a["completion_tokens"], a["reasoning_tokens"]) == (2, 200, 650, 600)
    assert a["diagnoses"] == {"empty_content_length": 1, "leading_whitespace": 1}
    assert a["finish"] == {"length": 1, "stop": 1}
    assert s["sites"]["unknown"]["errors"] == {"501": 1}
    assert s["cost"] == 0.0015


def test_tests_for_selection_reads_the_k_runs(tmp_path):
    for i, outcome in enumerate(["passed", "failed"]):
        (tmp_path / f"junit-{i}.xml").write_text(JUNIT.replace('name="test_ok" file', f'name="test_ok" x="{outcome}" file'))
    runs = job.collect_runs(tmp_path, 2)
    assert len(runs) == 2 and all("tests/llm/test_a.py::test_ok" in r for r in runs)
    json.dumps(runs)


def test_frozen_hash_check_inside_the_sandbox(tmp_path):
    import hashlib
    (tmp_path / "tests").mkdir()
    f = tmp_path / "tests" / "test_x.py"
    f.write_text("a")
    hashes = {"tests/test_x.py": hashlib.sha256(b"a").hexdigest(), "tests/gone.py": "0" * 64}
    assert job.check_hashes(tmp_path, hashes) == ["missing tests/gone.py"]
    f.write_text("b")
    assert sorted(job.check_hashes(tmp_path, hashes)) == ["changed tests/test_x.py", "missing tests/gone.py"]


def test_test_env_points_sdks_at_the_proxy_and_hides_the_real_key(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "real-key")
    env = job.build_env({"repo_dir": "/work/repo", "env": {"X": "1"}}, 8765, "/mmd")
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8765/v1" and env["OPENAI_API_KEY"] == "mmd-proxy"
    assert "NEBIUS_API_KEY" not in env and env["X"] == "1" and env["MMD_REPO_ROOT"] == "/work/repo"
    assert env["PYTHONPATH"].split(":")[0] == "/mmd/model_moving_day/tracer"


def test_baseline_runs_can_leave_the_sdk_endpoint_alone(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    env = job.build_env({"repo_dir": "/r", "proxy_env": False}, 8765, "/mmd")
    assert "OPENAI_BASE_URL" not in env and "OPENAI_API_BASE" not in env and env["OPENAI_API_KEY"] == "mmd-proxy"
