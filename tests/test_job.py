import hashlib, json, os, subprocess, sys, textwrap
from pathlib import Path

import pytest

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
        job.run_dir(tmp_path, i).mkdir()
        (job.run_dir(tmp_path, i) / "junit.xml").write_text(JUNIT.replace('name="test_ok" file', f'name="test_ok" x="{outcome}" file'))
    runs = job.collect_runs(tmp_path, 3)
    assert len(runs) == 3 and all("tests/llm/test_a.py::test_ok" in r for r in runs[:2])
    assert runs[2] == {"__run__": {"outcome": "infra", "message": "no junit for run 2"}}
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


def test_errors_and_calls_are_split_by_run_listings_and_untraced_calls_are_counted_apart():
    calls = [
        {"site": "a.py:1", "chain": ["a.py:1"], "test": "t", "run": 0, "mode": "forward", "status": 200, "model": "M"},
        {"site": "a.py:1", "chain": ["a.py:1"], "test": "t", "run": 1, "mode": "forward", "status": 402},
        {"site": "a.py:1", "chain": ["a.py:1"], "test": "t", "run": 1, "mode": "forward", "status": 502},
        {"site": "a.py:1", "chain": ["a.py:1"], "test": "t", "mode": "forward", "status": 500, "proxy_error": "x"},
        {"site": "unknown", "chain": [], "test": "t", "run": 0, "mode": "forward", "status": 200,
         "repaired_empty_args": 2},
        {"path": "/v1/models", "method": "GET", "status": 200},
    ]
    s = job.summarize_calls(calls)
    a = s["sites"]["a.py:1"]
    assert a["calls_by_run"] == {"0": 1, "1": 2, "?": 1}
    assert a["errors_by_run"] == {"1": {"402": 1, "502": 1}, "?": {"500": 1}} and a["errors"] == {"402": 1, "502": 1, "500": 1}
    assert s["models_listings"] == 1 and s["untraced"] == {"forward": 1} and s["sites"]["unknown"]["repaired_empty_args"] == 2
    assert "" not in s["tests"] and "/v1/models" not in json.dumps(s["sites"])


def test_hash_check_catches_added_test_files_and_pytest_config(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("a")
    hashes = {"tests/test_x.py": hashlib.sha256(b"a").hexdigest()}
    assert job.check_hashes(tmp_path, hashes) == []
    (tmp_path / "tests" / "conftest_extra.py").write_text("")
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --deselect tests/test_x.py\n")
    assert job.check_hashes(tmp_path, hashes) == ["added pytest.ini", "added tests/conftest_extra.py"]


def test_the_key_file_is_read_once_and_deleted(tmp_path, monkeypatch):
    f = tmp_path / "key"
    f.write_text("secret\n")
    monkeypatch.setenv("NEBIUS_API_KEY", "from-env")
    assert job.take_key({"key_file": str(f)}) == "secret" and not f.exists()
    assert job.take_key({}) == "from-env" and "NEBIUS_API_KEY" not in os.environ


ROOT = Path(__file__).resolve().parents[1]


def run_job(tmp_path, test_body, k=2, timeout_s=60, routes=None, extra_spec=None):
    """Run the real in-sandbox job locally: proxy + K parallel pytest runs of a tiny repo that asks the proxy."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "llm.py").write_text(textwrap.dedent("""
        import os, httpx
        def ask():
            r = httpx.post(os.environ["OPENAI_BASE_URL"] + "/chat/completions", json={"model": "m", "messages": []}, timeout=30)
            return r.json()["choices"][0]["message"]["content"]
    """))
    (repo / "tests" / "test_app.py").write_text(textwrap.dedent(test_body))
    key = tmp_path / "key"
    key.write_text("test-key\n")
    spec = {"repo_dir": str(repo), "routes": routes or {"default": {"mode": "broken"}}, "k": k,
            "pytest_args": ["tests", "-q"], "env": {}, "cap_usd": 0.01, "timeout_s": timeout_s, "port": 0,
            "key_file": str(key), **(extra_spec or {})}
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        spec["port"] = sock.getsockname()[1]
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    env = {k_: v for k_, v in os.environ.items() if k_ != "NEBIUS_API_KEY"}
    env.update(MMD_OUT=str(tmp_path / "out"), NEBIUS_API_KEY="must-not-reach-the-tests")
    out = subprocess.run([sys.executable, "-m", "model_moving_day.job", "--spec", str(tmp_path / "spec.json")],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert job.MARK in out.stdout, out.stdout[-2000:] + out.stderr[-2000:]
    return json.loads(out.stdout[out.stdout.rindex(job.MARK) + len(job.MARK):].splitlines()[0]), key


def test_the_job_runs_k_tagged_runs_in_parallel_and_the_tests_never_see_a_key(tmp_path):
    summary, key = run_job(tmp_path, """
        import os
        from pkg.llm import ask
        def test_broken_answer_is_empty():
            assert "NEBIUS_API_KEY" not in os.environ and os.environ["MMD_RUN"] in ("0", "1")
            assert ask() == ""
    """)
    assert summary.get("error") is None and summary["exit_codes"] == [0, 0] and summary["parallel"] is True
    assert [list(r.values())[0]["outcome"] for r in summary["runs"]] == ["passed", "passed"]
    site = summary["calls"]["sites"]["pkg/llm.py:4"]
    assert site["calls_by_run"] == {"0": 1, "1": 1} and not key.exists()


def test_a_run_that_outlives_the_timeout_is_killed_and_has_no_junit(tmp_path):
    summary, _ = run_job(tmp_path, """
        import os, time
        def test_slow():
            if os.environ["MMD_RUN"] == "1":
                time.sleep(30)
    """, timeout_s=3)
    assert summary["exit_codes"] == [0, "timeout"]
    assert summary["runs"][1] == {"__run__": {"outcome": "infra", "message": "no junit for run 1"}}


def test_a_job_that_requires_frozen_hashes_refuses_to_run_without_them(tmp_path):
    summary, _ = run_job(tmp_path, "def test_x():\n    pass\n", extra_spec={"require_frozen": True})
    assert "frozen_hashes are required" in summary["error"] and summary["runs"] == []
